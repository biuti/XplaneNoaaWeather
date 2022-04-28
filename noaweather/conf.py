"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2012-2020 Joan Perez i Cauhe
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

from . import c


class Conf:
    """Loads and saves configuration variables"""
    syspath, dirsep = '', os.sep
    printableChars = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~ '

    __VERSION__ = '3.0.0'

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
            self.respath = os.sep.join([xplane_path, 'Resources', 'plugins', 'PythonPlugins', 'noaweather'])
        else:
            self.respath = os.path.dirname(os.path.abspath(__file__))

        self.settingsfile = os.sep.join([self.respath, 'settings.pkl'])
        self.serverSettingsFile = os.sep.join([self.respath, 'weatherServer.pkl'])
        self.gfsLevelsFile = os.sep.join([self.respath, 'gfs_levels_config.json'])

        self.cachepath = os.sep.join([self.respath, 'cache'])
        if not os.path.exists(self.cachepath):
            os.makedirs(self.cachepath)

        self.setDefaults()
        self.pluginLoad()
        self.serverLoad()

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
            # Hide wgrib window for windows users
            self.spinfo = subprocess.STARTUPINFO()
            self.spinfo.dwFlags |= 1  # STARTF_USESHOWWINDOW
            self.spinfo.wShowWindow = 0  # 0 or SW_HIDE 0

        elif self.platform == 'linux' and self.version >= 4.0:  # Kernel 4.0 (Ubuntu 16.04) and above
            # Linux
            wgbin = 'linux-wgrib2'  # compiled in Ubuntu 20.04 LTS

        if wgbin:
            self.wgrib2bin = os.sep.join([self.respath, 'bin', wgbin])
            # Enforce execution rights
            try:
                os.chmod(self.wgrib2bin, 0o775)
            except:
                pass

        self.meets_wgrib2_requirements = wgbin is not False

    def setDefaults(self):
        """Default settings"""

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
        self.minRedraw = [c.f2m(500),
                          c.f2m(5000),
                          c.f2m(10000)]

        # User settings
        self.enabled = True
        self.set_wind = True
        self.set_tropo = True
        self.set_clouds = True
        self.opt_clouds_update = True
        self.set_temp = True
        self.set_visibility = False
        self.set_turb = True
        self.set_pressure = True
        self.set_thermals = True
        self.set_surface_layer = True
        self.turbulence_probability = 1

        self.inputbug = False

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
        self.download = True
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

        self.tracker_uid = False
        self.tracker_enabled = True

        self.ignore_metar_stations = []

        self.updateMetarRWX = True

    def saveSettings(self, filepath, settings):
        print("Saving Settings to {}".format(filepath))
        f = open(filepath, 'wb')
        cPickle.dump(settings, f)
        f.close()

    def loadSettings(self, filepath):
        if os.path.exists(filepath):
            f = open(filepath, 'rb')
            try:
                conf = cPickle.load(f)
                f.close()
            except:
                # Corrupted settings, remove file
                os.remove(filepath)
                return

            # Reset settings on different versions.
            if 'version' not in conf or conf['version'] < '2.0':
                return

            # may be "dangerous" if someone messes our config file
            for var in conf:
                if var in self.__dict__:
                    self.__dict__[var] = conf[var]

            # Versions config overrides
            if 'version' in conf:
                if conf['version'] < '2.3.1':
                    # Enforce metar station update
                    self.ms_update = 0
                if conf['version'] < '2.4.0':
                    # Clean ignore stations
                    self.ignore_metar_stations = []
                if conf['version'] < '2.4.3':
                    self.inputbug = True

    def pluginSave(self):
        """Save plugin settings"""
        conf = {
            'version': self.__VERSION__,
            'set_temp': self.set_temp,
            'set_clouds': self.set_clouds,
            'set_wind': self.set_wind,
            'set_turb': self.set_turb,
            'set_pressure': self.set_pressure,
            'set_tropo': self.set_tropo,
            'set_thermals': self.set_thermals,
            'set_surface_layer': self.set_surface_layer,
            'opt_clouds_update': self.opt_clouds_update,
            'enabled': self.enabled,
            'updaterate': self.updaterate,
            'metar_source': self.metar_source,
            'download': self.download,
            'metar_agl_limit': self.metar_agl_limit,
            'metar_distance_limit': self.metar_distance_limit,
            'max_visibility': self.max_visibility,
            'max_cloud_height': self.max_cloud_height,
            'turbulence_probability': self.turbulence_probability,
            'inputbug': self.inputbug,
            'metar_updaterate': self.metar_updaterate,
            'tracker_uid': self.tracker_uid,
            'tracker_enabled': self.tracker_enabled,
            'ignore_metar_stations': self.ignore_metar_stations,
            'metar_ignore_auto': self.metar_ignore_auto
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
        if os.path.isfile(self.gfsLevelsFile):
            self.gfs_variable_list = self.load_gfs_levels(self.gfsLevelsFile)
        else:
            self.gfs_variable_list = self.gfs_levels_defaults()
            self.save_gfs_levels(self.gfs_variable_list)

    @staticmethod
    def gfs_levels_defaults():
        """GFS Levels default config"""
        d = [
                {
                    "vars": [
                        "PRES",
                        "TMP",
                        "HGT"
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
                        "850 mb",
                        "700 mb",
                        "600 mb",
                        "500 mb",
                        "400 mb",
                        "300 mb",
                        "200 mb",
                        "150 mb"
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

    def save_gfs_levels(self, levels):
        """Save gfs levels settings to a json file"""
        with open(self.gfsLevelsFile, 'w') as f:
            config = {'comment': [line.strip() for line in iter(self.GFS_JSON_HELP.splitlines())],
                      'config': levels,
                      }
            level = c.gfs_levels_help_list()
            config['comment'] += [' | '.join(level[i:i + 5]) for i in range(0, len(level), 5)]
            json.dump(config, f, indent=2)

    def load_gfs_levels(self, json_file):
        """Load gfs levels configuration from a json file"""

        print("Trying to locate gfs jsonfile {}".format(json_file))
        with open(json_file, 'r') as f:
            try:
                return json.load(f)['config']
            except (KeyError, Exception) as err:
                print("Format ERROR parsing gfs levels file: %s" % str(err))
                return self.gfs_levels_defaults()

    @staticmethod
    def can_exec(file_path):
        return os.path.isfile(file_path) and os.access(file_path, os.X_OK)

    @staticmethod
    def find_in_path(filename, path_separator=':'):
        if 'PATH' in os.environ:
            for path in os.environ['PATH'].split(path_separator):
                full_path = os.path.sep.join([path, filename])
                if Conf.can_exec(full_path):
                    return full_path
        return False
