"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2021-2023 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import sqlite3
import sys

from contextlib import contextmanager
from pathlib import Path


class Database:
    """Wrapper Class for SQLite database connection"""

    def __init__(self, dbfile: Path = None):
        self.conn = None
        self.cursor = None

        if dbfile:
            self.open(dbfile)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def commit(self):
        self.conn.commit()

    def open(self, dbfile: Path):

        try:
            self.conn = sqlite3.connect(dbfile, check_same_thread=False)  # if it does not exist, file will be created
            self.create_database()
            self.cursor = self.conn.cursor()
        except sqlite3.Error as e:
            print(f"SQLite Error connecting to {dbfile.name}: {e}")

    def close(self):

        if self.conn:
            self.conn.commit()
            self.cursor.close()
            self.conn.close()

    def create_database(self):
        queries = [
            ''' CREATE TABLE IF NOT EXISTS source 
                (icao text KEY UNIQUE, lat real, lon real, elevation int, timestamp int KEY, metar text);''',
            ''' CREATE TABLE IF NOT EXISTS realweather 
                            (icao text KEY UNIQUE, metar text);'''
        ]

        with self.session() as db:
            for query in queries:
                db.execute(query)

    def get(self, table: str, icao: str) -> tuple:

        query = '''SELECT * FROM {} WHERE icao = ?'''.format(table)
        with self.session() as db:
            res = db.execute(query, (icao,))
            met = res.fetchone() or (icao, 'not found')
        return met

    def get_all(self, table: str) -> list:

        query = '''SELECT * FROM {} WHERE metar NOT NULL'''.format(table)

        with self.session() as db:
            res = db.execute(query)
            met = res.fetchall()
            return met

    def to_file(self, file: Path, table: str, batch: int = 100):
        query = '''SELECT icao, metar FROM {} WHERE metar NOT NULL'''.format(table)
        lines = 0
        try:
            f = open(file, 'w')
            with self.session() as db:
                res = db.execute(query)
                while True:
                    rows = res.fetchmany(batch)
                    if not rows:
                        break
                    lines += len(rows)
                    for row in rows:
                        f.write(f"{row[0]} {row[1]}\n")
            f.close()
        except (OSError, IOError):
            print(f"ERROR updating METAR.rwx file: {sys.exc_info()[0]}, {sys.exc_info()[1]}")
        return lines

    def query(self, sql):
        with self.session() as db:
            res = db.execute(sql).rowcount
            return res

    def writemany(self, query: str, rows: list, batch: int = 100):
        with self.session() as db:
            return db.executemany(query, rows).rowcount

    @contextmanager
    def session(self):
        """Provide a transactional scope around a series of operations."""
        session = self.conn
        # print(f'with session id: {id(session)}')
        try:
            yield session
            session.commit()

        except sqlite3.IntegrityError as e:
            print(f"Integrity Error: {e}")
            session.rollback()
            # raise
        except Exception as e:
            print(f"Exception Error: {e}")
            session.rollback()
            # raise
        except sqlite3.OperationalError as e:
            print(f"Operational Error: {e}")
            session.rollback()
            # raise
        finally:
            # session.expunge_all()
            # session.close()
            pass

