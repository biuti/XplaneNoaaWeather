"""
NOAA weather daemon server

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

from .weathersource import GribWeatherSource
from .c import c


class GFS(GribWeatherSource):
    """NOAA GFS weather source"""

    base_url = 'https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.'

    download = False
    download_wait = 0

    def __init__(self, conf):
        self.variable_list = conf.gfs_variable_list
        super(GFS, self).__init__(conf)

    @classmethod
    def get_download_url(cls, datecycle, cycle, forecast):
        """Returns the GRIB download url add .idx or .grib to the end"""
        filename = f"gfs.t{cycle:02}z.pgrb2full.0p50.f0{forecast:02}"
        url = f"{cls.base_url}{datecycle}/{cycle:02}/atmos/{filename}"

        return url

    @classmethod
    def get_cache_filename(cls, datecycle, cycle, forecast):
        """Returns the proper filename for the cache"""
        return f"{datecycle}_gfs.t{cycle:02}z.pgrb2full.0p50.f0{forecast:02}"

    def parse_grib_data(self, filepath, lat, lon):
        """Executes wgrib2 and parses its output"""

        args = ['-s',
                '-lon',
                f"{lon}",
                f"{lat}",
                filepath
                ]

        kwargs = {'stdout': subprocess.PIPE}

        if self.conf.spinfo:
            kwargs.update({'startupinfo': self.conf.spinfo, 'shell': True})

        # print("Calling subprocess with {}, {}".format([self.conf.wgrib2bin] + args, kwargs))
        p = subprocess.Popen([self.conf.wgrib2bin] + args, **kwargs)
        # print("result of grib data subprocess is p={}".format(p))
        it = iter(p.stdout)
        data = {}
        clouds = {}
        pressure = False
        tropo = {}
        surface = {}

        for line in it:
            r = line.decode('utf-8')[:-1].split(':')
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
                    # wind levels
                    data.setdefault(level[0], {})
                    data[level[0]][variable] = value
                elif level[0] == 'mean':
                    if variable == 'PRMSL':
                        pressure = c.pa2inhg(float(value))
            elif level[0] == 'tropopause':
                tropo[variable] = value
            elif level[0] == 'surface':
                surface[variable] = value

        windlevels = []
        cloudlevels = []
        templevels = []

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

        windlevels.sort()
        cloudlevels.sort()
        templevels.sort()

        # tropo
        if all(k in tropo.keys() for k in ('PRES', 'TMP')):
            alt = int(c.mb2alt(float(tropo['PRES'])*0.01))
            temp = float(tropo['TMP'])
            dev = c.isaDev(alt, temp)
            tropo ={'alt': float(alt), 'temp': temp, 'dev': dev}
        else:
            tropo = {}

        # surface
        if all(k in surface.keys() for k in ('PRES', 'TMP', 'HGT')):
            alt = float(surface['HGT'])
            temp = float(surface['TMP'])
            press = float(surface['PRES'])*0.01
            surface = {'alt': alt, 'temp': temp, 'press': press}
        else:
            surface = {}

        data = {
            'winds': windlevels,
            'clouds': cloudlevels,
            'temperature': templevels,
            'pressure': pressure,
            'tropo': tropo,
            'surface': surface
        }

        return data
