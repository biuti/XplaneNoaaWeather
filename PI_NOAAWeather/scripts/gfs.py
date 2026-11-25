"""
NOAA weather daemon server

---
X-plane NOAA GFS weather plugin.
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2026 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

from itertools import chain

from . import c, log
from .weathersource import GribWeatherSource


class GFS(GribWeatherSource):
    """NOAA GFS weather source
    Grib parsing is inherited from GribWeatherSource: parse_grib_data(lat, lon) reads the last downloaded file.
    """

    base_url = 'https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.'

    def __init__(self, conf) -> None:
        self.variable_list = conf.gfs_variable_list
        self.download_enabled = conf.download_GFS
        super().__init__(conf)

    @classmethod
    def get_download_url(cls, datecycle: str, cycle: int, forecast: int) -> str:
        """ Returns the GRIB download url add .idx or .grib to the end
            it will use more precise 0.25 grid GFS data instead of 0.50
            The file is about 10mb, still testing"""
        filename = f"gfs.t{cycle:02}z.pgrb2.0p25.f0{forecast:02}"
        # filename = f"gfs.t{cycle:02}z.pgrb2full.0p50.f0{forecast:02}"
        return f"{cls.base_url}{datecycle}/{cycle:02}/atmos/{filename}"

    @classmethod
    def get_cache_filename(cls, datecycle: str, cycle: int, forecast: int) -> str:
        """Returns the proper filename for the cache"""
        return f"{datecycle}_gfs.t{cycle:02}z.pgrb2.0p25.f0{forecast:02}"
        # return f"{datecycle}_gfs.t{cycle:02}z.pgrb2full.0p50.f0{forecast:02}"
