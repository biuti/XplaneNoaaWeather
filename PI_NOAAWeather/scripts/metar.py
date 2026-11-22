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
import re
import math
import time
import base64
import json
import traceback

from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone
from itertools import batched
from pathlib import Path

from . import c, utils
from .database import Database
from .weathersource import WeatherSource, GribDownloaderError, GribDownloader, AsyncTask


class Metar(WeatherSource):
    """Provides METAR download and parsing routines. """

    # Metar parse regex
    RE_CLOUD = re.compile(r'\b(?P<coverage>FEW|BKN|SCT|OVC|VV)(?P<level>[0-9]+)(?P<type>[A-Z]{2,3})?\b')
    RE_WIND = re.compile(r'\b(?P<heading>VRB|[0-9]{3})(?P<speed>[0-9]{2,3})'
                         r'(?P<gust>G[0-9]{2,3})?(?P<unit>MPH|KT?|MPS|KMH)\b')
    RE_VARIABLE_WIND = re.compile(r'\b(?P<from_heading>[0-9]{3})V(?P<to_heading>[0-9]{3})\b')
    RE_VISIBILITY = re.compile(r'\b(CAVOK|[PM]?([0-9]{4})|([0-9] )?([0-9]{1,2})(/[0-9])?(SM|KM))\b')
    RE_PRESSURE = re.compile(r'\b(Q|QNH|SLP|A)[ ]?([0-9]{3,4})\b')
    RE_TEMPERATURE = re.compile(r'\b([M-])?([0-9]{1,2})/([M-])?([0-9]{1,2})\b')
    RE_TEMPERATURE2 = re.compile(r'\bT([01])([0-9]{3})([01])([0-9]{3})\b')
    RE_PRECIPITATION = re.compile(r'(?P<intensity>[-+])?(?P<recent>RE)?(?P<modifier>DZ|SG|IC|PL|SH)?'
                                  r'(?P<kind>DZ|RA|SN|TS)(?P<negation>NO|E)?')
    RE_RVR = re.compile(r'R(?P<runway>(?P<heading>[0-9]{2})(?P<rw_position>[LCR])?)/'
                        r'(?P<exceed>[PM])?(?P<visibility>[0-9]{4})(?P<change>[UDN])?')
    RE_NON_ASCII = re.compile(r'[^\x00-\x7F]+')

    METAR_STATIONS_GZIP = 'https://aviationweather.gov/data/cache/stations.cache.json.gz'
    # this seems to be a static copy to use as a backup source
    METAR_STATIONS_BACKUP_URL = 'https://weather.rap.ucar.edu/surface/stations.txt'
    NOAA_METAR_URL = 'https://aviationweather.gov/data/cache/metars.cache.csv.gz'
    VATSIM_METAR_URL = 'https://metar.vatsim.net/metar.php?id=all'
    IVAO_METAR_URL = 'https://api.ivao.aero/v2/airports/all/metar'

    METAR_URLS = {'NOAA': NOAA_METAR_URL, 'VATSIM': VATSIM_METAR_URL, 'IVAO': IVAO_METAR_URL}

    STATION_UPDATE_RATE = 30  # In days

    table = 'source'

    def __init__(self, conf) -> None:

        self.db = Database(conf.db_file)

        super().__init__(conf)

        # Download flags
        self.ms_download = False
        self.downloading = False

        # Metar stations update
        if (time.time() - conf.ms_update) > self.STATION_UPDATE_RATE * 86400:
            self.download_stations()
        self.last_timestamp = 0

    def download_stations(self, url: str = METAR_STATIONS_GZIP, filename: str = 'stations.json') -> None:
        self.ms_download = AsyncTask(
            GribDownloader.download,
            url,
            Path(self.cache_path, filename),
            binary=True,
            cancel_event=self.die
        )
        self.ms_download.start()
        self.ms_url = url

    @staticmethod
    def _stations_from_json(stations: list[dict]) -> Iterator[tuple]:
        """aviationweather.gov stations cache -> (icao, lat, lon, elevation) rows"""
        for el in stations:
            icao = el['icaoId']
            if icao not in (None, '') and len(icao) < 5 and not icao[0].isdigit():
                yield icao, el['lat'], el['lon'], el['elev']

    @staticmethod
    def _stations_from_text(lines: Iterable[str]) -> Iterator[tuple]:
        """Static stations.txt backup (fixed width columns) -> (icao, lat, lon, elevation) rows"""
        for el in lines:
            if el[:1] != '!' and len(el) > 80 and el[20] != ' ' and el[51] != '9':
                lat, lon = c.parse_dm(el[39:54])
                yield el[20:24].strip('"'), lat, lon, int(el[55:59])

    def update_stations(self, path: Path, batch: int = 100) -> int:
        """Updates db's airport information from the METAR stations file"""

        query = f"""INSERT OR REPLACE INTO {self.table}
                        (icao, lat, lon, elevation, timestamp)
                    VALUES
                        (?,?,?,?,0)"""

        # the file is read only once: if it isn't json we parse the same text as the backup static file
        with open(path, encoding='utf-8', errors='replace') as f:
            text = f.read()
        try:
            rows = self._stations_from_json(json.loads(text))
        except json.JSONDecodeError:
            rows = self._stations_from_text(io.StringIO(text))

        nparsed = 0
        for chunk in batched(rows, batch):
            nparsed += len(chunk)
            self.db.writemany(query, list(chunk))

        self.conf.ms_update = time.time()
        return nparsed

    @classmethod
    def _parse_report_line(cls, line: str, today: str, today_prefix: str, yesterday_prefix: str) -> tuple | None:
        """One line of a METAR download -> (timestamp, metar, icao, timestamp) query bindings, None if unusable"""

        # 20250915 NOAA changed line formats to:
        # "SPECI YMML 181527Z AUTO 01023G33KT 9999 // NCD 13/04 Q1015",YMML,2025-09-18T15:27:00.000Z,-37.6660,144.8320,13,4,10,23,33,6+,29.97,,,TRUE,,,,,,,,,,,,,,,,VFR,,,,,,,,,,,,SPECI,115
        # "METAR KFCH 181640Z AUTO 00000KT 10SM CLR 24/11 A2980 RMK A01",KFCH,2025-09-18T15:26:11.000Z,36.7338,-119.8204,24,11,0,,,10+,29.80,,,TRUE,TRUE,,,,,,,,,,,,,,,VFR,,,,,,,,,,,,METAR,85
        # IVAO (decoded json):
        # KSFO 181656Z 00000KT 10SM CLR 19/11 A2992 RMK AO2 SLP134 T01940106 10194 201
        # VATSIM:
        # KEVU 181635Z AUTO 13007KT 10SM CLR 22/21 A3002 RMK AO2 T02280212
        # METAR MMZO 170140Z 06005KT 8SM BKN020 BKN210 25/24 A2982 RMK 8/105 RA E40

        if line.startswith('"') and len(line) > 18 and line[18] == 'Z':
            line = line.split('"')[1]
        elif line[:1].isalpha() and len(line) > 11 and line[11] == 'Z':
            line = line.split(',')[0]

        line = line.replace('METAR', '').replace('SPECI', '').replace('metar', '').strip()
        if len(line) <= 11:
            return None

        icao, mtime, metar = line[0:4], line[5:11], cls.RE_NON_ASCII.sub(' ', line[5:]).strip()

        if mtime[-1] == 'Z':
            mtime = '0' + mtime[:-1]

        if not mtime.isdigit():
            mtime = '000000'

        # Prepend year and month to the timestamp
        timestamp = (today_prefix if mtime[:2] == today else yesterday_prefix) + mtime

        return timestamp, metar, icao, timestamp

    def update_metar(self, path: Path, batch: int = 100) -> tuple[int, int]:
        """Updates metar table from Metar file. Returns (updated, parsed)"""

        today, today_prefix, yesterday_prefix = utils.date_info()

        # deal with non utf-8 characters, avoiding error
        with open(path, encoding='utf-8', errors='replace') as f:
            if self.conf.metar_source == 'IVAO':
                lines = [e['metar'] for e in json.load(f)]
            else:
                lines = f.readlines()

        query = f"UPDATE {self.table} SET timestamp = ?, metar = ? WHERE icao = ? AND timestamp < ?"

        rows = (self._parse_report_line(line, today, today_prefix, yesterday_prefix) for line in lines)

        nupdated = 0
        nparsed = 0
        for chunk in batched(filter(None, rows), batch):
            nparsed += len(chunk)
            nupdated += self.db.writemany(query, list(chunk))

        if not self.conf.keepOldFiles:
            utils.remove(path)

        return nupdated, nparsed

    @staticmethod
    def clear_reports(file: Path) -> None:
        """Clears all metar reports from the db"""

        db = Database(file)
        db.query(f"UPDATE {Metar.table} SET metar = NULL, timestamp = 0")

    def get_closest_station(self, lat: float, lon: float) -> tuple[str, float, float, int, int, str] | None:
        """Return the closest airport with a metar report
            ex: ('ENSB', 78.25, 15.4667, 30, 202211271620, '271620Z 22003KT 9999 FEW025 BKN070 M02/M05 Q1017 RMK WIND 1400FT VRB03KT')"""

        conditions = ['metar NOT NULL']
        bindings = []

        if ignored := self.conf.ignore_metar_stations:
            conditions.append(f"icao NOT IN ({','.join('?' * len(ignored))})")
            bindings.extend(ignored)
        if self.conf.metar_ignore_auto:
            conditions.append("metar NOT LIKE '%AUTO%'")

        # longitude degrees get shorter away from the equator
        fudge = math.pow(math.cos(math.radians(lat)), 2)
        bindings.extend([lat, lat, lon, lon, fudge])

        query = f"""SELECT * FROM {self.table}
                        WHERE {' AND '.join(conditions)}
                        ORDER BY ((? - lat) * (? - lat) + (? - lon) * (? - lon) * ?)
                        LIMIT 1"""

        with self.db.session() as db:
            return db.execute(query, tuple(bindings)).fetchone()

    def get_metar(self, icao: str) -> tuple[str, str]:
        """Returns the METAR from an airport icao code"""
        return self.db.get(self.table, icao)

    @staticmethod
    def get_current_cycle() -> tuple[str, int]:
        """Returns the current METAR cycle"""
        now = datetime.now(timezone.utc)
        # Cycle is updated until the hour has arrived (ex: 01 cycle updates until 1am)

        current_cycle = now + timedelta(hours=0, minutes=5)

        timestamp = int(time.time())
        return f"{current_cycle.hour:02}", timestamp

    @classmethod
    def parse_metar(cls, icao: str, metar: str, airport_msl: float = 0) -> dict:
        """Returns a parsed METAR"""

        weather = {
            'icao': icao,
            'metar': metar,
            'elevation': airport_msl,  # meters
            'wind': [0, 0, 0],  # Heading, speed, shear
            'variable_wind': False,
            'ceiling': c.f2m(5000) + airport_msl,  # minimum cloud level, meters
            'clouds': [0, 0, False] * 3,  # Alt, coverage type
            'temperature': [False, False],  # Temperature, dewpoint
            'pressure': False,  # space c.pa2inhg(10.1325),
            'visibility': 9998,  # meters
            'precipitation': {},
            'rvr': []
        }

        metar = metar.split('TEMPO')[0]

        weather['clouds'] = cls._parse_clouds(metar, airport_msl)
        weather['ceiling'] = min([weather['ceiling'], *(cloud[0] for cloud in weather['clouds'])])
        weather['pressure'] = cls._parse_pressure(metar)
        weather['temperature'] = cls._parse_temperature(metar)

        # the remarks section only carries extra info (pressure and temperature above)
        metar = metar.split('RMK')[0]

        if (visibility := cls._parse_visibility(metar)) is not None:
            weather['visibility'] = visibility

        weather['wind'], weather['variable_wind'] = cls._parse_wind(metar)
        weather['precipitation'] = cls._parse_precipitation(metar)
        weather['rvr'] = [int(rvr['visibility']) for rvr in cls.RE_RVR.finditer(metar)]

        # Extended visibility
        if weather['visibility'] > 9998:
            weather['mt_visibility'] = weather['visibility']
            ext_vis = c.rh2visibility(c.dewpoint2rh(*weather['temperature']))
            if ext_vis > weather['visibility']:
                weather['visibility'] = int(ext_vis)

        return weather

    @classmethod
    def _parse_clouds(cls, metar: str, airport_msl: float) -> list[list]:
        """Cloud groups -> [[alt in meters msl, coverage, type], ]"""
        return [[float(level) * 30.48 + airport_msl, coverage, cloud_type]
                for coverage, level, cloud_type in cls.RE_CLOUD.findall(metar)]

    @classmethod
    def _parse_pressure(cls, metar: str) -> float | bool:
        """QNH in inHg, False if missing or out of range"""
        m = cls.RE_PRESSURE.search(metar)
        if not m:
            return False

        unit, value = m.groups()
        press = float(value)

        match unit:
            case 'A':
                press /= 100
            case 'SLP':
                press = c.pa2inhg((press / 10 + (900 if press > 500 else 1000)) * 100)
            case 'Q' | 'QNH':
                press = c.pa2inhg(press * 100)

        return press if 25 < press < 35 else False

    @classmethod
    def _parse_temperature(cls, metar: str) -> list:
        """[temperature, dewpoint] in C, the precise remarks group (T02280212) first"""
        if m := cls.RE_TEMPERATURE2.search(metar):
            tp, temp, dp, dew = m.groups()
            temp, dew = float(temp) * 0.1, float(dew) * 0.1
            return [-temp if tp == '1' else temp, -dew if dp == '1' else dew]

        if m := cls.RE_TEMPERATURE.search(metar):
            temps, temp, dews, dew = m.groups()
            return [-int(temp) if temps else int(temp), -int(dew) if dews else int(dew)]

        return [False, False]

    @classmethod
    def _parse_visibility(cls, metar: str) -> float | None:
        """Visibility in meters, None if not reported"""
        m = cls.RE_VISIBILITY.search(metar)
        if not m:
            return None

        if m.group(0) == 'CAVOK' or (m.group(0)[0] == 'P' and int(m.group(2)) > 7999):
            return 9999

        _, vis1, vis2, vis3, div, unit = m.groups()
        visibility = 0

        if vis1:
            visibility += int(vis1)
        if vis2:
            visibility += int(vis2)
        if vis3:
            vis3 = int(vis3)
            if div:
                vis3 /= float(div[1:])
            visibility += vis3
        if unit == 'SM':
            visibility *= 1609.34
        if unit == 'KM':
            visibility *= 1000

        return visibility

    @staticmethod
    def _to_knots(speed: float, unit: str) -> float:
        match unit:
            case 'MPS':
                return c.ms2knots(speed)
            case 'MPH':
                return c.ms2knots(speed * 0.44704)
            case 'KMH':
                return c.ms2knots(speed / 3.6)
            case _:
                return speed

    @classmethod
    def _parse_wind(cls, metar: str) -> tuple[list, list | bool]:
        """([heading, speed in knots, gust in knots], variable wind range or False)"""
        wind, variable_wind = [0, 0, 0], False

        if m := cls.RE_WIND.search(metar):
            heading, speed, gust, unit = m.groups()
            if heading == 'VRB':
                heading = 0
                variable_wind = [0, 360]
            else:
                heading = int(heading)

            speed = int(speed)
            gust = int(gust[1:]) - speed if gust else 0

            wind = [heading, cls._to_knots(speed, unit), cls._to_knots(gust, unit)]

        if m := cls.RE_VARIABLE_WIND.search(metar):
            variable_wind = [int(heading) for heading in m.groups()]

        return wind, variable_wind

    @classmethod
    def _parse_precipitation(cls, metar: str) -> dict:
        """Present weather -> {'RA': {'int': '-', 'mod': '', 'recent': ''}, }"""
        precipitation = {}
        for intensity, recent, modifier, kind, negation in cls.RE_PRECIPITATION.findall(metar):
            if negation == 'E':
                recent = 'RE'
            if negation != 'NO':
                precipitation[kind] = {'int': intensity, 'mod': modifier, 'recent': recent}
        return precipitation

    def run(self, elapsed: int) -> None:

        # Update stations table if required
        if self.ms_download:
            if not self.ms_download.pending():
                self.ms_download.join()
                stations = self.ms_download.result
                self.ms_download = False

                if isinstance(stations, Exception):
                    print(f"Error downloading metar stations file: {stations!r}")

                    if self.ms_url == self.METAR_STATIONS_GZIP:
                        print("Trying backup static url")
                        self.download_stations(
                            url=self.METAR_STATIONS_BACKUP_URL,
                            filename='stations.txt'
                        )
                else:
                    print('Updating metar stations.')
                    try:
                        nstations = self.update_stations(stations)
                        print(
                            f"**** {datetime.now(timezone.utc).strftime('%H:%M:%S')} "
                            f"{nstations} metar stations updated."
                        )
                    except Exception:
                        traceback.print_exc()

            else:
                print(
                    f" **** {datetime.now(timezone.utc).strftime('%H:%M:%S')} "
                    "waiting stations download ... "
                )

        # Check for new metar downloaded data
        elif self.download:
            if not self.download.pending():
                metar_file = self.download.result
                self.download.join()
                if isinstance(metar_file, Exception):
                    print(f"Error downloading METAR: {metar_file!r}")
                else:
                    self.update_metar(metar_file)
                self.download = False

        elif self.conf.download_METAR:
            # Download new data if required
            cycle, timestamp = self.get_current_cycle()
            if (timestamp - self.last_timestamp) > self.conf.metar_updaterate * 60:
                self.last_timestamp = timestamp
                self.download_cycle(cycle, timestamp)

    def download_cycle(self, cycle: str, timestamp: int) -> None:
        """Downloads a new METAR cycle when required.
            The cycle is the hour of the day in UTC, and timestamp is the current time in seconds since epoch"""

        source = self.conf.metar_source

        if (url := self.METAR_URLS.get(source)) is None:
            print(f"Unknown METAR source: {source!r}")
            return

        self.downloading = True

        headers = {}
        if source == 'IVAO':
            key = base64.b64decode(Path(self.conf.bin_path, 'ivao.bin').read_text())
            headers = {
                'accept': 'application/json',
                'apiKey': key
            }

        cache_file = Path(self.cache_path, f"{source}_{timestamp}_{cycle}Z.txt")
        self.download = AsyncTask(
            GribDownloader.download,
            url=url,
            file_path=cache_file,
            binary=True,
            cancel_event=self.die, headers=headers
        )
        self.download.start()

    def shutdown(self) -> None:
        super().shutdown()
        self.db.commit()
        self.db.close()
