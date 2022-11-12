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
import sys
import time
import sqlite3

from pathlib import Path
from datetime import datetime
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

    def __init__(self, conf):
        self.variable_list = conf.gfs_variable_list
        self.zulu_time = None
        self.base = None
        self.cycle = None
        self.fcst = None
        self.next_rwmetar = time.time() + 30
        self.last_rwmetar = None

        self.cache_path = Path(conf.cachepath, 'metar')
        self.database = Path(self.cache_path, 'rwmetar.db')

        self.th_db = False

        self.connection = self.db_connect(self.database)
        self.cursor = self.connection.cursor()
        self.db_create()

        super(RealWeather, self).__init__(conf)

    @property
    def grib_files(self) -> list:
        if self.base is not None:
            return [Path(self.conf.wpath, self.base + el + '.grib') for el in self.suffixes]

    @property
    def metar_file(self):
        metar_files = [p for p in Path(self.conf.wpath).iterdir() if p.is_file() and 'METAR' in p.stem]
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

    def db_connect(self, path):
        """Returns an SQLite connection to the metar database"""
        return sqlite3.connect(path, check_same_thread=False)

    def db_create(self):
        """Creates the METAR database and tables"""
        # cursor = db.cursor()
        db = self.connection
        # db.execute('''DROP TABLE IF EXISTS airports''')
        db.execute('''CREATE TABLE IF NOT EXISTS airports (icao text KEY UNIQUE, metar text)''')
        print(f"Creating Table airports ...")
        db.commit()

    def update_rwmetar(self, db):
        """Updates metar table from Metar file"""
        # f = open(path, encoding='utf-8', errors='replace')  # deal with non utf-8 characters, avoiding error
        # nupdated = 0
        nparsed = 0
        # cursor = db.cursor()
        # i = 0
        inserts = []
        # INSBUF = cursor.arraysize

        # today_prefix = datetime.utcnow().strftime('%Y%m')
        # yesterday_prefix = (datetime.utcnow() + timedelta(days=-1)).strftime('%Y%m')
        #
        # today = datetime.utcnow().strftime('%d')

        # today, today_prefix, yesterday_prefix = util.date_info()

        # lines = f.readlines()

        # lines = list(set(open(self.metar_file, encoding='utf-8', errors='replace')))
        # lines = [x for x in (set(open(self.metar_file, encoding='utf-8', errors='replace')))
        #          if x[0].isalpha() and len(x) > 11 and x[11] == 'Z']
        # codes = list(set(x[0:4] for x in lines))

        lines = util.get_rw_ordered_lines(self.metar_file)

        if lines:
            # lines.sort(key=lambda x: (x[0:4], -int(x[5:10])))
            seen = set()
            # rows = [x for x in lines if not (x[0:4] in seen or seen.add(x[0:4]))]
            query = """
                        INSERT OR REPLACE INTO airports 
                            (icao, metar)
                        VALUES
                            (?, ?)
                    """
            for line in lines:
                if not line[0:4] in seen:
                    # i += 1
                    icao, metar = line[0:4], line[5:-1].split(',')[0]
                    seen.add(icao)

                    inserts.append((icao, metar))
                    nparsed += 1

            #         if (i % INSBUF) == 0:
            #             cursor.executemany(query, inserts)
            #             inserts = []
            #             nupdated += cursor.rowcount
            #
            # if len(inserts):
            #     cursor.executemany(query, inserts)
            #     nupdated += cursor.rowcount
            # db.commit()
            with db:
                try:
                    db.executemany(query, inserts)
                    db.commit()
                except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
                    print('SQLite Error inserting lines. Could not complete operation:', e)
                    db.rollback()

        return nparsed

    def get_real_weather_metar(self, icao: str) -> tuple:
        """ Reads METAR DB created from files in XP12 real weather folder
            icao: ICAO code for requested airport
            returns METAR string"""

        # cursor = self.cursor if not self.th_db else self.th_db.cursor()
        #
        # res = cursor.execute('''SELECT * FROM airports WHERE icao = ? AND metar NOT NULL LIMIT 1''', (icao.upper(),))
        #
        # ret = res.fetchall()
        # if len(ret) > 0:
        #     return ret[0]
        with self.th_db or self.connection as db:
            icao = icao.upper()
            try:
                res = db.execute('''SELECT * FROM airports WHERE icao = ? AND metar NOT NULL LIMIT 1''', (icao,))
                met = res.fetchone() or (icao, 'not found')
                print(f"Query {icao}: {met}")
                return met
            except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
                print('SQLite Error querying db. Could not complete operation:', e)
                return icao, 'DB ERROR'

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
            response['file_time'] = self.metar_file.stem[11:-5]
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

        with self.th_db or self.connection as db:
            try:
                f = open(Path(self.conf.syspath, 'METAR.rwx'), 'w')
                res = db.execute('SELECT icao, metar FROM airports WHERE metar NOT NULL')
                while True:
                    rows = res.fetchmany(100)
                    if not rows:
                        break
                    for row in rows:
                        f.write(f"{row[0]} {row[1]}\n")
                f.close()
            except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
                print('SQLite Error querying db. Could not complete operation:', e)
                return False
            except (OSError, IOError):
                print(f"ERROR updating METAR.rwx file: {sys.exc_info()[0]}, {sys.exc_info()[1]}")
                return False

        return True

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
        self.zulu_time, self.base = time, f'GRIB-{now.year}-{now.month}-{now.day}-{time}.00-ZULU-'

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
            # print("Calling subprocess with {}, {}".format([self.conf.wgrib2bin] + args, kwargs))
            p = subprocess.Popen([self.conf.wgrib2bin] + args, **kwargs)
            # print("result of grib data subprocess is p={}".format(p))
            it.extend(iter(p.stdout))

        data = {}
        clouds = {}
        pressure = False
        tropo = {}
        surface = {}
        turb = {}

        # inizialize levels
        for level in self.levels:
            data.setdefault(level, {})

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
                        data[level[0]][variable] = value
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
        wind_levels = iter(data.items())
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
        if all(k in surface.keys() for k in ('PRES', 'TMP', 'HGT')):
            alt = float(surface['HGT'])
            temp = float(surface['TMP'])
            press = float(surface['PRES'])*0.01
            hdg, vel = False, False
            if 'UGRD' in surface.keys() and 'VGRD' in surface.keys():
                hdg, vel = c.c2p(float(surface['UGRD']), float(surface['VGRD']))
            surface = {'alt': alt, 'temp': temp, 'press': press, 'hdg': hdg, 'spd': c.ms2knots(vel)}
        else:
            surface = {}

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

        # Worker thread requires its own db connection and cursor
        if not self.th_db:
            self.th_db = self.db_connect(self.database)

        if self.time_to_update_rwmetar:
            # update real weather metar database
            print(f"Updating Real Weather DB ...")
            self.update_rwmetar(self.th_db)
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
