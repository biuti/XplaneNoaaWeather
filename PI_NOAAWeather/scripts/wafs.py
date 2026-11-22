"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2026 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

from .weathersource import GribWeatherSource


class WAFS(GribWeatherSource):
    """World Area Forecast System - Upper Air Forecast weather source

    Grib parsing is inherited from GribWeatherSource: parse_grib_data(lat, lon) reads the last downloaded file.

    https://aviationweather.gov/turbulence/help?page=plot

    All graphics display atmospheric turbulence intensity as energy (or eddy) dissipation rate to the
    1/3 power, i.e. EDR =e1/3 where e is the eddy dissipation rate in units of m2/s3). Typically EDR
    varies from close to 0, "smooth", to near 1, "extreme for most aircraft types. The display colors
    of EDR range from white near 0 to violet near 1.
    """

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

    grib_conf_var = 'lastwafsgrib'

    def __init__(self, conf) -> None:
        self.variable_list = conf.wafs_variable_list
        self.download_enabled = conf.download_WAFS
        self.download_needed = False
        super().__init__(conf)

    @classmethod
    def get_download_url(cls, datecycle: str, cycle: int, forecast: int) -> str:
        filename = f"gfs.t{cycle:02}z.awf_0p25.f0{forecast:02}.grib2"
        return f"{cls.base_url}{datecycle}/{cycle:02}/atmos/{filename}"

    @classmethod
    def get_cache_filename(cls, datecycle: str, cycle: int, forecast: int) -> str:
        return f"{datecycle}{cycle:02}_gfs.t{cycle:02}z.wafs_0p25_unblended.f{forecast:02}.grib2"
