"""
X-Plane 12 Real Weather daemon server

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

import time

from pathlib import Path
from datetime import datetime, timedelta, timezone

from . import c, util
from .database import Database
from .weathersource import GribWeatherSource

# import xp
# xp.log(f"test realweather: {xp.VERSION}")

# try:
#     from . import xp
#     xp.log(f"test realweather: {xp.VERSION}")
# except Exception as e:
#     print(f"** ** error realweather: {e}")
#     pass
# from . import xp_test
# xp_test.xp.log(f"test realweather: {xp_test.xp.VERSION}")


class RealWeather(GribWeatherSource):
    """X-Plane 12 Real Weather files source"""

    timeframes = range(0, 24, 3)

    suffixes = [
        'calt',
        'ccov',
        'wind',
        'temp',
        'ctrb',
        'dewp',
        'pres',
        'svis',
        'srfc',
        'prcp'
    ]

    table = 'realweather'

    def __init__(self, conf):
        self.starting = True
        self.zulu_time = None
        self.idx_behind = None
        self.idx_ahead = None
        self.gfs_run = None
        self.gfs_fcst = None
        self.wafs_run = None
        self.wafs_fcst = None
        self.latest_wafs_checked = None
        self.next_rwmetar = time.time() + 30
        self.last_rwmetar = None

        self.db = Database(conf.dbfile)

        super(RealWeather, self).__init__(conf)

    @property
    def base_behind(self) -> str | None:
        if self.idx_behind is not None and self.zulu_time:
            return f'{self.zulu_time.year}-{self.zulu_time.month:02d}-{self.zulu_time.day:02d}-{self.timeframes[self.idx_behind]:02d}.00'

    @property
    def base_ahead(self) -> str | None:
        if self.idx_ahead is not None and self.zulu_time:
            day = self.zulu_time if self.idx_ahead > 0 else self.zulu_time + timedelta(days=1)
            return f'{day.year}-{day.month:02d}-{day.day:02d}-{self.timeframes[self.idx_ahead]:02d}.00'

    @property
    def base(self) -> str | None:
        if self.zulu_time and self.idx_behind is not None and self.idx_ahead is not None:
            time_behind = self.timeframes[self.idx_behind]
            time_ahead = self.timeframes[self.idx_ahead] if self.idx_ahead > 0 else 24
            if self.zulu_time.hour - time_behind < time_ahead - self.zulu_time.hour:
                return self.base_behind
            else:
                return self.base_ahead

    @property
    def grib_files(self) -> list:
        # print(f"time: {self.zulu_time} | base = {self.base}")
        # from . import xp
        # print(f"test xp: {xp.getMETARForAirport('LIME')}")
        return [] if self.base is None else [path for path in self.conf.wpath.resolve().glob(f"*{self.base}*.grib")]

    @property
    def metar_file(self) -> Path | None:
        metar_files = [p for p in self.conf.wpath.iterdir() if p.is_file() and 'METAR' in p.stem.upper()]
        if metar_files:
            '''get latest file'''
            return max([f for f in metar_files], key=lambda item: item.stat().st_ctime)
        else:
            return None

    @property
    def time_to_update_rwmetar(self) -> bool:
        return (self.metar_file is not None
                and (not self.last_rwmetar
                        or self.last_rwmetar < self.metar_file.stat().st_ctime
                        or self.next_rwmetar < time.time()
                    ))

    @property
    def wafs_download_needed(self) -> bool:
        wafs_run = self.wafs_run if self.latest_wafs_checked == self.idx_ahead else self.check_latest_wafs()
        print(f"idx_ahead: {self.idx_ahead} | latest checked: {self.latest_wafs_checked} | wafs_run: {wafs_run}")
        return self.gfs_run is not None and wafs_run is not None and int(self.gfs_run) - int(wafs_run) > 100

    def update_rwmetar(self, batch: int = 100) -> tuple[int, int]:
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
    def get_real_weather_metar(db, icao: str) -> tuple[str, str]:
        """ Reads METAR DB created from files in XP12 real weather folder
            icao: ICAO code for requested airport
            returns METAR string"""

        # xp.log(xp.getMETARForAirport(icao))
        # print(f"METAR: {xp.getMETARForAirport(icao)}")

        return db.get(RealWeather.table, icao)

    # @staticmethod
    # def get_real_weather_metar(icao: str) -> tuple[str, str]:
    #     """ Reads METAR DB created from files in XP12 real weather folder
    #         icao: ICAO code for requested airport
    #         returns METAR string"""
    #     try:
    #         import XPPython3.xp as xp
    #     except (ImportError, Exception) as e:
    #         print(f" *** Import Error for xp: {e}")
    #         try:
    #             from . import xp
    #         except (ImportError, Exception) as e:
    #             print(f" *** Import Error for xp: {e}")
    #             return (icao, f'{e}')

    #     return (icao, xp.getMETARForAirport(icao))

    def get_real_weather_metars(self, icao: str) -> dict:
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
            00      18 (-1d)    06
            03      18 (-1d)    09
            06      18 (-1d)    12
            09      00          09
            12      00          12
            15      12          03
            18      12          06
            21      12          09
        """

        now = datetime.now(timezone.utc)
        self.zulu_time = now
        self.idx_ahead = next((k for k, v in enumerate(self.timeframes) if v > now.hour), 0)
        self.idx_behind = self.idx_ahead - 1 if self.idx_ahead > 0 else len(self.timeframes) - 1

    def parse_grib_data(self, lat: float, lon: float) -> dict:
        """Executes wgrib2 and parses its output"""

        it = []

        for file in [el for el in self.grib_files if el.is_file()]:
            it.extend(self.read_grib_file(file, lat, lon))

        wind = {}
        clouds = {}
        pressure = False
        tropo = {}
        surface = {}
        turb = {}
        checked = False

        # inizialize levels
        for level in self.levels:
            wind.setdefault(level, {})

        for line in it:
            r = line.split(':')

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
                elif any(el in variable for el in ('ICESEV', 'EDPARM', 'UGRD', 'VGRD', 'TMP', 'RH')):
                    if variable == 'ICESEV':
                        # Icing severity
                        pass
                    elif variable == 'EDPARM':
                        # Eddy Dissipation Param
                        if not checked:
                            # getting cycle info
                            if not r[2].split('=')[1] == self.wafs_run:
                                self.wafs_run = r[2].split('=')[1]
                                self.wafs_fcst = r[5]
                            checked = True
                        turb['1000' if float(level[0]) < 100 else level[0]] = value
                    elif variable in ['UGRD', 'VGRD', 'TMP', 'RH']:
                        # wind, temperature and humidity
                        if not r[2].split('=')[1] == self.gfs_run:
                            # getting cycle info
                            self.gfs_run = r[2].split('=')[1]
                            self.gfs_fcst = r[5]
                        wind['1000' if float(level[0]) < 100 else level[0]][variable] = value
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

                windlevels.append(
                    [
                        alt,
                        hdg,
                        c.ms2knots(vel),
                        {'temp': temp, 'dev': dev, 'rh': rh, 'dew': dew, 'gust': 0}
                    ]
                )
                if alt and temp:
                    templevels.append([alt, temp, dev, dew])

                # get tropopause info from F386 wind level if not already available
                if not tropo and float(level) == 200 and temp:
                    tropo = {'PRES': level, 'TMP': wind['TMP']}

        # Convert cloud level
        for level in clouds.values():
            if all(k in level.keys() for k in ('top', 'bottom')):
                top, bottom = float(level['top']) * 0.01, float(level['bottom']) * 0.01  # mb
                cover = float(level.get(next((k for k in level.keys() if k in ('LCDC', 'MCDC', 'HCDC')), None)) or 0)

                if cover:
                    cloudlevels.append([round(c.mb2alt(bottom)), round(c.mb2alt(top)), cover])

        # convert turbulence
        for lvl, val in turb.items():
            alt = round(c.mb2alt(float(lvl)))
            turblevels.append([alt, float(val)])

        windlevels.sort()
        cloudlevels.sort()
        templevels.sort()
        turblevels.sort()

        # tropo
        if any(k in tropo.keys() for k in ('PRESS', 'HGT')) and 'TMP' in tropo.keys():
            if 'PRES' in tropo.keys():
                alt = round(c.mb2alt(float(tropo['PRES'])*0.01))
            else:
                alt = float(tropo['HGT'])
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

    def check_latest_wafs(self) -> str | None:
        """reads the run data from latest WAFS grib file """

        file = next((f for f in self.conf.wpath.iterdir() if 'ctrb' in f.name and self.base_ahead in f.name), None)
        print(f"check_latest_wafs: {file}")
        if file:
            it = self.read_grib_file(file)
            for line in it:
                try:
                    r = line.split(':')
                    self.latest_wafs_checked = self.idx_ahead
                    print(f"WAFS Checked: {file.name} | latest checked: {self.latest_wafs_checked} | wafs_run: {r[2].split('=')[1]}")
                    return r[2].split('=')[1]
                except Exception as e:
                    continue
        return None

    def update_wafs_files(self, grib_file: Path) -> bool:
        """takes the downloaded WAFS file and copies it to the XP12 Real Weather folder"""
        if self.starting:
            print(f"Need to change both ahead and behind files")
            files = [f for f in self.conf.wpath.iterdir() if 'ctrb' in f.name and (self.base_behind in f.name or self.base_ahead in f.name)]
        else:
            files = [f for f in self.conf.wpath.iterdir() if 'ctrb' in f.name and self.base_ahead in f.name]
        for file in files:
            print(f"copying {grib_file.name} over {file.name}")
            util.copy(grib_file, file)
        return len(files) > 1

    def run(self, elapsed):
        """ Updates METAR.rwx file from XP12 realweather metar files if option to do so is checked"""

        if self.time_to_update_rwmetar:
            # update real weather metar database
            print("Updating Real Weather DB ...")
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
