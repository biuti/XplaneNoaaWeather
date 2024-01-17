"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2021-2023 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

from .easydref import EasyDref


class Dref:
    """
    Plugin dataref data binding and publishing
    """

    def __init__(self):

        self.registered = False
        self.registerTries = 0

        '''
        Bind datarefs
        '''

        # Position
        self.latdr = EasyDref('sim/flightmodel/position/latitude', 'double')
        self.londr = EasyDref('sim/flightmodel/position/longitude', 'double')
        self.altdr = EasyDref('sim/flightmodel/position/elevation', 'double')

        # wind dataref are array[13] in XP12
        self.winds = {
            'alt': EasyDref('sim/weather/region/wind_altitude_msl_m[0:12]', 'float', writable=True),
            'hdg': EasyDref('sim/weather/region/wind_direction_degt[0:12]', 'float', writable=True),
            'speed': EasyDref('sim/weather/region/wind_speed_msc[0:12]', 'float', writable=True),  # m/s, it was kt in XP11
            'gust_hdg': EasyDref('sim/weather/region/shear_direction_degt[0:12]', 'float', writable=True),
            'gust': EasyDref('sim/weather/region/shear_speed_msc[0:12]', 'float', writable=True),  # m/s, it was kt in XP11
            'turb': EasyDref('sim/weather/region/turbulence[0:12]', 'float', writable=True),
            'temp': EasyDref('sim/weather/region/temperatures_aloft_deg_c[0:12]', 'float', writable=True),
            'dewp': EasyDref('sim/weather/region/dewpoint_deg_c[0:12]', 'float', writable=True)
        }

        # cloud dataref are array[3] in XP12
        self.clouds = {
            'top': EasyDref('sim/weather/region/cloud_tops_msl_m[0:2]', 'float'),
            'bottom': EasyDref('sim/weather/region/cloud_base_msl_m[0:2]', 'float'),
            'coverage': EasyDref('sim/weather/region/cloud_coverage_percent[0:2]', 'float'),
            'type': EasyDref('sim/weather/region/cloud_type[0:2]', 'int'),
        }

        # XP Time
        self.xpTime = EasyDref('sim/time/local_time_sec', 'float')  # sim time (sec from midnight)

        # What system is currently controlling the weather. 0 = Preset, 1 = Real Weather, 2 = Controlpad, 3 = Plugin.
        self.xpWeather = EasyDref('sim/weather/region/weather_source', 'int')

        self.msltemp = EasyDref('sim/weather/region/sealevel_temperature_c', 'float')
        self.temp = EasyDref('sim/weather/aircraft/temperature_ambient_deg_c', 'float')
        self.visibility = EasyDref('sim/weather/aircraft/visibility_reported_sm', 'float')
        self.pressure = EasyDref('sim/weather/region/sealevel_pressure_pas', 'float')  # Pascal, it was inHg in XP11

        self.precipitation = EasyDref('sim/weather/region/rain_percent', 'float')
        self.runwayFriction = EasyDref('sim/weather/region/runway_friction', 'float')

        # snow coverage, this are private dref for some reason cannot be initialized at start
        self.snow_cover = None # 1.25 to 0.01
        self.frozen_water_a = None  # default 0
        self.frozen_water_b = None   # default 0
        # self.rain_force_factor = EasyDref('sim/private/controls/rain/force_factor', 'float', writable=True)

        self.thermals_rate = EasyDref('sim/weather/region/thermal_rate_ms', 'float')  # seems ft/m 0 - 1000

        self.mag_deviation = EasyDref('sim/flightmodel/position/magnetic_variation', 'float')

        self.acf_vy = EasyDref('sim/flightmodel/position/local_vy', 'float')

        # print(self.dump())

    def check_snow_dref(self):
        if not self.snow_cover or not self.snow_cover.value:
            try:
                # print(f"snow_cover value: {self.snow_cover.value}")
                self.snow_cover = EasyDref('sim/private/controls/wxr/snow_now', 'float', writable=True)
                self.frozen_water_a = EasyDref('sim/private/controls/snow/luma_a', 'float', writable=True)
                self.frozen_water_b = EasyDref('sim/private/controls/snow/luma_b', 'float', writable=True)
            except SystemError as e:
                print(f"ERROR inizializing snow drefs: {e}")
                return False
        return True

    def dump(self) -> dict:
        # Dump winds datarefs
        datarefs = {
            'winds': self.winds,
            'clouds': self.clouds,
        }
        pdrefs = {}

        for label, data in datarefs.items():
            pdrefs[label] = {k:v.value for k, v in data.items()}

        return pdrefs

    def cleanup(self):
        EasyDref.cleanup()
