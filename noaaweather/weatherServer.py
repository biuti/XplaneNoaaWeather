#!/usr/bin/env python3
"""
NOAA weather daemon server

---
X-plane NOAA GFS weather plugin.
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2023 Antonio Golfari
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
import pickle as cPickle
import socketserver as SocketServer

from pathlib import Path
from datetime import datetime

try:
    from . import Conf
except ImportError:
    __package__ = 'noaaweather'
    this_dir = Path(__file__).resolve().parent
    sys.path.append(str(this_dir.parent))
    from .conf import Conf

from . import c
from .metar import Metar
from .realweather import RealWeather
from .gfs import GFS
from .wafs import WAFS
from .weathersource import Worker


class LogFile:
    """File object wrapper, adds timestamp to print output"""

    def __init__(self, file: Path, options):
        self.f = open(file, options)

    def write(self, data):
        if len(data) > 1:
            self.f.write(f"{datetime.now().strftime('%b %d %H:%M:%S')}  {data}")
        else:
            self.f.write(data)

    def __getattr__(self, name):
        return getattr(self.f, name)

    def __setattr__(self, name, value):
        if name != 'f':
            setattr(self.f, name, value)
        else:
            self.__dict__[name] = value


class ClientHandler(SocketServer.BaseRequestHandler):

    @staticmethod
    def get_weather_data(data) -> dict | bool:
        """Collects weather data for the response"""

        lat, lon = float(data[0]), float(data[1])

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
        if conf.meets_wgrib2_requirements and conf.use_real_weather_data:
            rw.get_real_weather_forecast()
            if all(el.is_file() for el in rw.grib_files):
                response['rw'] = rw.parse_grib_data(lat, lon)
                response['info']['rw_gfs_cycle'] = f"{rw.gfs_run}: {rw.gfs_fcst}" if rw.gfs_run else 'na'
                response['info']['rw_wafs_cycle'] = f"{rw.wafs_run}: {rw.wafs_fcst}" if rw.wafs_run else 'na'
                # response['wafs'] = response['rw']['turbulence']
                if conf.download_GFS and gfs.last_grib:
                    filepath = Path(gfs.cache_path, gfs.last_grib)
                    response['gfs'] = gfs.parse_grib_data(filepath, lat, lon)
                # print(f"Grib File: {gfs.last_grib}, data: {response['gfs']}")
                if conf.download_WAFS and rw.wafs_download_needed and wafs.last_grib:
                    # TURB data is not up-to-date, download GRIB file needed
                    print(f"TURB data is not up-to-date, download GRIB file needed ...")
                    wafs_file = Path(wafs.cache_path, wafs.last_grib)
                    if wafs_file.is_file():
                        # resp = rw.update_wafs_files(Path(wafs.cache_path, wafs.last_grib))
                        # if resp is True:
                        #     rw.starting = False
                        print(f"Turbulence updated from WFS data: {wafs_file.name}")
                        response['wafs'] = wafs.parse_grib_data(wafs_file, lat, lon)
                        print(f"response['wafs]: {response['wafs']}")
                        response['info']['wafs_cycle'] = f"{wafs.wafs_run}: {wafs.wafs_fcst}" if wafs.wafs_run else 'na'

        # Parse metar
        apt = metar.get_closest_station(lat, lon)
        if apt and len(apt) > 4:
            response['metar'] = metar.parse_metar(apt[0], apt[5], apt[3])
            response['metar']['latlon'] = (apt[1], apt[2])
            response['metar']['distance'] = c.greatCircleDistance((lat, lon), (apt[1], apt[2]))
            response['rwmetar'] = dict(zip(('file_time', 'result'), [rw.metar_file_time, rw.get_rwmetar(apt[0])]))
        return response

    def shutdown(self):
        # shutdown Needs to be from called from a different thread
        def shut_down_now(srv):
            srv.shutdown()

        th = threading.Thread(target=shut_down_now, args=(self.server,))
        th.start()

    def handle(self):
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
                        # response['metar'] = metar.parse_metar(apt[0], apt[5], apt[3])
                        response['metar'] = dict(zip(('icao', 'metar'), [apt[0], apt[5]]))
                    else:
                        response['metar'] = {
                            'icao': 'METAR STATION',
                            'metar': 'NOT AVAILABLE'
                        }
                    apt = rw.get_rwmetar(data[1:])
                    # print(f" ** weatherServer | RWMETAR: {apt}")
                    if apt and apt[1]:
                        response['rwmetar'] = dict(zip(('icao', 'metar'), [apt[0], apt[1]]))
                        # response['rwmetar'] = dict(zip(('icao', 'metar'), rw.get_real_weather_metar(data[1:])))
                        # print(f"METAR TEST: {xp.getMETARForAirport(data[1:])}")
                        # xp.log(f"METAR TEST: {xp.getMETARForAirport(data[1:])}")
                    else:
                        response['rwmetar'] = {
                            'icao': 'METAR STATION',
                            'metar': 'NOT AVAILABLE'
                        }
            elif data == '!shutdown':
                conf.serverSave()
                self.shutdown()
                response = '!bye'
            elif data == '!reload':
                conf.serverSave()
                conf.pluginLoad()
            elif data == '!resetMetar':
                # Clear database and force redownload
                metar.clear_reports(conf.dbfile)
                metar.last_timestamp = 0
                metar.next_metarRWX = time.time() + 10
            elif data == '!resetRWMetar':
                # reload database
                rw.next_rwmetar = time.time() + 5
                metar.next_metarRWX = time.time() + 5
            elif data == '!ping':
                response = '!pong'
            else:
                return

        socket = self.request[1]
        nbytes = 0

        if response:
            response = cPickle.dumps(response)
            socket.sendto(response + b"\n", self.client_address)
            nbytes = sys.getsizeof(response)

        print(f"{self.client_address[0]}:{data}: {nbytes} bytes sent.")


if __name__ == "__main__":
    debug = False
    # Get the X-Plane path from the arguments
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        # Debug run
        debug = True
        path = False

    conf = Conf(path)

    if not debug:
        logfile = LogFile(Path(conf.respath, 'weatherServerLog.txt'), 'w')
        sys.stderr = logfile
        sys.stdout = logfile

    print(f"NOAA plugin version: {conf.__VERSION__}")
    print('---------------')
    print('Starting server')
    print('---------------')
    print(sys.argv)

    try:
        server = SocketServer.UDPServer(("localhost", conf.server_port), ClientHandler)
    except socket.error:
        print(f"Can't bind address: {'localhost'}, port: {conf.server_port}.")

        if conf.weatherServerPid is not False:
            print(f"Killing old server with pid {conf.weatherServerPid}")
            os.kill(conf.weatherServerPid, signal.SIGTERM)
            time.sleep(2)
            conf.serverLoad()
            server = SocketServer.UDPServer(("localhost", conf.server_port), ClientHandler)

    # Save pid
    conf.weatherServerPid = os.getpid()
    conf.serverSave()

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
    worker = Worker(workers, conf.parserate)
    worker.start()

    if not conf.meets_wgrib2_requirements:
        print('*** OS does not meet minimum requirements. GRIB data download disabled ***')
    print('Server started.')

    # Server loop
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

    # Close gfs worker and save config
    worker.shutdown()
    conf.serverSave()
    sys.stdout.flush()

    print('Server stopped.')

    if not debug:
        logfile.close()

    server.shutdown()
