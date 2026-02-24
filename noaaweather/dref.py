"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2021-2026 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

from . import find_dataref


class Dref:
    """
    Plugin dataref data binding and publishing
    """

    def __init__(self) -> None:

        self.registered = False
        self.registerTries = 0

        '''
        Bind datarefs
        '''

        # XP12 Version
        self.xp_version = find_dataref('sim/version/xplane_internal_version')

        # Position
        self.latdr = find_dataref('sim/flightmodel/position/latitude')
        self.londr = find_dataref('sim/flightmodel/position/longitude')
        self.altdr = find_dataref('sim/flightmodel/position/elevation')
        self.gsdr = find_dataref('sim/flightmodel/position/groundspeed')
        self.dirdr = find_dataref('sim/flightmodel/position/hpath')

        self.wheels_on_ground = find_dataref('sim/flightmodel2/gear/on_ground')

        # wind dataref are array[13] in XP12
        self.winds = {
            'alt': find_dataref('sim/weather/region/wind_altitude_msl_m'),
            'hdg': find_dataref('sim/weather/region/wind_direction_degt'),
            'speed': find_dataref('sim/weather/region/wind_speed_msc'),  # m/s, it was kt in XP11
            'gust_hdg': find_dataref('sim/weather/region/shear_direction_degt'),
            'gust': find_dataref('sim/weather/region/shear_speed_msc'),  # m/s, it was kt in XP11
            'turb': find_dataref('sim/weather/region/turbulence'),
            'temp': find_dataref('sim/weather/region/temperatures_aloft_deg_c'),
            'dewp': find_dataref('sim/weather/region/dewpoint_deg_c')
        }

        # cloud dataref are array[3] in XP12
        self.clouds = {
            'top': find_dataref('sim/weather/region/cloud_tops_msl_m'),
            'bottom': find_dataref('sim/weather/region/cloud_base_msl_m'),
            'coverage': find_dataref('sim/weather/region/cloud_coverage_percent'),
            'type': find_dataref('sim/weather/region/cloud_type'),
        }

        # XP Time
        self.xpTime = find_dataref('sim/time/local_time_sec')  # sim time (sec from midnight)

        # What system is currently controlling the weather. 0 = Preset, 1 = Real Weather, 2 = Controlpad, 3 = Plugin.
        self.xp_weather_source = find_dataref('sim/weather/region/weather_source')

        self.msltemp = find_dataref('sim/weather/region/sealevel_temperature_c')
        self.temp = find_dataref('sim/weather/aircraft/temperature_ambient_deg_c')
        self.visibility = find_dataref('sim/weather/aircraft/visibility_reported_sm')
        self.pressure = find_dataref('sim/weather/region/qnh_pas')  # Pascal, it was inHg in XP11
        self.wind_dir = find_dataref('sim/weather/aircraft/wind_now_direction_degt')
        self.wind_spd = find_dataref('sim/weather/aircraft/wind_now_speed_msc')  #msc

        self.precipitation = find_dataref('sim/weather/region/rain_percent')
        self.runwayFriction = find_dataref('sim/weather/region/runway_friction')

        # snow coverage, this are private dref for some reason cannot be initialized at start
        self.snow_override = None  # default 0, 1 if snow cover is active
        self.snow_cover = None # from 0 (no snow) to 1 (full snow) in XP version 12.4
        self.puddles = None  # from 0 (no puddles) to 1 (full puddles) in XP version 12.4
        self.iced_tarmac = None  # from 0 (no ice) to 1 (full ice) in XP version 12.4

        self.frozen_water = None   # default 0 (no frozen water) to 1000 (full frozen water)
        self.tarmac_snow_width = None  # default 0.25 | 0 no snow on tarmac | 1 full | values should go 0.6 | 0.4 | 0.15
        self.tarmac_snow_scale = None  # default 500 | values should go 500 | 300 | 100
        self.tarmac_snow_noise = None  # default 0.04 | 0 uniform snow cover on tarmac | 1 very defined patches | values should go 0.2 | 0.1 | 0.05
        # self.rain_force_factor = find_dataref('sim/private/controls/rain/force_factor')

        self.thermals_rate = find_dataref('sim/weather/region/thermal_rate_ms')  # seems ft/m 0 - 1000
        self.mag_deviation = find_dataref('sim/flightmodel/position/magnetic_variation')
        self.acf_vy = find_dataref('sim/flightmodel/position/local_vy')

        # print(self.dump())

    @property
    def real_weather_enabled(self) -> bool:
        return self.xp_weather_source.value == 1

    @property
    def on_ground(self) -> bool:
        return any(self.wheels_on_ground.value)

    @property
    def groundspeed(self) -> bool:
        return self.gsdr.value

    @property
    def track(self) -> bool:
        return self.dirdr.value

    def check_snow_dref(self) -> bool:
        if self.snow_cover is None or not hasattr(self.snow_cover, 'value'):
            try:
                self.snow_override = find_dataref('sim/private/controls/weather/allow_initial_snow_coverage')
                self.snow_cover = find_dataref('sim/private/controls/wxr/snow_now')
                self.frozen_water = find_dataref('sim/private/controls/snow/luma_b')
                self.tarmac_snow_width = find_dataref('sim/private/controls/twxr/snow_area_width')
                self.tarmac_snow_scale = find_dataref('sim/private/controls/twxr/snow_area_scale')
                self.tarmac_snow_noise = find_dataref('sim/private/controls/twxr/snow/noise_depth')
                self.puddles = find_dataref('sim/private/controls/wxr/puddles_now')
                self.iced_tarmac = find_dataref('sim/private/controls/wxr/ice_now')
            except (SystemError, Exception) as e:
                print(f"ERROR initializing snow drefs: {e}")
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
