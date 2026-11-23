#!/usr/bin/env python3
"""
NOAA weather daemon server

---
X-plane NOAA GFS weather plugin.
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2026 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import os
import sys
import signal
import socket
import time
import threading
import json
import subprocess
import socketserver as SocketServer

from pathlib import Path
from datetime import datetime
from contextlib import contextmanager

if not __package__:
    package_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(package_root))
    __package__ = 'scripts'

from . import Conf, c, log
from .metar import Metar
from .realweather import RealWeather
from .gfs import GFS
from .wafs import WAFS
from .weathersource import Worker


@contextmanager
def guarded(section: str):
    """A failing data source degrades to an empty section instead of killing the whole reply"""
    try:
        yield
    except Exception:
        log.exception(f"{section} failed:")


def ask_to_quit(port: int) -> None:
    """Asks a weather server left over from a previous run, still listening on our port, to stop"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(b'!shutdown', ('127.0.0.1', port))


def is_weather_server(pid: int) -> bool:
    """True if pid is a weatherserver.py process: the saved pid can be stale and pids get reused"""
    if os.name == 'nt':
        return False
    try:
        out = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return False
    return 'weatherserver.py' in out


def open_server(conf):
    """UDP server on our port. If an old server still holds the port it is asked to quit,
    and only if it does not answer (and really is one of ours) it is killed"""
    address = ("localhost", conf.server_port)
    try:
        return SocketServer.UDPServer(address, ClientHandler)
    except socket.error:
        log.exception(f"Can't bind address: localhost, port: {conf.server_port}.")

    def retry(seconds: float):
        deadline = time.time() + seconds
        while time.time() < deadline:
            time.sleep(0.5)
            try:
                srv = SocketServer.UDPServer(address, ClientHandler)
                conf.load_server_settings()  # the old server saved its state while quitting
                return srv
            except socket.error:
                pass

    ask_to_quit(conf.server_port)
    if (server := retry(5)) is None:
        pid = conf.weather_server_pid
        if pid and is_weather_server(pid):
            log.debug(f"Old server does not answer, killing pid {pid}")
            os.kill(pid, signal.SIGTERM)
            server = retry(3)
        elif pid:
            log.debug(f"pid {pid} is not a weather server, leaving it alone")
    return server


def exit_with_parent(srv) -> None:
    """The plugin keeps our stdin pipe open: it closes when X-Plane exits or crashes"""
    try:
        sys.stdin.buffer.read()
    except Exception:
        pass
    log.debug("Parent process is gone, shutting down")
    srv.shutdown()


class LogFile:
    """File object wrapper, adds timestamp to print output"""

    def __init__(self, file: Path, options) -> None:
        self.f = open(file, options)

    def write(self, data):
        if len(data) > 1:
            self.f.write(f"{datetime.now().strftime('%b %d %H:%M:%S')}  {data}")
        else:
            self.f.write(data)

    def __getattr__(self, name: str):
        return getattr(self.f, name)

    def __setattr__(self, name: str, value) -> None:
        if name != 'f':
            setattr(self.f, name, value)
        else:
            self.__dict__[name] = value


class ClientHandler(SocketServer.BaseRequestHandler):

    @staticmethod
    def get_weather_data(data) -> dict | bool:
        """Collects weather data for the response"""

        lat, lon, hdg, gs = float(data[0]), float(data[1]), float(data[2]), float(data[3])

        if lat > 98 and lon > 98:
            return False

        response = {
            'rw': {},
            'gfs': {},
            'wafs': {},
            'metar': {},
            'rwmetar': {},
            'info': {
                'lat': lat,
                'lon': lon,
                'gfs_cycle': 'na',
                'wafs_cycle': 'na',
                'rw_gfs_cycle': 'na',
                'rw_wafs_cycle': 'na',
            }
        }

        # Parse gfs and wafs
        with guarded('rw'):
            if conf.meets_wgrib2_requirements and conf.use_real_weather_data:
                rw.get_real_weather_forecast()
                if rw.grib_files:
                    response['rw'] = rw.parse_grib_data(lat, lon)
                    response['info']['rw_gfs_cycle'] = f"{rw.gfs_run}: {rw.gfs_fcst}" if rw.gfs_run else 'na'
                    response['info']['rw_wafs_cycle'] = f"{rw.wafs_run}: {rw.wafs_fcst}" if rw.wafs_run else 'na'
                    if conf.set_snow:
                        # we could need to check for nearby snow depth values
                        rw.check_snow_values(lat, lon, hdg, response['rw'])
                    if conf.download_GFS and gfs.last_grib:
                        filepath = Path(gfs.cache_path, gfs.last_grib)
                        response['gfs'] = gfs.parse_grib_data(filepath, lat, lon)
                    if conf.download_WAFS and rw.wafs_download_needed and wafs.last_grib:
                        # TURB data is not up-to-date, download GRIB file needed
                        log.debug("TURB data is not up-to-date, download GRIB file needed ...")
                        wafs_file = Path(wafs.cache_path, wafs.last_grib)
                        if wafs_file.is_file():
                            log.info(f"Turbulence updated from WFS data: {wafs_file.name}")
                            response['wafs'] = wafs.parse_grib_data(wafs_file, lat, lon)
                            log.debug(f"response['wafs']: {response['wafs']}")
                            response['info']['wafs_cycle'] = f"{wafs.wafs_run}: {wafs.wafs_fcst}" if wafs.wafs_run else 'na'

        # Parse metar
        with guarded('metar'):
            apt = metar.get_closest_station(lat, lon)
            if apt and len(apt) > 4:
                response['metar'] = metar.parse_metar(apt[0], apt[5], apt[3])
                response['metar']['latlon'] = (apt[1], apt[2])
                response['metar']['distance'] = c.greatCircleDistance((lat, lon), (apt[1], apt[2]))
                response['rwmetar'] = dict(zip(('file_time', 'result'), [rw.metar_file_time, rw.get_rwmetar(apt[0])]))

        return response

    def shutdown(self) -> None:
        # shutdown Needs to be from called from a different thread
        def shut_down_now(srv):
            srv.shutdown()

        th = threading.Thread(target=shut_down_now, args=(self.server,))
        th.start()

    def handle(self) -> None:
        response = False
        data = self.request[0].decode('utf-8').strip("\n\r\t")

        if len(data) > 1:
            if data[0] == '?':
                # weather data request
                sdata = data[1:].split('|')
                if len(sdata) > 1:
                    response = self.get_weather_data(sdata)
                elif len(data) == 5:
                    # Icao
                    response = {}
                    apt = metar.get_metar(data[1:])
                    if apt and len(apt) > 2 and apt[5]:
                        response['metar'] = dict(zip(('icao', 'metar'), [apt[0], apt[5]]))
                    else:
                        response['metar'] = {
                            'icao': 'METAR STATION',
                            'metar': 'NOT AVAILABLE'
                        }
                    apt = rw.get_rwmetar(data[1:])
                    if apt and apt[1]:
                        response['rwmetar'] = dict(zip(('icao', 'metar'), [apt[0], apt[1]]))
                    else:
                        response['rwmetar'] = {
                            'icao': 'METAR STATION',
                            'metar': 'NOT AVAILABLE'
                        }
            elif data == '!shutdown':
                conf.save_server_settings()
                self.shutdown()
                response = '!bye'
            elif data == '!reload':
                conf.save_server_settings()
                conf.load_plugin_settings()
            elif data == '!resetMetar':
                # Clear database and force redownload
                metar.clear_reports(conf.db_file)
                metar.last_timestamp = 0
            elif data == '!resetRWMetar':
                # reload database
                rw.next_rwmetar = time.time() + 5
            elif data == '!ping':
                response = '!pong'
            else:
                return

        socket = self.request[1]
        nbytes = 0

        if response:
            # JSON, not pickle: the plugin must never unpickle what arrives on a UDP port
            response = json.dumps(response, separators=(',', ':'), default=str).encode('utf-8')
            try:
                socket.sendto(response + b"\n", self.client_address)
                nbytes = len(response)
            except OSError:
                log.exception(f"can't send {len(response)} bytes to {self.client_address}")

        log.debug(f"{self.client_address[0]}:{data}: {nbytes} bytes sent.")


if __name__ == "__main__":
    # Get the X-Plane path from the arguments
    path = sys.argv[1]
    conf = Conf(path)

    logfile = LogFile(conf.server_log_file, 'w')
    sys.stderr = logfile
    sys.stdout = logfile

    log.info(f"NOAA plugin version: {conf.__VERSION__}")
    log.info('-----------------------')
    log.info('Starting Weather Server')
    log.info('-----------------------')
    log.debug(sys.argv)
    log.debug(f"__file__: {__file__}")
    log.debug(f"__package__: {__package__}")
    log.debug(f"sys.path[0]: {sys.path[0]}")
    log.debug(f"package root: {Path(__file__).resolve().parent.parent}")

    server = open_server(conf)
    if server is None:
        log.critical("Can't start the weather server: the port is busy")
        sys.exit(1)

    parent_watch = None

    if '--parent-pipe' in sys.argv:
        parent_watch = threading.Thread(
            target=exit_with_parent,
            args=(server,),
            name='parent-watch',
            daemon=True
        )
        parent_watch.start()

    # Save pid
    conf.weather_server_pid = os.getpid()
    conf.save_server_settings()

    # Weather classes
    workers = []
    metar = Metar(conf)
    workers.append(metar)
    if conf.meets_wgrib2_requirements:
        rw = RealWeather(conf)
        workers.append(rw)
        if conf.download_GFS:
            gfs = GFS(conf)
            workers.append(gfs)
        if conf.download_WAFS:
            wafs = WAFS(conf)
            workers.append(wafs)

    # Init worker thread
    worker = Worker(workers, conf.parse_rate)
    worker.start()

    if not conf.meets_wgrib2_requirements:
        log.info('*** OS does not meet minimum requirements. GRIB data download disabled ***')
    log.info('Weather server started.')

    # Server loop
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

    # Wait for the parent watcher to finish before interpreter shutdown.
    if parent_watch:
        parent_watch.join()

    # Close gfs worker and save config
    worker.shutdown()
    conf.save_server_settings()

    log.info('Weather server stopped.')


    sys.stdout.flush()
    logfile.close()

    server.server_close()
