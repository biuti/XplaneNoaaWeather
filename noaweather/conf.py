"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2023 Antonio Golfari
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
import pickle as cPickle

from pathlib import Path

from . import c


class Conf:
    """Loads and saves configuration variables"""
    syspath, dirsep = '', os.sep
    printableChars = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~ '

    __VERSION__ = '12.0.4-beta2'

    GFS_JSON_HELP = '''Here you can edit which wind levels will be downloaded from NOAA without hacking the code.
                    Keep the list short to optimize the download size and parsing times.
                    If you mess-up just remove this file, a new one will be created with default values.
                    
                    For a full list of levels check:
                    https://www.nco.ncep.noaa.gov/pmb/products/gfs/gfs.t00z.pgrb2.0p50.f003.shtml
                    Remove the current cycle from the cache/gfs to trigger a download with new values.
                        
                    Refer to the following list for millibar Flight Level conversion:'''

    def __init__(self, xplane_path=False):
        """The plugin uses wgrib2 utility to decode GFS files
        - Compiled version: v3.1.0"""

        if xplane_path:
            self.syspath = xplane_path
            self.respath = Path(xplane_path, 'Resources', 'plugins', 'PythonPlugins', 'noaweather')
        else:
            self.respath = Path(__file__).resolve().parent
            self.syspath = self.respath.parents[3]

        self.wpath = Path(self.syspath, 'Output', 'real weather')
        self.settingsfile = Path(self.respath, 'settings.pkl')
        self.serverSettingsFile = Path(self.respath, 'weatherServer.pkl')
        self.gfsLevelsFile = Path(self.respath, 'gfs_levels_config.json')

        self.cachepath = Path(self.respath, 'cache')
        self.cachepath.mkdir(parents=True, exist_ok=True)

        self.dbfile = Path(self.cachepath, 'metar', 'metar.db')
        self.dbfile.parent.mkdir(parents=True, exist_ok=True)

        self.setDefaults()
        self.pluginLoad()
        self.serverLoad()

        # verbose mode
        self.verbose = any(el in self.__VERSION__ for el in ["alpha", "beta"])

        # Config Overrides
        self.parserate = 1
        # self.metar_agl_limit = 10

        # Selects the apropiate wgrib binary
        self.platform, _, self.version = [c.float_or_lower(el) for el in platform.uname()[:3]]
        self.spinfo = False
        self.wgrib2bin = None

        wgbin = False
        if self.platform == 'darwin' and self.version >= 18.0:  # Mojave and above
            # 18.0 Mojave (MacOS 10.14)
            # 19.0 Catalina (MacOS 10.15)
            # 20.0 Big Sur (MacOS 16.0)
            # 21.0 Monterey (MacOS 12.0)
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
            self.wgrib2bin = Path(self.respath, 'bin', wgbin)
            # Enforce execution rights
            try:
                self.wgrib2bin.chmod(0o775)
            except:
                pass

        self.meets_wgrib2_requirements = wgbin is not False

    @property
    def gfs_variable_list(self) -> dict:
        return self.gfs_levels_real_weather() if self.real_weather_enabled else self.gfs_levels

    @property
    def wafs_variable_list(self) -> dict:
        return self.wafs_levels_real_weather() if self.real_weather_enabled else self.gfs_levels

    def setDefaults(self):
        """Default settings"""
        print(f"Loading defaults settings ...")

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
        self.xpClouds = {
            'CIRRUS': [1, c.f2m(1000)],
            'FEW': [2, c.f2m(2000)],
            'SCT': [3, c.f2m(4000)],
            'BKN': [4, c.f2m(4000)],
            'OVC': [5, c.f2m(4000)],
            'VV': [6, c.f2m(6000)]
        }

        # Minimum redraw difference per layer (legacy cloud layers procedure)
        self.minRedraw = [
            c.f2m(500),
            c.f2m(5000),
            c.f2m(10000)
        ]

        # User settings
        self.enabled = True

        self.metar_decode = False
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

        self.download_METAR = True

        # Waiting API SDK to implement automatic mode switch
        self.real_weather_enabled = True

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
        self.surface_wind_layer_limit = 600  # In meters over first GFS layer for smooth  transition

        self.parserate = 1
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

        self.weatherServerPid = False

        # Surface wind random variability range
        self.maxRandomWindHdg = 5  # degrees
        self.maxRandomWindGust = 5  # kt

        # Max Turbulence (4 = severe turbulence)
        self.max_turbulence = 4

        # Transitions
        self.windTransSpeed = 0.5  # kt/s
        self.windGustTransSpeed = 1  # kt/s
        self.windHdgTransSpeed = 1  # degrees/s

        self.metar_source = 'NOAA'
        self.metar_updaterate = 5  # minutes
        self.metar_ignore_auto = False
        self.metar_use_xp12 = False

        self.ignore_metar_stations = []

        self.updateMetarRWX = True

    def saveSettings(self, filepath: Path, settings: dict):
        print(f"Saving Settings to {filepath.name}")
        f = open(filepath, 'wb')
        cPickle.dump(settings, f)
        f.close()

    def loadSettings(self, filepath: Path):
        if filepath.is_file():
            f = open(filepath, 'rb')
            try:
                conf = cPickle.load(f)
                f.close()
            except:
                # Corrupted settings, remove file
                print(f"{filepath.name}: Corrupted file, deleting ...")
                filepath.unlink()
                return

            # print(f"Conf settings Version: {conf['version']}")
            # Reset settings on different versions.
            if 'version' not in conf or conf['version'] < '12.0.0':
                print(f"Version unknown or very old, skipping ...")
                return

            # may be "dangerous" if someone messes our config file
            for var in conf:
                if var in self.__dict__:
                    self.__dict__[var] = conf[var]

    def pluginSave(self):
        """Save plugin settings"""
        conf = {
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
            'opt_clouds_update': self.opt_clouds_update,
            'metar_source': self.metar_source,
            'download_METAR': self.download_METAR,
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
            'metar_use_xp12': self.metar_use_xp12,
        }
        self.saveSettings(self.settingsfile, conf)

    def pluginLoad(self):
        self.loadSettings(self.settingsfile)

        if self.metar_source == 'NOAA':
            self.metar_updaterate = 5
        else:
            self.metar_updaterate = 10

    def serverSave(self):
        """Save weather server settings"""
        server_conf = {
            'version': self.__VERSION__,
            'lastgrib': self.lastgrib,
            'lastwafsgrib': self.lastwafsgrib,
            'ms_update': self.ms_update,
            'weatherServerPid': self.weatherServerPid,
        }
        self.saveSettings(self.serverSettingsFile, server_conf)

    def serverLoad(self):
        self.pluginLoad()
        self.loadSettings(self.serverSettingsFile)

        # Load the GFS levels file or create a new one.
        if self.gfsLevelsFile.is_file():
            self.gfs_levels = self.load_gfs_levels(self.gfsLevelsFile)
        else:
            self.gfs_levels = self.gfs_levels_defaults()
            self.save_gfs_levels(self.gfs_levels)
        print(f"XP12 Real Weather Mode: {self.real_weather_enabled}")

    @staticmethod
    def gfs_levels_defaults() -> list:
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
    def gfs_levels_real_weather() -> list:
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
                }
        ]
        return d

    @staticmethod
    def wafs_levels_default() -> list:
        """GFS Levels default config"""
        d = [
                {
                    "vars": [
                        "ICESEV",
                        "EDPARM"
                    ],
                    "levels": [
                                "900 mb",
                                "800 mb",
                                "700 mb",
                                "600 mb",
                                "500 mb",
                                "400 mb",
                                "350 mb",
                                "300 mb",
                                "250 mb",
                                "200 mb",
                                "150 mb"
                    ]
                }
        ]
        return d

    @staticmethod
    def wafs_levels_real_weather() -> list:
        """GFS Levels 
        At the moment gfs.t00z.awf_0p25.fFFF.grib2 do not permit partial download. Empty list will use the whole file"""
        d = [
        ]
        return d

    def save_gfs_levels(self, levels: list):
        """Save gfs levels settings to a json file"""
        with open(self.gfsLevelsFile, 'w', encoding='UTF-8') as f:
            config = {
                'comment': [line.strip() for line in iter(self.GFS_JSON_HELP.splitlines())],
                'config': levels,
            }
            level = c.gfs_levels_help_list()
            config['comment'] += [' | '.join(level[i:i + 5]) for i in range(0, len(level), 5)]
            json.dump(config, f, indent=2)

    def load_gfs_levels(self, json_file: Path) -> list:
        """Load gfs levels configuration from a json file"""

        print(f"Trying to locate gfs jsonfile {json_file.name}")
        with open(json_file, 'r', encoding='UTF-8') as f:
            try:
                return json.load(f)['config']
            except (KeyError, Exception) as err:
                print(f"Format ERROR parsing gfs levels file: {err}")
                return self.gfs_levels_defaults()

    @staticmethod
    def can_exec(file_path: Path) -> bool:
        return file_path.is_file() and os.access(file_path, os.X_OK)
