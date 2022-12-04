"""
X-Plane 12 Real Weather daemon server

---
X-plane NOAA GFS weather plugin.
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2022 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import subprocess
import time

from pathlib import Path
from datetime import datetime
from .database import Database
from .weathersource import GribWeatherSource
from .c import c
from .util import util


class RealWeather(GribWeatherSource):
    """X-Plane 12 Real Weather files source"""

    forecasts = range(0, 24, 3)
    levels = [
        '900',
        '800',
        '700',
        '600',
        '500',
        '400',
        '300',
        '250',
        '200'
    ]

    suffixes = [
        'calt',
        'ccov',
        'wind',
        'temp',
        'ctrb',
        'dewp',
        'pres',
        'svis',
        'srfc'
    ]

    table = 'realweather'

    def __init__(self, conf):
        self.zulu_time = None
        self.base = None
        self.cycle = None
        self.fcst = None
        self.next_rwmetar = time.time() + 30
        self.last_rwmetar = None

        self.db = Database(conf.dbfile)

        super(RealWeather, self).__init__(conf)

    @property
    def grib_files(self) -> list:
        if self.base is not None:
            return [Path(self.conf.wpath, self.base + el + '.grib') for el in self.suffixes]

    @property
    def metar_file(self):
        metar_files = [p for p in self.conf.wpath.iterdir() if p.is_file() and 'METAR' in p.stem.upper()]
        if metar_files:
            '''get latest file'''
            return max([f for f in metar_files], key=lambda item: item.stat().st_ctime)
        else:
            return None

    @property
    def time_to_update_rwmetar(self):
        return (self.metar_file is not None
                and (not self.last_rwmetar
                     or self.last_rwmetar < self.metar_file.stat().st_ctime
                     or self.next_rwmetar < time.time()))

    def update_rwmetar(self, batch: int = 100) -> tuple:
        """Updates metar table from Metar file"""
        nupdated = 0
        nparsed = 0
        inserts = []

        lines = util.get_rw_ordered_lines(self.metar_file)

        if lines:
            seen = set()
            query = """
                        INSERT OR REPLACE INTO realweather 
                            (icao, metar)
                        VALUES
                            (?, ?)
                    """

            for i, line in enumerate(lines, 1):
                if not line[0:4] in seen:
                    icao, metar = line[0:4], line[5:-1].split(',')[0]
                    seen.add(icao)
                    inserts.append((icao, metar))
                if len(inserts) > batch or i >= len(lines):
                    nparsed += len(inserts)
                    nupdated += self.db.writemany(query, inserts)
                    inserts = []

        return nparsed, nupdated

    @staticmethod
    def get_real_weather_metar(db, icao: str) -> tuple:
        """ Reads METAR DB created from files in XP12 real weather folder
            icao: ICAO code for requested airport
            returns METAR string"""

        return db.get(RealWeather.table, icao)

    def get_real_weather_metars(self, icao) -> dict:
        """ Reads METAR DB created from files in XP12 real weather folder
            icao: ICAO code for requested airport
            returns a dict with time of last METAR update and a LIST of METARs for given ICAO"""

        response = {
            'file_time': None,
            'reports': []
        }

        '''get METAR files'''
        if self.metar_file:
            '''get latest file'''
            response['file_time'] = f"{self.metar_file.stem[11:-6]} {self.metar_file.stem[-5:]}Z"
            '''get ICAO metar'''
            response['reports'] = ([line for line in util.get_rw_ordered_lines(self.metar_file)
                                    if line.startswith(icao)]
                                   or [f"{icao} not found in XP12 real weather METAR files"])

        return response

    def update_metar_rwx_file(self):
        """Dumps all metar data from XP12 METAR files to the METAR.rwx file"""
        print(f"updating METAR.rwx file using XP12 files: RealWeather.update_metar_rwx_file()")
        if not self.metar_file or not self.metar_file.is_file():
            print(f"ERROR updating METAR.rwx file: XP12 did not download files yet")
            return False

        return self.db.to_file(Path(self.conf.syspath, 'METAR.rwx'), self.table)

    def get_real_weather_forecast(self):
        """ configures x-plane 12 weather filenames to be read
            As X-Plane already downloads GFS grib files, there's no need to download them again as in XP11 version
            Filenames:
            calt:       cloud layers altitude
            ccov:       cloud layers coverage
            ctrb:       turbolence, icing severity
            dewp:       relative humidity
            wind:       wind layers
            svis:       surface visibility
            temp:       temperature
            pres:       pressure at sea level
            sfrc:       ground elevation

            RealWeather Download Logic:
            UTC     GFS Cycle   forecast
            00      12 (-1d)    12
            03      18 (-1d)    09
            06      18 (-1d)    12
            09      00          09
            12      00          12
            15      06          09
            18      06          12
            21      12          09
        """

        now = datetime.utcnow()
        time = min(self.forecasts, key=lambda x: abs(x - now.hour))
        self.zulu_time, self.base = time, f'GRIB-{now.year}-{now.month:02d}-{now.day:02d}-{time:02d}.00-ZULU-'

    def parse_grib_data(self, lat, lon) -> dict:
        """Executes wgrib2 and parses its output"""

        kwargs = {'stdout': subprocess.PIPE}

        if self.conf.spinfo:
            kwargs.update({'startupinfo': self.conf.spinfo, 'shell': True})

        it = []

        for file in [el for el in self.grib_files if el.is_file()]:
            args = [
                '-s',
                '-lon',
                f"{lon}",
                f"{lat}",
                file
            ]
            p = subprocess.Popen([self.conf.wgrib2bin] + args, **kwargs)
            it.extend(iter(p.stdout))

        wind = {}
        clouds = {}
        pressure = False
        tropo = {}
        surface = {}
        turb = {}

        # inizialize levels
        for level in self.levels:
            wind.setdefault(level, {})

        for line in it:
            r = line.decode('utf-8')[:-1].split(':')
            if not r[2].split('=')[1] == self.cycle:
                # getting info
                self.cycle = r[2].split('=')[1]
                self.fcst = r[5]
            # Level, variable, value
            level, variable, value = [r[4].split(' '), r[3], r[7].split(',')[2].split('=')[1]]

            if len(level) > 1:
                if level[1] == 'cloud':
                    # cloud layer
                    clouds.setdefault(level[0], {})
                    if len(level) > 3 and variable == 'PRES':
                        clouds[level[0]][level[2]] = value
                    else:
                        # level coverage/temperature
                        clouds[level[0]][variable] = value
                elif level[1] == 'mb':
                    if variable == 'ICESEV':
                        # Icing severity, not used yet
                        pass
                    elif variable == 'EDPARM':
                        # Eddy Dissipation Param
                        turb[level[0]] = value
                    elif variable in ['UGRD', 'VGRD', 'TMP', 'RH']:
                        # wind, temperature and humidity
                        wind[level[0]][variable] = value
                elif level[-1] == 'ground':
                    surface[variable] = value
                elif variable == 'PRMSL':
                    pressure = c.pa2inhg(float(value))
            elif level[0] == 'tropopause':
                tropo[variable] = value
            elif level[0] == 'surface':
                surface[variable] = value

        windlevels = []
        cloudlevels = []
        templevels = []
        turblevels = []

        # Let data ready to push on datarefs.

        # Convert wind levels
        wind_levels = iter(wind.items())
        for level, wind in wind_levels:
            if 'UGRD' in wind and 'VGRD' in wind:
                hdg, vel = c.c2p(float(wind['UGRD']), float(wind['VGRD']))
                alt = int(c.mb2alt(float(level)))

                # Optional varialbes
                temp, dev, rh, dew = False, False, False, False
                # Temperature
                if 'TMP' in wind:
                    temp = float(wind['TMP'])
                    dev = c.isaDev(alt, temp)
                # Relative Humidity
                if 'RH' in wind:
                    rh = float(wind['RH'])

                if temp and rh:
                    dew = c.dewpoint(temp, rh)

                windlevels.append([alt, hdg, c.ms2knots(vel), {'temp': temp,
                                                               'dev': dev,
                                                               'rh': rh,
                                                               'dew': dew,
                                                               'gust': 0}])
                if alt and temp:
                    templevels.append([alt, temp, dev, dew])

                # get tropopause info from F386 wind level if not already available
                if not tropo and float(level) == 200 and temp:
                    tropo = {'PRES': level, 'TMP': wind['TMP']}

        # Convert cloud level
        for level in clouds:
            level = clouds[level]
            if 'top' in level and 'bottom' in level:
                top, bottom = float(level['top']) * 0.01, float(level['bottom']) * 0.01  # mb
                # print "XPGFS: top: %.0fmbar %.0fm, bottom: %.0fmbar %.0fm %d%%" % (top * 0.01, c.mb2alt(top * 0.01), bottom * 0.01, c.mb2alt(bottom * 0.01), cover)
                cover = float(level.get(next((k for k in level.keys() if k in ('LCDC', 'MCDC', 'HCDC')), None)) or 0)

                if cover:
                    # cloudlevels.append([c.mb2alt(bottom * 0.01) * 0.3048, c.mb2alt(top * 0.01) * 0.3048, cover])
                    cloudlevels.append([round(c.mb2alt(bottom)), round(c.mb2alt(top)), cover])

        # convert turbulence
        for lvl, val in turb.items():
            alt = round(c.mb2alt(float(lvl)))
            turblevels.append([alt, float(val) * 8])

        windlevels.sort()
        cloudlevels.sort()
        templevels.sort()
        turblevels.sort()

        # tropo
        if all(k in tropo.keys() for k in ('PRES', 'TMP')):
            alt = round(c.mb2alt(float(tropo['PRES'])*0.01))
            temp = float(tropo['TMP'])
            dev = c.isaDev(alt, temp)
            tropo = {'alt': float(alt), 'temp': temp, 'dev': dev}
        else:
            tropo = {}

        # surface
        default = {'PRES': 'press', 'TMP': 'temp', 'HGT': 'alt', 'SNOD': 'snow', 'APCP': 'apcp'}
        for k, v in [i for i in default.items() if i[0] in surface.keys()]:
            surface[v] = float(surface.pop(k, None)) if k != 'PRES' else float(surface.pop(k)) * 0.01

        data = {
            'winds': windlevels,
            'clouds': cloudlevels,
            'temperature': templevels,
            'turbulence': turblevels,
            'pressure': pressure,
            'tropo': tropo,
            'surface': surface
        }

        return data

    def run(self, elapsed):
        """ Updates METAR.rwx file from XP12 realweather metar files if option to do so is checked"""

        if self.time_to_update_rwmetar:
            # update real weather metar database
            print(f"Updating Real Weather DB ...")
            self.update_rwmetar()
            print(f"*** RW METAR DB updated: {datetime.utcnow().strftime('%H:%M:%S')} ***")
            self.last_rwmetar = time.time()
            self.next_rwmetar = time.time() + 1800  # 30 min.
            if self.conf.updateMetarRWX and self.conf.metar_use_xp12:
                # Update METAR.rwx
                if self.update_metar_rwx_file():
                    print('Updated METAR.rwx file using XP12 Real Weather METAR files.')
                else:
                    # Retry in 30 sec
                    self.next_rwmetar = time.time() + 30

    def shutdown(self):
        super(RealWeather, self).shutdown()
        self.db.commit()
        self.db.close()
