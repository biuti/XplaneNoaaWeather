"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2021-2024 Antonio Golfari
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

        self.wheels_on_ground = EasyDref('sim/flightmodel2/gear/on_ground[0:10]', 'int')

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
        self.xp_weather_source = EasyDref('sim/weather/region/weather_source', 'int')

        self.msltemp = EasyDref('sim/weather/region/sealevel_temperature_c', 'float')
        self.temp = EasyDref('sim/weather/aircraft/temperature_ambient_deg_c', 'float')
        self.visibility = EasyDref('sim/weather/aircraft/visibility_reported_sm', 'float')
        self.pressure = EasyDref('sim/weather/region/sealevel_pressure_pas', 'float')  # Pascal, it was inHg in XP11
        self.wind_dir = EasyDref('sim/weather/aircraft/wind_now_direction_degt', 'float')
        self.wind_spd = EasyDref('sim/weather/aircraft/wind_now_speed_msc', 'float')  #msc

        self.precipitation = EasyDref('sim/weather/region/rain_percent', 'float')
        self.runwayFriction = EasyDref('sim/weather/region/runway_friction', 'float')

        # snow coverage, this are private dref for some reason cannot be initialized at start
        self.snow_cover = None # 1.25 to 0.01
        self.puddles = None  # 1.25 to 0.01
        self.iced_tarmac = None  # 2 to 0.01

        self.frozen_water = None   # default 0
        self.tarmac_snow_width = None  # default 0.25 | 0 no snow on tarmac | 1 full | values should go 0.6 | 0.4 | 0.15
        self.tarmac_snow_scale = None  # default 500 | values should go 500 | 300 | 100
        self.tarmac_snow_noise = None  # default 0.04 | 0 uniform snow cover on tarmac | 1 very defined patches | values should go 0.2 | 0.1 | 0.05
        # self.rain_force_factor = EasyDref('sim/private/controls/rain/force_factor', 'float', writable=True)

        self.thermals_rate = EasyDref('sim/weather/region/thermal_rate_ms', 'float')  # seems ft/m 0 - 1000
        self.mag_deviation = EasyDref('sim/flightmodel/position/magnetic_variation', 'float')
        self.acf_vy = EasyDref('sim/flightmodel/position/local_vy', 'float')

        # print(self.dump())

    @property
    def real_weather_enabled(self) -> bool:
        return True if not self.xp_weather_source else self.xp_weather_source.value == 1

    @property
    def on_ground(self) -> bool:
        return any(self.wheels_on_ground.value)

    def check_snow_dref(self) -> bool:
        if not self.snow_cover or not self.snow_cover.value:
            try:
                self.snow_cover = EasyDref('sim/private/controls/wxr/snow_now', 'float', writable=True)
                self.frozen_water = EasyDref('sim/private/controls/snow/luma_b', 'float', writable=True, default_value=0)
                self.tarmac_snow_width = EasyDref('sim/private/controls/twxr/snow_area_width', 'float', writable=True, default_value=0.25)
                self.tarmac_snow_scale = EasyDref('sim/private/controls/twxr/snow_area_scale', 'float', writable=True, default_value=500)
                self.tarmac_snow_noise = EasyDref('sim/private/controls/twxr/snow/noise_depth', 'float', writable=True, default_value=0.04)
                self.puddles = EasyDref('sim/private/controls/wxr/puddles_now', 'float', writable=True)
                self.iced_tarmac = EasyDref('sim/private/controls/wxr/ice_now', 'float', writable=True)
            except SystemError as e:
                print(f"ERROR initializing snow drefs: {e}")
                return False
        return True

    def set_snow_defaults(self):
        try:
            self.tarmac_snow_width.set_default()
            self.tarmac_snow_scale.set_default()
            self.tarmac_snow_noise.set_default()
            self.frozen_water.set_default()
        except SystemError as e:
            print(f"ERROR resetting snow drefs to default values: {e}")

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
