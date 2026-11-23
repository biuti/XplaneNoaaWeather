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

import os
import platform
import subprocess
import json
import pickle
import re

from pathlib import Path

from . import __version__, c, log


class Conf:
    """Loads and saves configuration variables"""
    xplane_path, dirsep = '', os.sep
    printable_chars = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~ '

    # Dref and Command parameters
    plugin_command_origin = 'XPNOAAWeather'

    # Load the version from the version.txt file if it exists
    __VERSION__ = __version__

    GFS_JSON_HELP = '''Here you can edit which wind levels will be downloaded from NOAA without hacking the code.
                    Keep the list short to optimize the download size and parsing times.
                    If you mess-up just remove this file, a new one will be created with default values.

                    For a full list of levels check:
                    https://www.nco.ncep.noaa.gov/pmb/products/gfs/gfs.t00z.pgrb2.0p50.f003.shtml
                    Remove the current cycle from the cache/gfs to trigger a download with new values.

                    Refer to the following list for millibar Flight Level conversion:'''

    def __init__(self, xplane_path: str | Path) -> None:
        """The plugin uses wgrib2 utility to decode GFS files
        - Compiled version: v3.1.0"""

        # X-Plane
        self.xplane_path = Path(xplane_path)
        self.rw_path = Path(self.xplane_path, 'Output', 'real weather')
        self.pref_path = Path(self.xplane_path, 'Output', 'preferences')

        # Plugin
        self.plugin_path = Path(xplane_path, 'Resources', 'plugins', 'PythonPlugins', 'PI_NOAAWeather')
        self.scripts_path = Path(self.plugin_path, 'scripts')
        self.bin_path = Path(self.plugin_path, 'bin')

        # Runtime
        self.cache_path = Path(self.plugin_path, 'cache')
        self.cache_path.mkdir(parents=True, exist_ok=True)

        self.db_file = Path(self.cache_path, 'metar', 'metar.db')
        self.db_file.parent.mkdir(parents=True, exist_ok=True)

        # Settings files
        self.settings_file = Path(self.plugin_path, 'settings.pkl')
        self.server_settings_file = Path(self.plugin_path, 'weather_server_state.pkl')
        self.gfs_levels_file = Path(self.plugin_path, 'gfs_levels_config.json')
        self.version_file = Path(self.plugin_path, 'version.txt')

        # Log file
        self.server_log_file = Path(self.plugin_path, 'weather_server_log.txt')

        # read settings files
        self.set_defaults()
        self.load_plugin_settings()
        self.load_server_settings()

        log.debug(f"NOAA plugin version: {self.__VERSION__}")
        log.debug(f"X-Plane path: {self.xplane_path}")
        log.debug(f"Plugin path: {self.plugin_path}")
        log.debug(f"Cache path: {self.cache_path}")
        log.debug(f"Settings file: {self.settings_file}")
        log.debug(f"GFS levels file: {self.gfs_levels_file}")
        log.debug(f"Database file: {self.db_file}")
        log.debug(f"Log file: {self.server_log_file}")
        log.debug(f"Real Weather path: {self.rw_path} - Exists: {self.rw_path.is_dir()}")

        # Config Overrides
        self.parse_rate = 1
        # self.metar_agl_limit = 10

        # Selects the appropriate wgrib binary
        self.platform = platform.system().lower()
        self.version = c.float_or_lower(platform.release())
        self.machine = platform.machine().lower()
        self.spinfo = False
        self.wgrib2bin = None

        # Select the appropriate wgrib2 binary for the current platform
        wgbin = False
        if self.platform == 'darwin' and self.version >= 18.0:  # Mojave and above
            # 18.0 Mojave (MacOS 10.14)
            # 19.0 Catalina (MacOS 10.15)
            # 20.0 Big Sur (MacOS 16.0)
            # 21.0 Monterey (MacOS 12.0)
            if 'arm' in self.machine:  # Apple Silicon
                wgbin = 'AppleSilicon-wgrib2'  # compiled in MacOS 26.2 Tahoe using Apple Silicon
            else:
                wgbin = 'OSX11wgrib2'  # compiled in MacOS 11.6.3 Big Sur

        elif self.platform == 'windows' and self.version >= 7.0:  # Windows 7 and above
            wgbin = 'WIN32wgrib2.exe'  # compiled in windows 11 using cygwin
            # Set environ for cygwin
            os.environ['CYGWIN'] = 'nodosfilewarning'
            # Hide wgrib window for Windows users
            self.spinfo = subprocess.STARTUPINFO()
            self.spinfo.dwFlags |= 1  # STARTF_USESHOWWINDOW
            self.spinfo.wShowWindow = 0  # 0 or SW_HIDE 0

        elif self.platform == 'linux' and self.version >= 4.0:  # Kernel 4.0 (Ubuntu 16.04) and above
            # Linux
            wgbin = 'linux-wgrib2'  # compiled in Ubuntu 20.04 LTS

        if wgbin:
            self.wgrib2bin = Path(self.bin_path, wgbin)
            # Enforce execution rights
            try:
                self.wgrib2bin.chmod(0o775)
            except Exception:
                pass

        self.meets_wgrib2_requirements = wgbin is not False

    @property
    def gfs_variable_list(self) -> list[dict]:
        return self.gfs_levels_real_weather() if self.use_real_weather_data else self.gfs_levels

    @property
    def wafs_variable_list(self) -> list[dict]:
        return self.wafs_levels_real_weather() if self.use_real_weather_data else self.wafs_levels_default()

    def set_defaults(self) -> None:
        """Default settings"""
        log.debug("Loading defaults settings ...")

        '''XP cloud cover'''
        # in XP 11.50+ cloud coverage and type goes:
        # type  cover   max thickness   desc
        #   0       0       null        null
        #   1       1       2000        CIRRUS
        #   1       2       2000        FEW, CIRRUSTATUS
        #   2       3           SCT
        #   3       4           BKN
        #   4       5           OVC
        #   5       6           STRATUS
        # cover value changes automatically, default value for type 1 is 2
        # changed Dataref from type to coverage
        self.xp_clouds = {
            'CIRRUS': [1, c.f2m(1000)],
            'FEW': [2, c.f2m(2000)],
            'SCT': [3, c.f2m(4000)],
            'BKN': [4, c.f2m(4000)],
            'OVC': [5, c.f2m(4000)],
            'VV': [6, c.f2m(6000)]
        }

        # Minimum redraw difference per layer (legacy cloud layers procedure)
        self.min_redraw = [
            c.f2m(500),
            c.f2m(5000),
            c.f2m(10000)
        ]

        # User settings
        self.enabled = True

        self.metar_decode = False

        # Weather settings mainly used if RealWeather is disabled
        self.set_wind = False
        self.set_tropo = False
        self.set_clouds = False
        self.opt_clouds_update = False
        self.set_temp = False
        self.set_visibility = False
        self.set_turb = False
        self.set_pressure = False
        self.set_thermals = False
        self.set_surface_layer = False
        self.turbulence_probability = 1

        # Adjust runway friction
        self.set_friction = True

        # added GFS Data in real Weather
        self.set_snow = True
        self.set_patches = False

        self.download_METAR = True

        # Waiting for weather API SDK the plugin mainly monitors Real Weather
        self.use_real_weather_data = True

        # Avoid downloading GFS and WAFS data until it will have some use in XP12
        self.download_GFS = False
        self.download_WAFS = False

        # From this AGL level METAR values are interpolated to GFS ones.
        self.metar_agl_limit = 20  # In meters
        # From this distance from the airport gfs data is used for temp, dew, pressure and clouds
        self.metar_distance_limit = 100000  # In meters
        # Max Altitude for TS clouds
        self.ts_clouds_top = 10000  # In meters (tropo limit?)
        # keep a surface wind layer with current METAR to get correct ATIS info
        self.surface_wind_layer_limit = 600  # In meters over first GFS layer for smooth transition

        self.parse_rate = 1
        self.updaterate = 1
        self.keepOldFiles = False

        # Performance tweaks
        self.max_visibility = False  # in SM
        self.max_cloud_height = False  # in feet

        # Weather server configuration
        self.server_updaterate = 10  # Run the weather loop each #seconds
        self.server_address = '127.0.0.1'
        self.server_port = 8950

        # Weather server variables
        self.lastgrib = False
        self.lastwafsgrib = False
        self.ms_update = 0

        self.weather_server_pid = False

        # Surface wind random variability range
        self.max_random_wind_heading = 5  # degrees
        self.max_random_wind_gust = 5  # kt

        # Max Turbulence (1 = severe turbulence)
        self.max_turbulence = 0.9

        # Transitions
        self.windTransSpeed = 0.5  # kt/s
        self.windGustTransSpeed = 1  # kt/s
        self.windHdgTransSpeed = 1  # degrees/s

        self.metar_source = 'NOAA'
        self.metar_updaterate = 5  # minutes
        self.metar_ignore_auto = False

        self.ignore_metar_stations = []

        # windows position
        self.info_window_position = [220, 640]
        self.metar_window_position = [10, 900]
        self.config_window_position = [200, 640]

    def save_settings(self, filepath: Path, settings: dict) -> None:
        try:
            with open(filepath, 'wb') as f:
                pickle.dump(settings, f)
        except (OSError, pickle.PicklingError, AttributeError, TypeError):
            log.exception(f"ERROR saving settings to {filepath.name}")

    def load_settings(self, filepath: Path) -> None:
        if filepath.is_file():
            try:
                with open(filepath, 'rb') as f:
                    settings = pickle.load(f)
            except (pickle.UnpicklingError, EOFError, ValueError):
                log.exception(f"{filepath.name}: Corrupted pickle data, deleting ...")
                filepath.unlink(missing_ok=True)
                return
            except OSError:
                log.exception(f"{filepath.name}: I/O error reading pickle file, skipping ...")
                return
            except Exception:
                log.exception(f"{filepath.name}: Unexpected error loading pickle file, deleting ...")
                filepath.unlink(missing_ok=True)
                return

            # Reset settings on different versions.
            if 'version' not in settings or self.version_tuple(settings['version']) < (12, 0, 0):
                log.info("Saved settings version unknown or very old, skipping ...")
                return

            # may be "dangerous" if someone messes our config file
            for var in settings:
                if var in ['set_patches', 'download_GFS', 'download_WAFS']:
                    # overwriting older plugin version values
                    continue
                elif var in self.__dict__:
                    self.__dict__[var] = settings[var]

    def save_plugin_settings(self) -> None:
        """Save plugin settings"""
        settings = {
            'version': self.__VERSION__,
            'enabled': self.enabled,
            'metar_decode': self.metar_decode,
            'updaterate': self.updaterate,
            'set_temp': self.set_temp,
            'set_clouds': self.set_clouds,
            'set_wind': self.set_wind,
            'set_turb': self.set_turb,
            'set_pressure': self.set_pressure,
            'set_tropo': self.set_tropo,
            'set_thermals': self.set_thermals,
            'set_surface_layer': self.set_surface_layer,
            'set_snow': self.set_snow,
            'set_patches': self.set_patches,
            'set_friction': self.set_friction,
            'opt_clouds_update': self.opt_clouds_update,
            'metar_source': self.metar_source,
            'download_GFS': self.download_GFS,
            'download_WAFS': self.download_WAFS,
            'metar_agl_limit': self.metar_agl_limit,
            'metar_distance_limit': self.metar_distance_limit,
            'max_visibility': self.max_visibility,
            'max_cloud_height': self.max_cloud_height,
            'turbulence_probability': self.turbulence_probability,
            'metar_updaterate': self.metar_updaterate,
            'ignore_metar_stations': self.ignore_metar_stations,
            'metar_ignore_auto': self.metar_ignore_auto,
            'info_window_position': self.info_window_position,
            'metar_window_position': self.metar_window_position,
            'config_window_position': self.config_window_position
        }
        self.save_settings(self.settings_file, settings)

    def load_plugin_settings(self) -> None:
        self.load_settings(self.settings_file)

        if self.metar_source == 'NOAA':
            self.metar_updaterate = 5
        else:
            self.metar_updaterate = 10

    def save_server_settings(self) -> None:
        """Save weather server settings"""
        server_conf = {
            'version': self.__VERSION__,
            'lastgrib': self.lastgrib,
            'lastwafsgrib': self.lastwafsgrib,
            'ms_update': self.ms_update,
            'weather_server_pid': self.weather_server_pid,
        }
        self.save_settings(self.server_settings_file, server_conf)

    def load_server_settings(self) -> None:
        self.load_settings(self.server_settings_file)

        # Load the GFS levels file or create a new one.
        if self.gfs_levels_file.is_file():
            self.gfs_levels = self.load_gfs_levels(self.gfs_levels_file)
        else:
            self.gfs_levels = self.gfs_levels_defaults()
            self.save_gfs_levels(self.gfs_levels)
        log.debug(f"XP12 Real Weather Mode: {self.use_real_weather_data}")

    @staticmethod
    def gfs_levels_defaults() -> list[dict]:
        """GFS Levels default config"""
        d = [
                {
                    "vars": [
                        "PRES",
                        "TMP",
                        "HGT",
                        "SNOD",
                        "APCP"
                    ],
                    "levels": "surface"
                },
                {
                    "vars": [
                        "TMP",
                        "UGRD",
                        "VGRD"
                    ],
                    "levels": [
                        "900 mb",
                        "800 mb",
                        "700 mb",
                        "600 mb",
                        "500 mb",
                        "400 mb",
                        "300 mb",
                        "250 mb",
                        "200 mb"
                    ]
                },
                {
                    "vars": "PRES",
                    "levels": [
                        "low cloud bottom level",
                        "low cloud top level",
                        "middle cloud bottom level",
                        "middle cloud top level",
                        "high cloud bottom level",
                        "high cloud top level"
                    ]
                },
                {
                    "vars": "LCDC",
                    "levels": "low cloud layer"
                },
                {
                    "vars": "MCDC",
                    "levels": "middle cloud layer"
                },
                {
                    "vars": "HCDC",
                    "levels": "high cloud layer"
                },
                {
                    "vars": "PRMSL",
                    "levels": "mean sea level"
                },
                {
                    "vars": [
                        "PRES",
                        "TMP"
                    ],
                    "levels": "tropopause"
                }
        ]
        return d

    @staticmethod
    def gfs_levels_real_weather() -> list[dict]:
        """GFS surface data that still are missing in Real Weather"""
        # not sure if we'll ever need them

        d = [
                {
                    "vars": [
                        "PRES",
                        "TMP",
                        "APCP"
                    ],
                    "levels": "surface"
                }
        ]
        return d

    @staticmethod
    def wafs_levels_default() -> list[dict]:
        """WAFS Levels default config"""
        d = [
                {
                    "vars": [
                        "ICESEV",
                        "EDPARM"
                    ],
                    "levels": [
                                "1000 mb",
                                "950 mb",
                                "900 mb",
                                "800 mb",
                                "700 mb",
                                "600 mb",
                                "500 mb",
                                "400 mb",
                                "300 mb",
                                "250 mb",
                                "200 mb",
                                "150 mb",
                                "100 mb"
                    ]
                }
        ]
        return d

    @staticmethod
    def wafs_levels_real_weather() -> list[dict]:
        """WAFS Levels 
        Real Weather has WAFS data, so there is no need to download it from NOAA"""
        d = [
        ]
        return d

    def save_gfs_levels(self, levels: list[dict]) -> None:
        """Save gfs levels settings to a json file"""
        with open(self.gfs_levels_file, 'w', encoding='UTF-8') as f:
            config = {
                'comment': [line.strip() for line in iter(self.GFS_JSON_HELP.splitlines())],
                'config': levels,
            }
            level = c.gfs_levels_help_list()
            config['comment'] += [' | '.join(level[i:i + 5]) for i in range(0, len(level), 5)]
            json.dump(config, f, indent=2)

    def load_gfs_levels(self, json_file: Path) -> list[dict]:
        """Load gfs levels configuration from a json file"""

        with open(json_file, 'r', encoding='UTF-8') as f:
            try:
                return json.load(f)['config']
            except (json.JSONDecodeError, KeyError, TypeError):
                log.exception("Format ERROR parsing gfs levels file")
            except OSError:
                log.exception("I/O ERROR reading gfs levels file")
            except Exception:
                log.exception("Unexpected ERROR loading gfs levels file")
            return self.gfs_levels_defaults()

    @staticmethod
    def can_exec(file_path: Path) -> bool:
        return file_path.is_file() and os.access(file_path, os.X_OK)

    @staticmethod
    def version_tuple(version: str) -> tuple[int, int, int]:
        """'12.4.4-beta.3' -> (12, 4, 4): version strings can't be compared as text ('3.5' > '12.0')"""
        numbers = [int(n) for n in re.findall(r'\d+', str(version))] + [0, 0, 0]
        return tuple(numbers[:3])
