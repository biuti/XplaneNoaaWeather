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

import io
import threading
import ssl
import zlib
import subprocess
import traceback

from collections import defaultdict
from collections.abc import Iterable, Sequence
from itertools import chain
from urllib.request import Request, urlopen
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryFile
from pathlib import Path
from typing import NamedTuple

from . import c, Conf, utils, log


# Variables that drive turbulence/icing vs. wind/temp/humidity handling
WIND_VARS = frozenset(('UGRD', 'VGRD', 'TMP', 'RH'))

# raw GRIB surface keys -> names used downstream ('PRES' is also converted Pa -> hPa)
SURFACE_RENAME = {'PRES': 'press', 'TMP': 'temp', 'HGT': 'alt', 'SNOD': 'snow', 'APCP': 'apcp'}


class GribRecord(NamedTuple):
    """One wgrib2 inventory line, reduced to the fields we use"""
    run: str                  # cycle, e.g. '2026111412'
    variable: str             # e.g. 'TMP'
    level: tuple[str, ...]    # e.g. ('2', 'm', 'above', 'ground')
    fcst: str                 # forecast descriptor
    value: str

    @classmethod
    def parse(cls, line: str) -> 'GribRecord':
        r = line.split(':')
        return cls(r[2].split('=')[1], r[3], tuple(r[4].split(' ')), r[5], r[7].split(',')[2].split('=')[1])


class WeatherSource:
    """Weather source metaclass"""

    def __init__(self, conf) -> None:
        self.download = False
        self.conf = conf
        self.die = threading.Event()

        self.cache_path = Path(self.conf.cache_path)
        if not self.cache_path.exists():
            self.cache_path.mkdir(parents=True, exist_ok=True)

    def read_grib_file(self, file: Path, lat: float = 46, lon: float = 9) -> list[str]:
        """Executes wgrib2 on given GRIB file and returns its output lines"""

        args = [
            self.conf.wgrib2bin,
            '-s',
            '-lon',
            f"{lon}",
            f"{lat}",
            str(file)
        ]

        kwargs = {
            'stdin': subprocess.DEVNULL,   # don't leak the parent-pipe to wgrib2
            'stderr': subprocess.DEVNULL,  # valid handle, we never read it anyway
            'stdout': subprocess.PIPE,
            'text': True,
        }
        if self.conf.spinfo:  # Windows
            kwargs.update(startupinfo=self.conf.spinfo, creationflags=subprocess.CREATE_NO_WINDOW)

        try:
            res = subprocess.run(args, timeout=30, **kwargs)
        except (OSError, subprocess.SubprocessError):   # includes TimeoutExpired
            log.exception(f"wgrib2 failed on {Path(file).name}")
            return []

        lines = res.stdout.splitlines()
        records = [l for l in lines if l.count(':') >= 7 and 'val=' in l]  # real `-s -lon` records have 8+ fields

        if res.returncode or len(records) != len(lines):
            log.debug(f"wgrib2 rc={res.returncode}, {len(lines) - len(records)} malformed line(s) in {Path(file).name}: {lines[:2]!r}")

        return records

    def shutdown(self) -> None:
        """Stop pending processes"""
        self.die.set()

    def run(self, elapsed: int) -> None:
        """Called by a worker thread"""
        return


class GribWeatherSource(WeatherSource):
    """Grib file weather source"""

    cycles = range(0, 24, 6)
    publish_delay = {'hours': 4, 'minutes': 25}
    variable_list = []
    download_wait = 0
    grib_conf_var = 'lastgrib'

    # forecast hours the source publishes, None = every 3 hours
    forecasts: Sequence[int] | None = None

    # Run / forecast info of the data last read by parse_grib_data()
    gfs_run: str | None = None
    gfs_fcst: str | None = None
    wafs_run: str | None = None
    wafs_fcst: str | None = None

    levels = [
        '1000',  # ~ surface
        '950',   # ~ 1500ft
        '900',   # ~ 3000ft
        '800',   # ~ 6000ft
        '700',   # ~ FL100
        '600',   # ~ FL140
        '500',   # ~ FL180
        '400',   # ~ FL240
        '300',   # ~ FL300
        '250',   # ~ FL340
        '200',   # ~ FL390
        '150',   # ~ FL440
        '100'    # ~ FL520
    ]

    def __init__(self, conf) -> None:
        self.cache_path = Path(conf.cache_path, 'gfs')

        super().__init__(conf)

        if self.last_grib and not Path(self.cache_path, self.last_grib).is_file():
            self.last_grib = False

    @property
    def grib_files(self) -> list[Path]:
        """Grib files to parse: sources that download their data (GFS, WAFS) read the last downloaded
        file, sources fed by another program (RealWeather) override this."""
        file = Path(self.cache_path, self.last_grib) if self.last_grib else None
        return [file] if file and file.is_file() else []

    def __getattr__(self, item: str):
        if item == 'last_grib':
            return getattr(self.conf, self.grib_conf_var)
        return self.__getattribute__(item)

    def __setattr__(self, key: str, value) -> None:
        if key == 'last_grib':
            self.conf.__dict__[self.grib_conf_var] = value
        self.__dict__[key] = value

    @classmethod
    def _elapsed_since_cycle(cls) -> tuple[datetime, int, int]:
        """Returns (date of the last published cycle, its hour, hours elapsed since it started)"""

        now = datetime.now(timezone.utc)
        # a cycle is published after publish_delay (4 hours 25min)
        cnow = now - timedelta(**cls.publish_delay)
        lcycle = max(cycle for cycle in cls.cycles if cycle <= cnow.hour)
        adjs = 24 if cnow.day != now.day else 0

        return cnow, lcycle, adjs + now.hour - lcycle

    @classmethod
    def get_cycle_date(cls) -> tuple[str, int, int]:
        """Returns last cycle date available: (date 'YYYYMMDD', cycle hour, forecast hour)"""

        cnow, lcycle, elapsed = cls._elapsed_since_cycle()
        if cls.forecasts:
            # first published forecast at or after the elapsed hours
            forecast = next((fcast for fcast in cls.forecasts if elapsed <= fcast), elapsed)
        else:
            forecast = elapsed // 3 * 3

        return f"{cnow:%Y%m%d}", lcycle, forecast

    def _track_gfs_run(self, rec: GribRecord) -> None:
        """Keeps the cycle / forecast info of the wind data read"""
        if rec.run != self.gfs_run:
            self.gfs_run = rec.run
            self.gfs_fcst = rec.fcst

    @staticmethod
    def _convert_winds(wind: dict[str, dict]) -> tuple[list, list]:
        """Wind buckets -> ([alt, hdg, kt, {...}] rows, [alt, temp, dev, dew] rows), both sorted"""
        windlevels = []
        templevels = []

        for lvl, values in wind.items():
            if 'UGRD' not in values or 'VGRD' not in values:
                continue

            hdg, vel = c.c2p(float(values['UGRD']), float(values['VGRD']))
            alt = int(c.mb2alt(float(lvl)))

            # optional variables, False when missing
            temp = float(values['TMP']) if 'TMP' in values else False
            rh = float(values['RH']) if 'RH' in values else False
            dev = c.isaDev(alt, temp) if temp else False
            dew = c.dewpoint(temp, rh) if temp and rh else False

            windlevels.append([alt, hdg, c.ms2knots(vel), {'temp': temp, 'dev': dev, 'rh': rh, 'dew': dew, 'gust': 0}])
            if alt and temp:
                templevels.append([alt, temp, dev, dew])

        return sorted(windlevels), sorted(templevels)

    @staticmethod
    def _tropo_from_200mb(wind: dict[str, dict]) -> dict:
        """Fallback tropopause from the 200 mb level, in the raw format of a GRIB
        tropopause message: PRES in Pa (not mb!), TMP in K"""
        for lvl, values in wind.items():
            if float(lvl) == 200 and all(k in values for k in ('UGRD', 'VGRD', 'TMP')):
                return {'PRES': float(lvl) * 100, 'TMP': values['TMP']}
        return {}

    @staticmethod
    def _convert_clouds(clouds: dict[str, dict]) -> list:
        """Cloud layers -> sorted [bottom alt, top alt, coverage] rows"""
        rows = []
        for layer in clouds.values():
            if 'top' not in layer or 'bottom' not in layer:
                continue
            cover_key = next((k for k in ('LCDC', 'MCDC', 'HCDC') if k in layer), None)
            cover = float(layer[cover_key]) if cover_key else 0.0
            if cover:
                # layer pressures are in Pa -> mb
                bottom, top = float(layer['bottom']) * 0.01, float(layer['top']) * 0.01
                rows.append([round(c.mb2alt(bottom)), round(c.mb2alt(top)), cover])
        return sorted(rows)

    @staticmethod
    def _convert_turbulence(turb: dict[str, str]) -> list:
        """Eddy dissipation by pressure level -> sorted [alt, value] rows"""
        return sorted([round(c.mb2alt(float(lvl))), float(val)] for lvl, val in turb.items())

    @staticmethod
    def _convert_tropo(tropo: dict[str, str]) -> dict:
        """Raw tropopause values -> {'alt', 'temp', 'dev'}, or {} when incomplete"""
        if 'TMP' not in tropo or not any(k in tropo for k in ('PRES', 'HGT')):
            return {}
        alt = round(c.mb2alt(float(tropo['PRES']) * 0.01)) if 'PRES' in tropo else float(tropo['HGT'])
        temp = float(tropo['TMP'])
        return {'alt': float(alt), 'temp': temp, 'dev': c.isaDev(alt, temp)}

    @staticmethod
    def _convert_surface(surface: dict[str, str]) -> dict:
        """Surface values: renamed + float-converted for the SURFACE_RENAME keys (pressure Pa -> hPa),
        everything else is passed through untouched"""
        out = {k: v for k, v in surface.items() if k not in SURFACE_RENAME}
        for raw_key, key in SURFACE_RENAME.items():
            if raw_key in surface:
                val = float(surface[raw_key])
                out[key] = val * 0.01 if raw_key == 'PRES' else val
        return out

    def parse_grib_data(self, lat: float, lon: float, files: Iterable[Path] | None = None) -> dict:
        """Executes wgrib2 and parses its output

        files: grib files to read. Defaults to every file of the current base
               (self.grib_files); pass a subset (e.g. only the snod file) to
               avoid decoding files you don't need.
        """

        files = self.grib_files if files is None else files
        lines = chain.from_iterable(self.read_grib_file(f, lat, lon) for f in files)

        wind = defaultdict(dict)    # one bucket per pressure level (mb)
        ground = {}                 # 2 m / 10 m above ground readings, see below
        clouds = {}
        pressure = False
        tropo = {}
        surface = {}
        turb = {}
        checked = False

        for rec in map(GribRecord.parse, lines):
            level, variable, value = rec.level, rec.variable, rec.value

            match level:
                case (layer, 'cloud', *rest):
                    # ('low', 'cloud', 'bottom', 'level') PRES 95267.5  -> layer bottom / top pressure
                    # ('low', 'cloud', 'layer') LCDC 56.8              -> layer coverage
                    cloud = clouds.setdefault(layer, {})
                    if variable == 'PRES' and len(rest) > 1:
                        cloud[rest[0]] = value
                    else:
                        cloud[variable] = value

                case ('tropopause',):
                    tropo[variable] = value

                case ('surface',):
                    surface[variable] = value

                case ('2' | '10', 'm', 'above', 'ground'):
                    # near-surface readings, only used where the real levels have nothing (see below)
                    ground[variable] = value
                    if variable in WIND_VARS:
                        self._track_gfs_run(rec)

                case (hpa, 'mb'):
                    # pressure levels
                    if variable == 'EDPARM':
                        # Eddy Dissipation Parameter -> turbulence
                        if not checked:
                            if rec.run != self.wafs_run:
                                self.wafs_run = rec.run
                                self.wafs_fcst = rec.fcst
                            checked = True
                        turb[hpa] = value
                    elif variable in WIND_VARS:
                        self._track_gfs_run(rec)
                        wind[hpa][variable] = value
                    # ICESEV (icing severity) isn't consumed yet

                case ('mean', 'sea', 'level'):
                    if variable == 'PRMSL':
                        pressure = c.pa2inhg(float(value))

        # 2 m / 10 m readings stand in for what the real levels don't provide: the surface level
        # (Real Weather srfc files have no surface temperature, wind or humidity) and the 1000 mb bucket.
        # Where the files do have real values (GFS) those keep the priority.
        for variable, value in ground.items():
            surface.setdefault(variable, value)
            if variable in WIND_VARS:
                wind['1000'].setdefault(variable, value)

        # Let data ready to push on datarefs.
        windlevels, templevels = self._convert_winds(wind)

        if not tropo:
            # no tropopause message in the files: derive it from the 200 mb (~F386) level
            tropo = self._tropo_from_200mb(wind)

        return {
            'winds': windlevels,
            'clouds': self._convert_clouds(clouds),
            'temperature': templevels,
            'turbulence': self._convert_turbulence(turb),
            'pressure': pressure,
            'tropo': self._convert_tropo(tropo),
            'surface': self._convert_surface(surface),
        }

    def run(self, elapsed: int) -> None:
        """Worker function called by a worker thread to update the data"""

        if not self.download_enabled:
            # data download is disabled
            return

        if not self.conf.meets_wgrib2_requirements:
            return

        if self.download_wait:
            self.download_wait -= elapsed
            return

        datecycle, cycle, forecast = self.get_cycle_date()
        cache_file = self.get_cache_filename(datecycle, cycle, forecast)
        cache_file_path = Path(self.cache_path, cache_file)

        if not self.download:
            if self.last_grib == cache_file and cache_file_path.is_file():
                # Nothing to do
                return

            # Trigger new download
            url = self.get_download_url(datecycle, cycle, forecast)
            log.debug(f"Downloading: {url}")
            self.download = AsyncTask(
                GribDownloader.download,
                url,
                cache_file_path,
                binary=True,
                variable_list=self.variable_list,
                cancel_event=self.die,
                decompress=self.conf.wgrib2bin,
                spinfo=self.conf.spinfo
            )
            self.download.start()
        else:
            if not self.download.pending():
                self.download.join()
                if isinstance(self.download.result, Exception):
                    log.error(f"Error Downloading Grib file: {self.download.result}.")
                    utils.remove(cache_file_path)
                    # wait a try again
                    self.download_wait = 60
                else:
                    # New file available
                    if not self.conf.keepOldFiles and self.last_grib:
                        log.debug(f"Removing old file: {self.last_grib}")
                        utils.remove(Path(self.cache_path, self.last_grib))
                    self.last_grib = self.download.result.name
                    log.debug(f"{self.last_grib} successfully downloaded.")

                # reset download
                self.download = False
            else:
                # Waiting for download
                return


class Worker(threading.Thread):
    """Creates a new thread to periodically run worker functions on weather sources to trigger
    data updating or other tasks

    Attributes:
        workers (list): Worker functions to be called
        die (threading.Event): Se the flag to end the thread
        rate (int): wait rate seconds between runs
    """

    def __init__(self, workers, rate: int) -> None:
        self.workers = workers
        self.die = threading.Event()
        self.rate = rate
        threading.Thread.__init__(self)

    def run(self) -> None:
        while not self.die.wait(self.rate):
            for worker in self.workers:
                try:
                    worker.run(self.rate)
                except Exception:
                    # a failing source must not stop the other ones, nor its own next run
                    log.exception(f"Worker {worker.__class__.__name__} failed on run()")

        if self.die.is_set():
            for worker in self.workers:
                worker.shutdown()

    def shutdown(self) -> None:
        if self.is_alive():
            self.die.set()
            self.join(3)


class AsyncTask(threading.Thread):
    """Run an asynchronous task on a new thread

    Attributes:
        task (method): Worker method to be called
        die (threading.Event): Set the flag to end the tasks
        result (): return of the task method
    """

    def __init__(self, task, *args, **kwargs):

        self.task = task
        self.cancel = threading.Event()
        self.kwargs = kwargs
        self.args = args
        self.result = False
        threading.Thread.__init__(self)

        self.pending = self.is_alive

    def run(self):
        try:
            self.result = self.task(*self.args, **self.kwargs)
        except Exception as result:
            log.exception(f"AsyncTask {self.task.__name__} failed")
            self.result = result
        return

    def stop(self):
        if self.is_alive():
            self.cancel.set()
            self.join(3)


class GribDownloader:
    """Grib download utilities"""

    @staticmethod
    def decompress_grib(path_in: Path, path_out: Path, wgrib2bin, spinfo=False) -> None:
        """Unpacks grib file using wgrib2 binary"""
        args = [str(wgrib2bin), str(path_in), '-set_grib_type', 'simple', '-grib_out', str(path_out)]
        kwargs = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL,
                  'stderr': subprocess.PIPE, 'text': True}
        if spinfo:
            kwargs.update(startupinfo=spinfo, creationflags=subprocess.CREATE_NO_WINDOW)

        res = subprocess.run(args, timeout=120, **kwargs)
        if res.returncode or not path_out.is_file():
            raise GribDownloaderError(f"wgrib2 decompress failed (rc={res.returncode}): {res.stderr.strip()[:300]}")

    @staticmethod
    def download_part(url: str, file_out, start: int = 0, end: int = 0, **kwargs) -> None:
        """File Downloader supports gzip and cancel

        Args:
            url (str): the url to download
            file_out (file): Output file descriptor

            start (int): start bytes for partial download
            end (int): end bytes for partial download

        Kwargs:
            cancel_event (threading.Event): Cancel download setting the flag
            user_agent (str): User-Agent HTTP header

        """

        req = Request(url)
        req.add_header('Accept-encoding', 'gzip, deflate')

        user_agent = kwargs.pop('user_agent', f"XPNOAAWeather/{Conf.__VERSION__}")
        req.add_header('User-Agent', user_agent)

        headers = kwargs.pop('headers', {})
        for k, v in headers.items():
            req.add_header(k, v)

        # Partial download headers
        if start or end:
            req.headers['Range'] = f"bytes={start}-{end}"

        if hasattr(ssl, '_create_unverified_context'):
            context = ssl._create_unverified_context()
            if hasattr(ssl, 'OP_LEGACY_SERVER_CONNECT'):
                context.options |= ssl.OP_LEGACY_SERVER_CONNECT
            else:
                context.options |= 4
            params = {'context': context}
        else:
            params = {}

        log.debug(f"Downloading part of {url} with params: {params}")
        with urlopen(req, timeout=30, **params) as response:
            gz = False
            if url[-3:] == '.gz' or response.headers.get('content-encoding', '').find('gzip') > -1:
                gz = zlib.decompressobj(16 + zlib.MAX_WBITS)

            cancel = kwargs.pop('cancel_event', False)

            while True:
                if cancel and cancel.is_set():
                    raise GribDownloaderCancel("Download canceled by user.")

                data = response.read(1024 * 128)
                if not data:
                    # End of file
                    break
                if gz:
                    data = gz.decompress(data)

                if isinstance(file_out, io.TextIOBase):
                    file_out.write(str(data))
                else:
                    file_out.write(data)

    @staticmethod
    def to_download(level, var, variable_list: list) -> bool:
        """Returns true if level/var combination is in the download list"""
        for group in variable_list:
            if var in group['vars'] and level in group['levels']:
                return True
        return False

    @classmethod
    def generate_chunk_list(cls, grib_index: list, variable_list: list) -> list:
        """Returns a download list from a grib index and a variable list

        Args:
            grib_index (list):      parsed grib index
            variable_list (list):   list of dicts defining data to download
                                    [{'levels': [], 'vars': []}, ]

        Returns:
            list: The chunk list [[start, stop], ]

        """
        chunk_list = []
        end = False

        for line in reversed(grib_index):
            start, var, level = line[1], line[3], line[4]
            if cls.to_download(level, var, variable_list):
                if end:
                    end -= 1
                chunk_list.append([start, end])
            end = start

        chunk_list.reverse()

        return chunk_list

    @staticmethod
    def parse_grib_index(index_file) -> list:
        """Returns

        args:
            index_file (file): grib idx file

        Return:
            list: The table index

        Index sample:
            1:0:d=2020022418:HGT:100 mb:6 hour fcst:
            2:38409:d=2020022418:TMP:100 mb:6 hour fcst:

        """

        index = []
        for line in index_file:
            cols = line.decode('utf-8').split(':')
            if len(cols) != 7 or not cols[1].isdecimal():
                raise GribDownloaderError(f"Bad GRIB index line: {line!r}")
            cols[1] = int(cols[1])
            index.append(cols)
        return index

    @classmethod
    def download(cls, url, file_path: Path, binary: bool = False, **kwargs) -> Path:
        """Download grib for the specified variable_lists

            Args:
                url (str): URL to the grib file excluding the extension
                file_path (str): Path to the output file
                binary (bool): Set to True for binary files or files will get corrupted on Windows.

            Kwargs:
                cancel_event (threading.Event): Set the flat to cancel the download at any time
                variable_list (list): List of variables dicts ex: [{'level': ['500mb', ], 'vars': 'TMP'}, ]
                decompress (str): Path to the wgrib2 to decompress the file.
                spinfo: startup info for the wgrib2 subprocess (Windows)

            Returns:
                Path: the path to the final file on success

            Raises:
                GribDownloaderError: on fail.
                GribDownloaderCancel: on cancel.
        """

        try:
            return cls._download(url, file_path, binary, **kwargs)
        except (OSError, subprocess.SubprocessError) as e:   # URLError is an OSError too
            raise GribDownloaderError(f"Download failed for {url}: {e!r}") from e

    @classmethod
    def _download(cls, url, file_path: Path, binary: bool, **kwargs) -> Path:
        variable_list = kwargs.pop('variable_list', [])

        if variable_list:
            # Download the index and create a chunk list
            with TemporaryFile('w+b') as idx_file:
                cls.download_part(f"{url}.idx", idx_file, **kwargs)
                idx_file.seek(0)
                index = cls.parse_grib_index(idx_file)
                chunk_list = cls.generate_chunk_list(index, variable_list)

        flags = 'wb' if binary else 'w'
        with open(file_path, flags) as grib_file:
            if not variable_list:
                chunk_list = [[False, False]]   # fake chunk list for non filtered files
            for chunk in chunk_list:
                log.debug("downloading ...")
                cls.download_part(str(url), grib_file, start=chunk[0], end=chunk[1], **kwargs)
        log.debug("download ended")

        wgrib2 = kwargs.pop('decompress', False)
        spinfo = kwargs.pop('spinfo', False)
        if wgrib2:
            tmp_file = Path(f"{file_path}.tmp")
            file_path.replace(tmp_file)
            try:
                cls.decompress_grib(tmp_file, file_path, wgrib2, spinfo)
            finally:
                utils.remove(tmp_file)   # also cleans up when decompression fails

        return file_path


class GribDownloaderError(Exception):
    """Raised on a download error"""


class GribDownloaderCancel(Exception):
    """Raised when a download is canceled by user intervention"""
