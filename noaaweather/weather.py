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

from . import xp, c, dref, util


class Weather:
    """Sets x-plane weather from GFS parsed data"""

    alt = 0.0
    ref_winds = {}
    lat, lon, last_lat, last_lon = 99, 99, False, False

    def __init__(self, conf):

        self.conf = conf
        self.data = dref.Dref()
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

    def setSnow(self, elapsed):
        """ Set snow cover
            Dref value goes from 1.25 to 0.01
            no snow:    1.25
            light:      0.31
            medium:     0.21
            heavy:      0.07

            GFS SNOD is in meters
            SNOD    Dref conversion:
            0       1.25
            0.1     ~0.3
            0.25    ~0.2
            0.5     ~0.1
            1+      ~0.05
        """
        if 'snow' in self.weatherData['gfs']['surface']:
            snow = self.weatherData['gfs']['surface']['snow']
            if snow > 0 and not c.is_exponential(snow):
                lat = abs(self.data.latdr.value)
                temp = c.kel2cel(self.weatherData['gfs']['surface']['temp'])
                # calculating a factor based on latitude and temperature
                factor = max(-20, lat - 55 - max(0, 0.2 * temp))
                val = max(3.8 * (1 - 0.005 * factor - snow**0.04), 0.05)
                try:
                    rw_val = self.data.snow_cover.value
                    if val < rw_val:
                        speed = 0.25 if self.data.on_ground else 0.01
                        c.snowDatarefTransition(self.data.snow_cover, val, elapsed=elapsed, speed=speed)
                    else:
                        val = self.data.snow_cover.value
                    v = min(5 * max(0, factor)**1.5 * val, 1000)
                    self.setDrefIfDiff(self.data.frozen_water_b, v)
                    # adding tarmac patches
                    w = min(0.6, 1.96*val + 0.1)
                    self.setDrefIfDiff(self.data.tarmac_snow_width, w)
                    self.setDrefIfDiff(self.data.tarmac_snow_noise, w/3)
                    self.setDrefIfDiff(self.data.tarmac_snow_scale, min(500, val*1500))
                    # adding standing water, as probably the tarmac is treated with addictives
                    self.data.puddles.value = 0.4*val + 0.53
                    # adding ice based on temperature and snow (total wild guess)
                    if temp > 4:
                        self.data.iced_tarmac.value = 2
                    else:
                        self.data.iced_tarmac.value = 0.82*val + 0.61 + max(temp*0.05, -0.2)
                except SystemError as e:
                    xp.log(f"ERROR injecting snow_cover: {e}")
            else:
                # default values
                self.data.set_snow_defaults()

    def setDrefIfDiff(self, dref, value, max_diff=False):
        """ Set a Dataref if the current value differs
            Returns True if value was updated """

        if max_diff is not False:
            if abs(dref.value - value) > max_diff:
                dref.value = value
                return True
        else:
            if dref.value != value:
                dref.value = value
                return True
        return False

    def reset_weather(self):
        c.transitionClearReferences()

    def weatherInfo(self, chars: int = 80) -> list[str]:
        """Return an array of strings with formatted weather data"""
        verbose = self.conf.verbose
        sysinfo = [f"XPNoaaWeather for XP12 {self.conf.__VERSION__} Status:"]

        if not self.weatherData:
            sysinfo += ['* Data not ready. Please wait...']
        else:
            wdata = self.weatherData
            if 'info' in wdata:
                sysinfo += [
                    '   LAT: %.2f/%.2f LON: %.2f/%.2f FL: %02.f MAGNETIC DEV: %.2f' % (
                        self.data.latdr.value, wdata['info']['lat'], self.data.londr.value, wdata['info']['lon'],
                        c.m2ft(self.data.altdr.value) / 100, self.data.mag_deviation.value)
                ]
                if not self.data.real_weather_enabled:
                    sysinfo += [f"   XP12 Real Weather is not active (value = {self.data.xp_weather_source.value})"]
                elif 'None' in wdata['info']['gfs_cycle']:
                    sysinfo += ['   XP12 is still downloading weather info ...']
                elif self.conf.use_real_weather_data:
                    sysinfo += [f"   GFS Cycle: {wdata['info']['rw_gfs_cycle']}"]
                else:
                    sysinfo += [f"   GFS Cycle: {wdata['info']['gfs_cycle']}"]

            if 'metar' in wdata and 'icao' in wdata['metar']:
                sysinfo += [
                    '',
                    f"{self.conf.metar_source} METAR:"
                ]
                # Split metar if needed
                metar = f"{wdata['metar']['icao']} {wdata['metar']['metar']}"
                sysinfo += util.format_text(metar, chars, 3)

                if self.conf.metar_decode:
                    # METAR Decoding Section
                    sysinfo += [
                        f"   Apt altitude: {int(c.m2ft(wdata['metar']['elevation']))}ft, "
                        f"Apt distance: {round(wdata['metar']['distance'] / 1000, 1)}km",
                        f"   Temp: {round(wdata['metar']['temperature'][0])}, "
                        f"Dewpoint: {round(wdata['metar']['temperature'][1])}, "
                        f"Visibility: {round(wdata['metar']['visibility'])}m, "
                        f"Press: {wdata['metar']['pressure']:.2f} inhg ({c.inHg2mb(wdata['metar']['pressure']):.1f} mb)"
                    ]

                    wind = f"   Wind:  {wdata['metar']['wind'][0]} {wdata['metar']['wind'][1]}kt"
                    if wdata['metar']['wind'][2]:
                        wind += f", gust {wdata['metar']['wind'][2]}kt"
                    if 'variable_wind' in wdata['metar'] and wdata['metar']['variable_wind']:
                        wind += f" Variable: {wdata['metar']['variable_wind'][0]}-{wdata['metar']['variable_wind'][1]}"
                    sysinfo += [wind]

                    if 'precipitation' in wdata['metar'] and len(wdata['metar']['precipitation']):
                        precip = ''
                        for type in wdata['metar']['precipitation']:
                            if wdata['metar']['precipitation'][type]['recent']:
                                precip += wdata['metar']['precipitation'][type]['recent']
                            precip += f"{wdata['metar']['precipitation'][type]['int']}{type} "
                        sysinfo += [f"   Precipitation: {precip}"]

                    if 'clouds' in wdata['metar']:
                        if len(wdata['metar']['clouds']):
                            clouds = '   Clouds: BASE|COVER    '
                            for cloud in wdata['metar']['clouds']:
                                alt, coverage, type = cloud
                                clouds += f"{c.m2fl(alt):03}|{coverage}{type} "
                        else:
                            clouds = '   Clouds and Visibility OK'
                        sysinfo += [clouds]

                if 'rwmetar' in wdata and self.conf.use_real_weather_data:
                    if not wdata['rwmetar'].get('file_time'):
                        sysinfo += ['XP12 REAL WEATHER METAR:', '   no METAR file, still downloading...']
                    else:
                        sysinfo += [f"XP12 REAL WEATHER METAR ({wdata['rwmetar']['file_time']}):"]
                        line = f"{wdata['rwmetar']['result'][0]} {wdata['rwmetar']['result'][1]}"
                        sysinfo += util.format_text(line, chars, 3)
                    # check actual pressure and adjusted friction
                    sysinfo += ['', 'XP12 REAL WEATHER LIVE PARAMETERS:']
                    pressure = self.data.pressure.value / 100  # mb
                    pressure_inHg = c.mb2inHg(pressure)
                    line = f"   Pressure: {pressure:.1f}mb ({pressure_inHg:.2f}inHg)"
                    vis_m, vis_sm = round(c.sm2m(self.data.visibility.value)), round(self.data.visibility.value, 1)
                    line += f" | Visibility: {vis_m}m ({vis_sm}sm)"
                    friction = self.data.runwayFriction.get()
                    # metar_friction = self.friction
                    line += f" | Runway Friction: {friction:02}"
                    # if friction != metar_friction:
                    #     line += f" (original {metar_friction:02})"
                    sysinfo += [line, '']

            if not self.conf.meets_wgrib2_requirements:
                '''not a compatible OS with wgrib2'''
                sysinfo += ['',
                            '*** *** WGRIB2 decoder not available for your OS version *** ***',
                            'Windows 7 or above, MacOS 10.14 or above, Linux kernel 4.0 or above.',
                            ''
                            ]
            elif 'gfs' not in wdata:
                sysinfo += ['',
                            '*** An error has occurred ***',
                            'No GFS data is available, check log',
                            ''
                            ]
            else:
                if not wdata['gfs']:
                    pass
                else:
                    # GFS data download for testing is enabled
                    sysinfo += [
                        '*** *** Experimental GFS 0.25 degrees weather data download *** ***'
                    ]
                    gfs = wdata['gfs']
                    if 'surface' in gfs and len(gfs['surface']):
                        s = gfs['surface']
                        snow_depth = 'na' if (s.get('snow') is None or s.get('snow') < 0) else round(s.get('snow'), 2)
                        acc_precip = 'na' if (s.get('acc_precip') is None or s.get('acc_precip') < 0) else round(s.get('acc_precip'), 2)
                        surface_temp = s.get('temp')
                        sysinfo += [
                            f"surface temp: {round(c.kel2cel(surface_temp), 1)} | Snow depth (m): {snow_depth}  |  Accumulated precip. (kg/sqm): {acc_precip}",
                            ''
                        ]

                if 'rw' in wdata and self.conf.use_real_weather_data:
                    # XP12 Real Weather is enabled
                    rw = wdata['rw']
                    if 'winds' in rw:
                        sysinfo += ['XP12 REAL WEATHER WIND LAYERS: FL | HDG KT | TEMP | DEV']
                        wlayers = ''
                        out = []
                        for i, layer in enumerate(rw['winds'], 1):
                            alt, hdg, speed, extra = layer
                            wind = f"{hdg:03.0f} {speed:>3.0f}kt"
                            temp = round(c.kel2cel(extra['temp']))
                            dev = round(c.kel2cel(extra['dev']))
                            wlayers += f"    F{c.m2fl(alt):03} | {wind} | {temp:> 3} | {dev:> 3}"
                            if i % 3 == 0 or i == len(rw['winds']):
                                out.append(wlayers)
                                wlayers = ''
                        sysinfo += out

                    if 'tropo' in rw and rw['tropo'].values():
                        alt, temp, dev = rw['tropo'].values()
                        if alt and temp and dev:
                            sysinfo += [f"TROPO LIMIT: {round(alt)}m (F{c.m2fl(alt):03}) | "
                                        f"temp {round(c.kel2cel(temp))}C ISA Dev {round(c.kel2cel(dev))}C"]

                    if 'clouds' in rw:
                        sysinfo += ['XP12 REAL WEATHER CLOUD LAYERS  FLBASE | FLTOP | COVER']
                        clayers = ''
                        clouds = [el for el in rw['clouds'] if el[0] > 0]
                        out = []
                        if not len(clouds):
                            sysinfo += ['    None reported']
                        else:
                            for i, layer in enumerate(clouds, 1):
                                base, top, cover = layer
                                clayers += f"    {c.m2fl(base):03} | {c.m2fl(top):03} | {cover:.0f}%"
                                if i % 3 == 0 or i == len(clouds):
                                    out.append(clayers)
                                    clayers = ''
                            sysinfo += out

                    if 'turbulence' in rw:
                        wafs = rw['turbulence']
                        tblayers = ''
                        out = []
                        cycle = 'not ready yet' if 'None' in wdata['info']['rw_wafs_cycle'] else wdata['info']['rw_wafs_cycle']
                        sysinfo += [f"XP12 REAL WEATHER TURBULENCE ({wdata['info']['rw_wafs_cycle']}):  "
                                    f"FL | SEV (val*10, max {self.conf.max_turbulence * 10}) "]
                        for i, layer in enumerate(wafs, 1):
                            fl = c.m2fl(layer[0])
                            value = f"{round(layer[1] * 10, 1):.1f}" if layer[1] < self.conf.max_turbulence else '*'
                            tblayers += f"    F{fl:03} | {value:3}"
                            if i % 7 == 0 or i == len(wafs):
                                out.append(tblayers)
                                tblayers = ''
                        sysinfo += out
                    if self.conf.download_WAFS and 'wafs' in wdata and 'turbulence' in wdata['wafs']:
                        wafs = wdata['wafs']['turbulence']
                        tblayers = ''
                        out = []
                        sysinfo += [f"NOAA Downloaded WAFS data ({wdata['info']['wafs_cycle']}):  "
                                    f"FL | SEV (val*10, max {self.conf.max_turbulence * 10}) "]
                        for i, layer in enumerate(wafs, 1):
                            fl = c.m2fl(layer[0])
                            value = f"{round(layer[1] * 10, 1):.1f}" if layer[1] < self.conf.max_turbulence else '*'
                            tblayers += f"    F{fl:03} | {value:3}"
                            if i % 7 == 0 or i == len(wafs):
                                out.append(tblayers)
                                tblayers = ''
                        sysinfo += out
                    sysinfo += ['']

                else:
                    '''Normal GFS mode'''
                    pass
                    # if 'winds' in gfs:
                    #     sysinfo += ['', f"GFS WIND LAYERS: {len(gfs['winds'])} FL|HDG|KT|TEMP|DEV"]
                    #     wlayers = ''
                    #     i = 0
                    #     for layer in gfs['winds']:
                    #         i += 1
                    #         alt, hdg, speed, extra = layer
                    #         wlayers += f"   F{c.m2fl(alt):03}|{hdg:03}|{speed:02}kt|" \
                    #                    f"{round(c.kel2cel(extra['temp'])):02}|{round(c.kel2cel(extra['dev'])):02}"
                    #         if i > 3:
                    #             i = 0
                    #             sysinfo += [wlayers]
                    #             wlayers = ''

                    # if 'clouds' in gfs:
                    #     clouds = 'GFS CLOUDS  FLBASE|FLTOP|COVER'
                    #     for layer in gfs['clouds']:
                    #         base, top, cover = layer
                    #         if base > 0:
                    #             clouds += f"    {c.m2fl(base):03} | {c.m2fl(top):03} | {cover}%"
                    #     sysinfo += [clouds]

                    # if 'tropo' in gfs:
                    #     alt, temp, dev = gfs['tropo'].values()
                    #     if alt and temp and dev:
                    #         sysinfo += [f"TROPO LIMIT: {round(alt)}m "
                    #                     f"temp {round(c.kel2cel(temp)):02}C ISA Dev {round(c.kel2cel(dev)):02}C"]

                    # if 'wafs' in wdata:
                    #     tblayers = ''
                    #     for layer in wdata['wafs']:
                    #         tblayers += f"   {c.m2fl(layer[0]):03}|{round(layer[1], 2)}" \
                    #                     f"{'*' if layer[1]>=self.conf.max_turbulence else ''}"

                    #     sysinfo += [f"WAFS TURBULENCE ({len(wdata['wafs'])}): FL|SEV (max {self.conf.max_turbulence}) ",
                    #                 tblayers]

                    # sysinfo += ['']
                    # if 'thermals' in wdata:
                    #     t = wdata['thermals']

                    #     if not t['grad']:
                    #         s = "THERMALS: N/A"
                    #     else:
                    #         if t['grad'] == "TS":
                    #             s = "THERMALS (TS mode): "
                    #         else:
                    #             s = f"THERMALS: grad. {round(t['grad'], 2)} ºC/100m, "
                    #         s += f"h {round(t['alt'])}m, p {round(t['prob']*100)}%, r {round(t['rate']*0.00508)}m/s"
                    #     sysinfo += [s]

                    # if 'cloud_info' in wdata and self.conf.opt_clouds_update:
                    #     ci = wdata['cloud_info']
                    #     s = 'OPTIMISED FOR BEST PERFORMANCE' if ci['OVC'] and ci['above_clouds'] else 'MERGED'
                    #     sysinfo += [f"CLOUD LAYERS MODE: {s}"]
                    #     if verbose:
                    #         sysinfo += [f"{ci['layers']}"]

        return sysinfo

    def dumpLog(self) -> Path:
        """Dumps all the information to a file to report bugs"""
        import platform
        from pprint import pprint

        dumpath = Path(self.conf.cachepath, 'dumplogs')
        dumpath.mkdir(parents=True, exist_ok=True)

        dumplog = Path(dumpath, datetime.utcnow().strftime('%Y%m%d_%H%M%SZdump.txt'))
        xp.log(f"creating dumplog file: {dumplog}")

        f = open(dumplog, 'w')

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
