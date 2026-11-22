"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2021-2026 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import sqlite3
import threading
import traceback

from contextlib import contextmanager
from pathlib import Path


class Database:
    """Wrapper Class for SQLite database connection
    One connection per thread. A connection shared by two threads shares its transaction as well: a commit()
    or rollback() in one thread would commit or discard the pending writes of the other.
    """

    def __init__(self, db_file: Path | None = None) -> None:
        self._db_file = None
        self._local = threading.local()
        self._connections = []
        self._lock = threading.Lock()

        if db_file:
            self.open(db_file)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    @property
    def conn(self) -> sqlite3.Connection | None:
        """Connection of the calling thread, opened on first use"""
        if self._db_file is None:
            return None
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            # every connection is used by its own thread only: check_same_thread=False just lets close() reach them all
            conn = sqlite3.connect(self._db_file, timeout=10, check_same_thread=False)
            try:
                conn.execute('PRAGMA journal_mode=WAL')  # readers do not block the writer
                conn.execute('PRAGMA synchronous=NORMAL')
            except sqlite3.Error as e:
                print(f"SQLite: can't set WAL mode: {e}")
            self._local.conn = conn
            with self._lock:
                self._connections.append(conn)
        return conn

    def commit(self):
        conn = getattr(self._local, 'conn', None)
        if conn:
            conn.commit()

    def open(self, db_file: Path) -> None:
        self._db_file = db_file
        try:
            self.create_database()
        except sqlite3.Error as e:
            print(f"SQLite Error connecting to {Path(db_file).name}: {e}")

    def close(self) -> None:
        with self._lock:
            connections, self._connections = self._connections, []
        for conn in connections:
            try:
                conn.commit()
                conn.close()
            except sqlite3.Error as e:
                print(f"SQLite Error closing connection: {e}")
        self._local = threading.local()

    def create_database(self) -> None:
        queries = [
            ''' CREATE TABLE IF NOT EXISTS source 
                (icao text KEY UNIQUE, lat real, lon real, elevation int, timestamp int KEY, metar text);''',
            ''' CREATE TABLE IF NOT EXISTS realweather 
                            (icao text KEY UNIQUE, metar text);'''
        ]

        with self.session() as db:
            for query in queries:
                db.execute(query)

    def get(self, table: str, icao: str) -> tuple[str, str]:

        query = '''SELECT * FROM {} WHERE icao = ?'''.format(table)
        met = (icao, 'not found')
        with self.session() as db:
            res = db.execute(query, (icao,))
            met = res.fetchone() or met
        return met

    def get_all(self, table: str) -> list:

        query = '''SELECT * FROM {} WHERE metar NOT NULL'''.format(table)
        met = []
        with self.session() as db:
            res = db.execute(query)
            met = res.fetchall()
        return met

    def query(self, sql: str) -> int:
        res = 0
        with self.session() as db:
            res = db.execute(sql).rowcount
        return res

    def writemany(self, query: str, rows: list, batch: int = 100) -> int:
        count = 0
        with self.session() as db:
            count = db.executemany(query, rows).rowcount
        return count

    @contextmanager
    def session(self):
        """Provide a transactional scope around a series of operations (SQLite errors are logged and rolled back)"""

        try:
            conn = self.conn
        except sqlite3.Error as e:
            print(f"SQLite Error connecting to {self._db_file}: {e}")
            conn = None

        fallback = conn is None
        if fallback:
            # database not available: every query fails with a logged sqlite3 error
            conn = sqlite3.connect(':memory:')
        try:
            yield conn
            conn.commit()
        except sqlite3.Error:
            traceback.print_exc()
            conn.rollback()
        finally:
            if fallback:
                conn.close()
