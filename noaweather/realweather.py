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

from pathlib import Path
from datetime import datetime
from .weathersource import GribWeatherSource
from .c import c


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

        super(RealWeather, self).__init__(conf)

    @property
    def grib_files(self):
        if self.base is not None:
            return [Path(self.conf.wpath, self.base + el + '.grib') for el in self.suffixes]

    def get_real_weather_metar(self, icao):
        """ Reads METAR files in XP12 real weather folder
            icao: ICAO code for requested airport"""

        response = {
            'file_time': None,
            'reports': []
        }

        '''get METAR files'''
        metar_files = [p for p in Path(self.conf.wpath).iterdir() if p.is_file() and 'METAR' in p.stem]
        if metar_files:
            '''get latest file'''
            file = max([f for f in metar_files], key=lambda item: item.stat().st_ctime)
            response['file_time'] = file.stem[11:-5]
            '''get ICAO metar'''
            with open(file, encoding='utf-8', errors='replace') as f:  # deal with non utf-8 characters, avoiding error
                response['reports'] = (list(set(line for line in f if line.startswith(icao)))
                                       or [f"{icao} not found in XP12 real weather METAR files"])

        return response

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

    def parse_grib_data(self, lat, lon):
        """Executes wgrib2 and parses its output"""

        kwargs = {'stdout': subprocess.PIPE}

        if self.conf.spinfo:
            kwargs.update({'startupinfo': self.conf.spinfo, 'shell': True})

        it = []

        for file in [el for el in self.grib_files if el.is_file()]:
            args = [
                '-s',
                '-lon',
                '%f' % (lon),
                '%f' % (lat),
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
                top, bottom = float(level['top']), float(level['bottom'])
                # print "XPGFS: top: %.0fmbar %.0fm, bottom: %.0fmbar %.0fm %d%%" % (top * 0.01, c.mb2alt(top * 0.01), bottom * 0.01, c.mb2alt(bottom * 0.01), cover)
                cover = float(level.get(next((k for k in level.keys() if k in ('LCDC', 'MCDC', 'HCDC')), None)) or 0)

                if cover:
                    cloudlevels.append([c.mb2alt(bottom * 0.01) * 0.3048, c.mb2alt(top * 0.01) * 0.3048, cover])

        # convert turbulence
        for lvl, val in turb.items():
            alt = int(c.mb2alt(float(lvl)))
            turblevels.append([alt, float(val) * 8])

        windlevels.sort()
        cloudlevels.sort()
        templevels.sort()
        turblevels.sort()

        # tropo
        if all(k in tropo.keys() for k in ('PRES', 'TMP')):
            alt = int(c.mb2alt(float(tropo['PRES'])*0.01))
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
