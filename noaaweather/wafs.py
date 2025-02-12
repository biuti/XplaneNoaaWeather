"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2024 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import re

from datetime import datetime, timedelta

from . import c
from .weathersource import GribWeatherSource


class WAFS(GribWeatherSource):
    """World Area Forecast System - Upper Air Forecast weather source"""

    forecasts = [6, 9, 12, 15, 18, 21, 24]
    base_url = 'https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.'

    levels = [
        '1000',  # ~ FL10
        '950',   # ~ FL100
        '900',   # ~ FL100
        '800',   # ~ FL100
        '700',   # ~ FL100
        '600',   # ~ FL140
        '500',   # ~ FL180
        '400',   # ~ FL240
        '300',   # ~ FL300
        '250',   # ~ FL340
        '200',   # ~ FL390
        '150',   # ~ FL440
    ]

    download_wait = 0
    grib_conf_var = 'lastwafsgrib'

    RE_PRAM = re.compile(r'\bparmcat=(?P<parmcat>[0-9]+) parm=(?P<parm>[0-9]+)')

    def __init__(self, conf):
        self.variable_list = conf.wafs_variable_list
        self.download_enabled = conf.download_WAFS
        self.download_needed = False
        self.wafs_run = None
        self.wafs_fcst = None
        super().__init__(conf)

    @classmethod
    def get_cycle_date(cls) -> tuple[str, int, int]:
        """Returns last cycle date available"""
        now = datetime.utcnow()

        cnow = now - timedelta(**cls.publish_delay)
        # Get last cycle
        for cycle in cls.cycles:
            if cnow.hour >= cycle:
                lcycle = cycle
        # Forecast
        adjs = 0
        if cnow.day != now.day:
            adjs = +24
        # Elapsed from cycle
        forecast = (adjs + now.hour - lcycle)
        # Get current forecast
        for fcast in cls.forecasts:
            if forecast <= fcast:
                forecast = fcast
                break

        return f"{cnow.year}{cnow.month:02}{cnow.day:02}{lcycle:02}", lcycle, forecast

    def parse_grib_data(self, filepath, lat: float, lon: float) -> dict:
        """Executes wgrib2 and parses its output

        https://aviationweather.gov/turbulence/help?page=plot

        All graphics display atmospheric turbulence intensity as energy (or eddy) dissipation rate to the
        1/3 power, i.e. EDR =e1/3 where e is the eddy dissipation rate in units of m2/s3). Typically EDR
        varies from close to 0, "smooth", to near 1, "extreme for most aircraft types. The display colors
        of EDR range from white near 0 to violet near 1.
        """

        it = self.read_grib_file(filepath, lat, lon)

        cat = {}
        for line in it:
            sline = line.split(':')
            if not any(el in sline[4] for el in self.levels):
                continue

            if sline[3] == 'EDPARM':
                # Eddy Dissipation Param
                alt = int(c.mb2alt(float(sline[4][:-3])))
                value = float(sline[7].split(',')[-1:][0][4:-1])
                cat[alt] = value
                if not sline[2].split('=')[1] == self.wafs_run:
                    self.wafs_run = sline[2].split('=')[1]
                    self.wafs_fcst = sline[5]
            elif sline[3] == 'ICESEV':
                # Icing severity
                pass
            elif sline[3] == 'CBHE':
                # Horizontal Extent of Cumulonimbus (CB) %
                pass
            elif sline[3] == 'ICAHT' and 'base' in sline[4]:
                # Cumulonimbus (CB) base height (meters)
                pass
            elif sline[3] == 'ICAHT' and 'top' in sline[4]:
                # Cumulonimbus (CB) top height (meters)
                pass

        turbulence = []
        turb_items = iter(cat.items())

        for key, value in turb_items:
            '''tweaking turbulence intensity using a factor'''
            turbulence.append([key, value])
        turbulence.sort()
        return {'turbulence': turbulence}

    @classmethod
    def get_download_url(cls, datecycle: str, cycle: int, forecast: int) -> str:
        filename = f"gfs.t{datecycle[-2:]}z.awf_0p25.f0{forecast:02}.grib2"
        url = f"{cls.base_url}{datecycle[:-2]}/{datecycle[-2:]}/atmos/{filename}"
        return url

    @classmethod
    def get_cache_filename(cls, datecycle: str, cycle: int, forecast: int) -> str:
        filename = f"{datecycle}_gfs.t{datecycle[-2:]}z.wafs_0p25_unblended.f{forecast:02}.grib2"
        return filename
