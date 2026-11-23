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

GFS_GRID_M = 28_000         # GFS 0.25 degree grid is ~27.75 km
SNOW_LOOKAHEAD_M = 60_000   # how far ahead along the track we look for a valid snow value


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

    def check_snow_values(self, lat: float, lon: float, hdg: float, data: dict) -> None:
        """Predicts snow cover when GFS has no valid value at the current position.

        Looks for a valid value on a ring around the position, then ahead along
        the present track. The result is stored in data['surface']['prediction'].
        """
        surface = data['surface']
        if not c.is_exponential(surface.get('snow')):
            return

        files = self.grib_files
        if not files:
            return

        # we have no info about snow, probably over or near water: probe around and ahead
        probes = chain(
            ((bearing, GFS_GRID_M) for bearing in range(0, 360, 45)),
            ((hdg, d) for d in range(GFS_GRID_M, SNOW_LOOKAHEAD_M + 1, GFS_GRID_M)),
        )
        for bearing, dist in probes:
            new_lon, new_lat = c.great_circle_destination(lon, lat, bearing, dist)
            val = self.parse_grib_data(new_lat, new_lon, files)['surface'].get('snow')
            if val is not None and not c.is_exponential(val):
                prediction = {'lat': new_lat, 'lon': new_lon, 'depth': val}
                surface['prediction'] = prediction
                log.debug(f"check_snow_values: prediction: {prediction}")
                return
