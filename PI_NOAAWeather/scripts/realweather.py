"""
X-Plane 12 Real Weather daemon server

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

import time
import traceback

from collections.abc import Iterator, Sequence
from pathlib import Path
from datetime import datetime, timedelta, timezone

from . import c, utils
from .database import Database
from .weathersource import GribWeatherSource


GFS_GRID_M = 28_000         # GFS 0.25 degree grid is ~27.75 km
SNOW_LOOKAHEAD_M = 60_000   # how far ahead along the track we look for a valid snow value


class RealWeather(GribWeatherSource):
    """X-Plane 12 Real Weather files source"""

    TIMEFRAMES = range(0, 24, 3)

    SUFFIXES = [
        'calt',
        'cbbs',
        'cbcv',
        'cbtp',
        'ccov',
        'ctrb',
        'dewp',
        'prcp',
        'pres',
        'snod',
        'srfc',
        'svis',
        'temp',
        'trop',
        'turb',
        'wind',
    ]

    DB_TABLE = 'realweather'
    RWMETAR_CHECK_INTERVAL = 300  # 5 minutes

    def __init__(self, conf) -> None:
        self.starting = True
        self.zulu_time = None
        self.idx_behind = None
        self.idx_ahead = None
        self.latest_wafs_checked = None
        self.next_rwmetar = time.time() + 30
        self.last_rwmetar = None

        self.db = Database(conf.db_file)

        super().__init__(conf)

    @property
    def base_behind(self) -> str | None:
        if self.idx_behind is not None and self.zulu_time:
            return f'{self.zulu_time.year}-{self.zulu_time.month:02d}-{self.zulu_time.day:02d}-{self.TIMEFRAMES[self.idx_behind]:02d}.00'

    @property
    def base_ahead(self) -> str | None:
        if self.idx_ahead is not None and self.zulu_time:
            day = self.zulu_time if self.idx_ahead > 0 else self.zulu_time + timedelta(days=1)
            return f'{day.year}-{day.month:02d}-{day.day:02d}-{self.TIMEFRAMES[self.idx_ahead]:02d}.00'

    @property
    def base(self) -> str | None:
        if self.zulu_time and self.idx_behind is not None and self.idx_ahead is not None:
            time_behind = self.TIMEFRAMES[self.idx_behind]
            time_ahead = self.TIMEFRAMES[self.idx_ahead] if self.idx_ahead > 0 else 24
            if self.zulu_time.hour - time_behind < time_ahead - self.zulu_time.hour:
                return self.base_behind
            else:
                return self.base_ahead

    @property
    def grib_files(self) -> list:
        return [] if self.base is None else [path for path in self.conf.rw_path.resolve().glob(f"*{self.base}*.grib") if path.is_file()]

    @property
    def metar_file(self) -> Path | None:
        """ Should get the latest RW METAR file
            Unfortunately when RW initialize does not save files in a smart order, so we need to use filename"""

        if not self.conf.rw_path.is_dir():
            return None

        metar_files = [p for p in self.conf.rw_path.iterdir() if p.is_file() and 'METAR' in p.stem.upper()]

        if not metar_files:
            return None

        # get latest file
        return max(metar_files, key=lambda x: utils.date_in_filename(x))

    @property
    def metar_file_time(self) -> str:
        if self.metar_file:
            return f"{self.metar_file.stem[11:-6]} {self.metar_file.stem[-5:]}Z"
        else:
            return ''

    @property
    def time_to_update_rwmetar(self) -> bool:
        return (self.metar_file is not None
                and (not self.last_rwmetar or self.last_rwmetar < self.metar_file.stat().st_ctime))

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

        lines = utils.get_rw_ordered_lines(self.metar_file)

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

    def get_rwmetar(self, icao: str) -> tuple[str, str]:
        """ Reads METAR DB created from files in XP12 real weather folder
            icao: ICAO code for requested airport
            returns METAR string"""

        return self.db.get(self.DB_TABLE, icao)

    def get_real_weather_forecast(self) -> None:
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
        self.idx_ahead = next((k for k, v in enumerate(self.TIMEFRAMES) if v > now.hour), 0)
        self.idx_behind = self.idx_ahead - 1 if self.idx_ahead > 0 else len(self.TIMEFRAMES) - 1

    @staticmethod
    def _snow_candidates(hdg: float) -> Iterator[tuple[float, int]]:
        """(bearing, distance in m) points to probe for a valid snow value: first the 8 compass
        points one GFS grid step away, then ahead along the present track"""
        yield from ((bearing, GFS_GRID_M) for bearing in range(0, 360, 45))
        yield from ((hdg, d) for d in range(GFS_GRID_M, SNOW_LOOKAHEAD_M + 1, GFS_GRID_M))

    def find_snow(self, lat: float, lon: float, hdg: float, files: Sequence[Path]) -> dict | None:
        """Looks for a valid snow value around the position and ahead along the track.

        files: grib files holding the snow data (read once per probed point)
        Returns {'lat', 'lon', 'depth'} of the first valid point found, else None.
        """
        for bearing, dist in self._snow_candidates(hdg):
            new_lon, new_lat = c.great_circle_destination(lon, lat, bearing, dist)
            val = self.parse_grib_data(new_lat, new_lon, files)['surface'].get('snow')
            if val is not None and not c.is_exponential(val):
                return {'lat': new_lat, 'lon': new_lon, 'depth': val}
        return None

    def check_snow_values(self, lat: float, lon: float, hdg: float, data: dict) -> None:
        """Predicts snow cover when GFS has no valid value at the current position.

        Looks for a valid value on a ring around the position, then ahead along
        the present track. The result is stored in data['surface']['prediction'].
        """

        surface = data.get('surface', {})
        if not surface:
            return

        snow = surface.get('snow')
        if not c.is_exponential(snow):
            return

        grib_file = next((f for f in self.grib_files if 'snod' in f.name and f.is_file()), None)
        if not grib_file:
            return

        # no info about snow here, probably over or near water: probe around and ahead
        if prediction := self.find_snow(lat, lon, hdg, [grib_file]):
            surface['prediction'] = prediction
            print(f"check_snow_values: prediction: {prediction}")

    def check_latest_wafs(self) -> str | None:
        """reads the run data from latest WAFS grib file """

        file = next((f for f in self.conf.rw_path.iterdir() if 'ctrb' in f.name and self.base_ahead in f.name), None)
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
            files = [f for f in self.conf.rw_path.iterdir() if 'ctrb' in f.name and (self.base_behind in f.name or self.base_ahead in f.name)]
        else:
            files = [f for f in self.conf.rw_path.iterdir() if 'ctrb' in f.name and self.base_ahead in f.name]
        for file in files:
            print(f"copying {grib_file.name} over {file.name}")
            utils.copy(grib_file, file)
        return len(files) > 1

    def run(self, elapsed) -> None:
        """ Updates METAR database from XP12 realweather metar files if option to do so is checked"""

        if self.time_to_update_rwmetar:
            # update real weather metar database
            try:
                print("Updating Real Weather DB ...")
                self.update_rwmetar()
                print(f"*** RW METAR DB updated: {datetime.now(timezone.utc).strftime('%H:%M:%S')} ***")
                self.last_rwmetar = time.time()
                self.next_rwmetar = self.last_rwmetar + self.RWMETAR_CHECK_INTERVAL
            except Exception:
                traceback.print_exc()

    def shutdown(self) -> None:
        super().shutdown()
        self.db.commit()
        self.db.close()
