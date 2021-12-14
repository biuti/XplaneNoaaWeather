"""
NOAA weather daemon server

---
X-plane NOAA GFS weather plugin.
Copyright (C) 2012-2015 Joan Perez i Cauhe
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import sys
import subprocess

try:
    from weathersource import GribWeatherSource
    from c import c
except ImportError:
    from .weathersource import GribWeatherSource
    from .c import c


class GFS(GribWeatherSource):
    """NOAA GFS weather source"""

    base_url = 'https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.'
    # base_url = 'https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p50.pl'

    download = False
    download_wait = 0

    def __init__(self, conf):
        self.variable_list = conf.gfs_variable_list
        super(GFS, self).__init__(conf)

    @classmethod
    def get_download_url(cls, datecycle, cycle, forecast):
        """Returns the GRIB download url add .idx or .grib to the end"""
        filename = 'gfs.t%02dz.pgrb2full.0p50.f0%02d' % (cycle, forecast)
        url = '%s%s/%02d/atmos/%s' % (cls.base_url, datecycle, cycle, filename)

        return url

    # @classmethod
    # def get_download_url(cls, datecycle, cycle, forecast, variable_list):
    #     """Returns the GRIB download url add .idx or .grib to the end"""
    #     filename = 'gfs.t%02dz.pgrb2full.0p50.f0%02d' % (cycle, forecast)
    #     # url = '%s%s/%02d/atmos/%s' % (cls.base_url, datecycle, cycle, filename)
    #     url = '%s?file=%s' % (cls.base_url, filename)
    #
    #     # get levels and vars
    #     levels = []
    #     vars = []
    #     for el in variable_list:
    #         if isinstance(el['levels'], list):
    #             levels.extend(el['levels'])
    #         else:
    #             levels.append(el['levels'])
    #         if isinstance(el['vars'], list):
    #             vars.extend(el['vars'])
    #         else:
    #             vars.append(el['vars'])
    #     levels = [f"lev_{el.replace(' ', '_')}=on" for el in set(levels)]
    #     vars = [f"var_{el.replace(' ', '_')}=on" for el in set(vars)]
    #
    #     url += '&' + '&'.join(levels+vars)
    #     url += '&leftlon=0&rightlon=360&toplat=90&bottomlat=-90&dir=/gfs.%s/%02d/atmos' % (datecycle, cycle)
    #
    #     return url

    @classmethod
    def get_cache_filename(cls, datecycle, cycle, forecast):
        """Returns the proper filename for the cache"""
        return '%s_gfs.t%02dz.pgrb2full.0p50.f0%02d' % (datecycle, cycle, forecast)

    def parse_grib_data(self, filepath, lat, lon):
        """Executes wgrib2 and parses its output"""
        args = ['-s',
                '-lon',
                '%f' % (lon),
                '%f' % (lat),
                filepath
                ]

        kwargs = {'stdout': subprocess.PIPE}

        if self.conf.spinfo:
            kwargs.update({'startupinfo': self.conf.spinfo, 'shell': True})

        print("Calling subprocess with {}, {}".format([self.conf.wgrib2bin] + args, kwargs))
        p = subprocess.Popen([self.conf.wgrib2bin] + args, **kwargs)
        print("result of grib data subprocess is p={}".format(p))
        it = iter(p.stdout)
        data = {}
        clouds = {}
        pressure = False
        tropo = {}

        for line in it:
            if sys.version_info.major == 2:
                r = line[:-1].split(':')
            else:
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

        windlevels = []
        cloudlevels = []

        # Let data ready to push on datarefs.

        # Convert wind levels
        if sys.version_info.major == 2:
            wind_levels = data.iteritems()
        else:
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

        # tropo
        if all(k in tropo.keys() for k in ('PRES', 'TMP')):
            alt = int(c.mb2alt(float(tropo['PRES'])*0.01))
            temp = float(tropo['TMP'])
            dev = c.isaDev(alt, temp)
            tropo ={'alt': float(alt), 'temp': temp, 'dev': dev}
        else:
            tropo = {}

        data = {
            'winds': windlevels,
            'clouds': cloudlevels,
            'pressure': pressure,
            'tropo': tropo
        }

        return data


def test(file=None):
    from .conf import Conf
    if not file:
        file = 'noaweather/cache/gfs/20211122_gfs.t06z.pgrb2full.0p50.f006'
    # file2 = 'noaweather/cache/gfs/gfs.t06z.pgrb2full.0p50.f003'
    lat = 44.5  # LIMZ
    lon = 7.6  # LIMZ
    conf = Conf(False)
    gfs = GFS(conf=conf)

    args = ['-s',
            '-lon',
            '%f' % (lon),
            '%f' % (lat),
            file
            ]
    kwargs = {'stdout': subprocess.PIPE}

    # subprocess.Popen([gfs.conf.wgrib2bin] + ['-h'])

    # p = subprocess.Popen([gfs.conf.wgrib2bin] + ['-s', '-lon', str(lon), str(lat), file], **kwargs).communicate()
    t = 'https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p50.pl?file=gfs.t06z.pgrb2full.0p50.f006&lev_150_mb=on&lev_200_mb=on&lev_300_mb=on&lev_400_mb=on&lev_500_mb=on&lev_600_mb=on&lev_700_mb=on&lev_850_mb=on&lev_high_cloud_bottom_level=on&lev_high_cloud_layer=on&lev_high_cloud_top_level=on&lev_low_cloud_bottom_level=on&lev_low_cloud_layer=on&lev_low_cloud_top_level=on&lev_mean_sea_level=on&lev_middle_cloud_bottom_level=on&lev_middle_cloud_layer=on&lev_middle_cloud_top_level=on&lev_tropopause=on&var_HCDC=on&var_LCDC=on&var_MCDC=on&var_PRES=on&var_PRMSL=on&var_TCDC=on&var_TMP=on&var_UGRD=on&var_VGRD=on&leftlon=0&rightlon=360&toplat=90&bottomlat=-90&dir=%2Fgfs.20211120%2F06%2Fatmos'
    p = subprocess.Popen([gfs.conf.wgrib2bin] + args, **kwargs)

    it = iter(p.stdout)
    data = []
    for line in it:
        if sys.version_info.major == 2:
            r = line[:-1].split(':')
        else:
            r = line.decode('utf-8')[:-1].split(':')
        # Level, variable, value
        level, variable, value = [r[4].split(' '), r[3], r[7].split(',')[2].split('=')[1]]

        data.append(dict(level=level, variable=variable, value=value))
    return data


def test2(file=None):
    from .conf import Conf
    if not file:
        file = 'noaweather/cache/gfs/20211122_gfs.t06z.pgrb2full.0p50.f006'
    # lat = -22.8  # Rio
    # lon = -43.2  # Rio
    lat = 44.5  # LIMZ
    lon = 7.6  # LIMZ
    conf = Conf(False)
    gfs = GFS(conf=conf)

    return gfs.parse_grib_data(file, lat, lon)
