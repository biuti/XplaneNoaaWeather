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

import io
import threading
import ssl
import zlib
import subprocess
import sys

from urllib.request import Request, urlopen
from urllib.error import URLError
from datetime import datetime, timedelta
from tempfile import TemporaryFile
from pathlib import Path

from . import util, Conf


class WeatherSource(object):
    """Weather source metaclass"""

    cache_path = False

    def __init__(self, conf):
        self.download = False
        self.conf = conf
        self.die = threading.Event()

        if not self.cache_path:
            self.cache_path = self.conf.cachepath

        self.cache_path.mkdir(parents=True, exist_ok=True)

    def read_grib_file(self, file: Path, lat: float = 46, lon: float = 9) -> list:
        """Executes wgrib2 on given GRIB file and parses its output"""

        args = [
            '-s',
            '-lon',
            f"{lon}",
            f"{lat}",
            file
        ]

        kwargs = {'stdout': subprocess.PIPE, "text": True}
        if self.conf.spinfo:
            kwargs.update({'startupinfo': self.conf.spinfo, 'shell': True})

        proc = subprocess.Popen([self.conf.wgrib2bin] + args, **kwargs)

        if not proc.stdout:
            return [] 

        return proc.stdout.read().splitlines()

    def shutdown(self):
        """Stop pending processes"""
        self.die.set()

    def run(self, elapsed):
        """Called by a worker thread"""
        return


class GribWeatherSource(WeatherSource):
    """Grib file weather source"""

    cycles = range(0, 24, 6)
    publish_delay = {'hours': 4, 'minutes': 25}
    variable_list = []
    download_wait = 0
    grib_conf_var = 'lastgrib'

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

    def __init__(self, conf):
        self.cache_path = Path(conf.cachepath, 'gfs')

        super().__init__(conf)

        if self.last_grib and not Path(self.cache_path, self.last_grib).is_file():
            self.last_grib = False

    @classmethod
    def get_cycle_date(cls) -> tuple[str, int, int]:
        """Returns last cycle date available"""

        now = datetime.utcnow()
        # cycle is published with 4 hours 25min delay
        cnow = now - timedelta(**cls.publish_delay)
        # get last cycle
        for cycle in cls.cycles:
            if cnow.hour >= cycle:
                lcycle = cycle
        # Forecast
        adjs = 0
        if cnow.day != now.day:
            adjs = +24
        forecast = (adjs + now.hour - lcycle) // 3 * 3

        return f"{cnow.year}{cnow.month:02}{cnow.day:02}", lcycle, forecast

    def run(self, elapsed: int):
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
            print(f"Downloading: {url}")
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
                    print(f"Error Downloading Grib file: {self.download.result}.")
                    util.remove(cache_file_path)
                    # wait a try again
                    self.download_wait = 60
                else:
                    # New file available
                    if not self.conf.keepOldFiles and self.last_grib:
                        util.remove(Path(self.cache_path, self.last_grib))
                    self.last_grib = self.download.result.name
                    print(f"{self.last_grib} successfully downloaded.")

                # reset download
                self.download = False
            else:
                # Waiting for download
                return

    def __getattr__(self, item):
        if item == 'last_grib':
            return getattr(self.conf, self.grib_conf_var)
        return self.__getattribute__(item)

    def __setattr__(self, key, value):
        if key == 'last_grib':
            self.conf.__dict__[self.grib_conf_var] = value
        self.__dict__[key] = value


class Worker(threading.Thread):
    """Creates a new thread to periodically run worker functions on weather sources to trigger
    data updating or other tasks

    Attributes:
        workers (list): Worker functions to be called
        die (threading.Event): Se the flag to end the thread
        rate (int): wait rate seconds between runs
    """

    def __init__(self, workers, rate):
        self.workers = workers
        self.die = threading.Event()
        self.rate = rate
        threading.Thread.__init__(self)

    def run(self):
        while not self.die.wait(self.rate):
            for worker in self.workers:
                worker.run(self.rate)

        if self.die.is_set():
            for worker in self.workers:
                worker.shutdown()

    def shutdown(self):
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
            self.result = result
        return

    def stop(self):
        if self.is_alive():
            self.cancel.set()
            self.join(3)


class GribDownloader(object):
    """Grib download utilities"""

    @staticmethod
    def decompress_grib(path_in: Path, path_out: Path, wgrib2bin, spinfo=False):
        """Unpacks grib file using wgrib2 binary
        """
        args = [wgrib2bin, path_in, '-set_grib_type', 'simple', '-grib_out', path_out]
        kwargs = {'stdout': sys.stdout, 'stderr': sys.stderr}

        if spinfo:
            kwargs.update({'shell': True, 'startupinfo': spinfo})

        p = subprocess.Popen(args, **kwargs)
        p.wait()

    @staticmethod
    def download_part(url: str, file_out, start: int = 0, end: int = 0, **kwargs):
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

        print(f"Downloading part of {url} with params: {params}")
        response = urlopen(req, **params)

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
            try:
                if isinstance(file_out, io.TextIOBase):
                    file_out.write(str(data))
                else:
                    file_out.write(data)
            except Exception as e:
                print(f"Failed to write out bit: {e}")
                raise e

    @staticmethod
    def to_download(level, var, variable_list) -> bool:
        """Returns true if level/var combination is in the download list"""
        for group in variable_list:
            if var in group['vars'] and level in group['levels']:
                return True
        return False

    @classmethod
    def gen_chunk_list(cls, grib_index: list, variable_list: list) -> list:
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
            if len(cols) != 7:
                raise RuntimeError(f"Bad GRIB file index format: Missing columns. Expected 7,  Found {len(cols)} columns: {cols}")
            try:
                cols[1] = int(cols[1])
            except ValueError as e:
                raise RuntimeError(f"Bad GRIB file index format: Bad integer, found: {cols[1]}") from e

            index.append(cols)

        return index

    @classmethod
    def download(cls, url, file_path: Path, binary=False, **kwargs) -> Path:
        """Download grib for the specified variable_lists

            Args:
                url (str): URL to the grib file excluding the extension
                file_path (str): Path to the output file
                binary (bool): Set to True for binary files or files will get corrupted on Windows.

            Kwargs:
                cancel_event (threading.Event): Set the flat to cancel the download at any time
                variable_list (list): List of variables dicts ex: [{'level': ['500mb', ], 'vars': 'TMP'}, ]
                decompress (str): Path to the wgrib2 to decompress the file.

            Returns:
                Path: the path to the final file on success

            Raises:
                GribDownloaderError: on fail.
                GribDownloaderCancel: on cancel.
        """

        variable_list = kwargs.pop('variable_list', [])

        if variable_list:
            # Download the index and create a chunk list
            with TemporaryFile('w+b') as idx_file:
                idx_file.seek(0)
                try:
                    cls.download_part(f"{url}.idx", idx_file, **kwargs)
                except URLError as e:
                    raise GribDownloaderError(f"Unable to download index file for: {url} - Error: {repr(e)}") from e

                idx_file.seek(0)

                index = cls.parse_grib_index(idx_file)
                chunk_list = cls.gen_chunk_list(index, variable_list)

        flags = 'wb' if binary else 'w'

        with open(file_path, flags) as grib_file:
            if not variable_list:
                # Fake chunk list for non filtered files
                chunk_list = [[False, False]]

            for chunk in chunk_list:
                print(f"downloading ...")
                try:
                    cls.download_part(str(url), grib_file, start=chunk[0], end=chunk[1], **kwargs)
                except URLError as e:
                    raise GribDownloaderError(f"Unable to open url: {url} \n\t{repr(e)}") from e

        print(f"download ended")
        wgrib2 = kwargs.pop('decompress', False)
        spinfo = kwargs.pop('spinfo', False)
        if wgrib2:
            tmp_file = Path(f"{file_path}.tmp")
            try:
                file_path.rename(tmp_file)
                cls.decompress_grib(tmp_file, file_path, wgrib2, spinfo)
                util.remove(tmp_file)
            except OSError as e:
                raise GribDownloaderError(f"Unable to decompress: {file_path.name} \n\t{repr(e)}") from e

        return file_path


class GribDownloaderError(Exception):
    """Raised on a download error"""


class GribDownloaderCancel(Exception):
    """Raised when a download is canceled by user intervention"""
