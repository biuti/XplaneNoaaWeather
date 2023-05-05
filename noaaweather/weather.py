"""
X-plane NOAA GFS weather plugin.

Development version for X-Plane 12
---
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2023 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import os
import pickle
import socket
import threading
import subprocess

from datetime import datetime
from pathlib import Path

# XPPython3 library
import xp

from . import c


class Weather:
    """Sets x-plane weather from GSF parsed data"""

    alt = 0.0
    ref_winds = {}
    lat, lon, last_lat, last_lon = 99, 99, False, False

    def __init__(self, conf, data):

        self.conf = conf
        self.data = data
        self.lastMetarStation = False

        self.friction = 0

        # Data
        self.weatherData = False
        self.weatherClientThread = False

        self.windAlts = -1

        # Response queue for user queries
        self.queryResponses = []

        # Create client socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.die = threading.Event()
        self.lock = threading.Lock()

        self.newData = False

        self.startWeatherServer()

    def startWeatherClient(self):
        if not self.weatherClientThread:
            self.weatherClientThread = threading.Thread(target=self.weatherClient)
            self.weatherClientThread.start()

    def weatherClient(self):
        """Weather client thread fetches weather from Weather Server"""

        # Send something for windows to bind
        self.weatherClientSend('!ping')

        while True:
            received = self.sock.recv(1024 * 8)
            wdata = pickle.loads(received)
            if self.die.is_set() or wdata == '!bye':
                break
            elif 'info' not in wdata:
                # A metar query response
                self.queryResponses.append(wdata)
            else:
                self.weatherData = wdata
                self.newData = True

    def weatherClientSend(self, msg):
        if self.weatherClientThread:
            self.sock.sendto(msg.encode('utf-8'), ('127.0.0.1', self.conf.server_port))

    def startWeatherServer(self):
        DETACHED_PROCESS = 0x00000008
        args = [xp.pythonExecutable, Path(self.conf.respath, 'weatherServer.py'), self.conf.syspath]
        xp.log(f"we are at xp.pythonExecutable ...")

        kwargs = {'close_fds': True}

        try:
            if self.conf.spinfo:
                kwargs.update({'startupinfo': self.conf.spinfo, 'creationflags': DETACHED_PROCESS})
            xp.log(f"Starting Weather Server using: {args} {kwargs}")
            subprocess.Popen(args, **kwargs)
        except Exception as e:
            print(f"Exception while executing subprocess: {e}")

    def shutdown(self):
        # Shutdown client and server
        self.weatherClientSend('!shutdown')
        self.weatherClientThread = False

    def get_XP12_METAR(self, icao: str) -> str:
        return xp.getMETARForAirport('icao')

    # def setSnow(self):
    #     """Set snot cover"""
    #     # Not used at the moment, probably needs a different API if we want to implement
    #     print(f"weatherdata['gfs']: {self.weatherData['gfs']}")
    #     if 'snow' in self.weatherData['gfs']:
    #         snow = self.weatherData['gfs']['snow']
    #         if 0 < snow <= 5:
    #             print(f"activating snow ...")
    #             self.setDrefIfDiff(self.hack_snow, snow, 0.05)
    #             self.setDrefIfDiff(self.hack_control, 1)
    #         else:
    #             print(f"deactivating control ...")
    #             self.setDrefIfDiff(self.hack_control, 0)

    def dumpLog(self) -> Path:
        """Dumps all the information to a file to report bugs"""
        import platform
        from pprint import pprint

        dumpath = Path(self.conf.cachepath, 'dumplogs')
        dumpath.mkdir(parents=True, exist_ok=True)

        dumplog = Path(dumpath, datetime.utcnow().strftime('%Y%m%d_%H%M%SZdump.txt'))
        print(f"dumplog file: {dumplog}")

        f = open(dumplog, 'w')

        # import XPPython

        xpver, sdkver, hid = xp.getVersions()
        output = [
            '--- Platform Info ---\n',
            f"Plugin version: {self.conf.__VERSION__}\n",
            f"Xplane Version: {round(xpver/1000, 3)}, SDK Version: {round(sdkver/100, 2)}\n",
            f"Platform: {platform.platform()}\n",
            f"Python version: {platform.python_version()}\n",
            '\n--- Weather Status ---\n'
        ]

        for line in self.weatherInfo():
            output.append(f"{line}\n")

        output += ['\n--- Weather Data ---\n']

        for line in output:
            f.write(line)

        pprint(self.weatherData, f, width=160)
        f.write('\n--- Transition data Data --- \n')
        pprint(c.transrefs, f, width=160)

        f.write('\n--- Weather Datarefs --- \n')

        # dump datarefs
        pprint(self.data.dump(), f, width=160)

        f.write('\n--- Configuration ---\n')
        vars = {}
        for var in self.conf.__dict__:
            if type(self.conf.__dict__[var]) in (str, int, float, list, tuple, dict):
                vars[var] = self.conf.__dict__[var]
        pprint(vars, f, width=160)

        # Append tail of PythonInterface log files
        logfiles = [
            'PythonInterfaceLog.txt',
            'PythonInterfaceOutput.txt',
            Path('noaaweather', 'weatherServerLog.txt'),
        ]

        for logfile in logfiles:
            try:
                filepath = Path(xp.PLUGINSPATH, logfile)
            except ImportError:
                filepath = Path(self.conf.syspath, 'Resources', 'plugins', 'PythonScripts', logfile)
            if filepath.is_file():

                lfsize = filepath.stat().st_size
                lf = open(filepath, 'r')
                lf.seek(0, os.SEEK_END)
                lf.seek(lf.tell() - c.limit(1024 * 6, lfsize), os.SEEK_SET)
                f.write(f"\n--- {logfile} ---\n\n")
                for line in lf.readlines():
                    f.write(line.strip('\r'))
                lf.close()

        f.close()

        return dumplog
