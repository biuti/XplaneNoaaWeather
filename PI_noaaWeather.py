"""
X-plane NOAA GFS weather plugin.

Development version for X-Plane 12


For support visit:
http://forums.x-plane.org/index.php?showtopic=72313

Github project page:
https://github.com/biuti/XplaneNoaaWeather

Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2022 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.
"""
import time

# X-plane includes
from XPLMDefs import *
from XPLMProcessing import *
from XPLMDataAccess import *
from XPLMUtilities import *
from XPLMPlanes import *
from XPLMNavigation import *
from XPLMPlugin import *
from XPLMMenus import *
from XPWidgetDefs import *
from XPWidgets import *
from XPStandardWidgets import *

# XPPython3 plugin
import XPPython3.xp as xp

import pickle
import socket
import threading
import subprocess
import os

from datetime import datetime
from pathlib import Path
from noaweather import EasyDref, Conf, c, EasyCommand


class Weather:
    """Sets x-plane weather from GSF parsed data"""

    alt = 0.0
    ref_winds = {}
    lat, lon, last_lat, last_lon = 99, 99, False, False

    def __init__(self, conf, data):

        self.conf = conf
        self.data = data
        self.lastMetarStation = False

        self.opt_clouds = {
            'mode': 'NA',
            'gfs_clouds': False,
            'metar_clouds': False,
            'ceiling': False,
            'OVC': False,
            'above_clouds': False,
            'layers': [],
            'cycles': 0,
            'redraw': False,
            'temp': False,
            'total_redraws': 0
        }

        '''
        Bind datarefs
        '''
        self.winds = []
        self.clouds = []
        self.turbulence = {}

        for i in range(3):
            self.winds.append({
                'alt': EasyDref(f'"sim/weather/wind_altitude_msl_m[{i}]"', 'float'),
                'hdg': EasyDref(f'"sim/weather/wind_direction_degt[{i}]"', 'float'),
                'speed': EasyDref(f'"sim/weather/wind_speed_kt[{i}]"', 'float'),
                'gust': EasyDref(f'"sim/weather/shear_speed_kt[{i}]"', 'float'),
                'gust_hdg': EasyDref(f'"sim/weather/shear_direction_degt[{i}]"', 'float'),
                'turbulence': EasyDref(f'"sim/weather/turbulence[{i}]"', 'float'),
            })

        for i in range(3):
            self.clouds.append({
                'top': EasyDref(f'"sim/weather/cloud_tops_msl_m[{i}]"', 'float'),
                'bottom': EasyDref(f'"sim/weather/cloud_base_msl_m[{i}]"', 'float'),
                'coverage': EasyDref(f'"sim/weather/cloud_coverage[{i}]"', 'float'),
                'type': EasyDref(f'"sim/weather/cloud_type[{i}]"', 'int'),
            })

        self.windata = []
        self.surface_wind = False

        self.xpTime = EasyDref('sim/time/local_time_sec', 'float')  # sim time (sec from midnight)

        # self.xpWeatherOn = EasyDref('sim/weather/use_real_weather_bool', 'int')  # deprecated
        # self.xpWeatherDownloadOn = EasyDref('sim/weather/download_real_weather', 'int')  # deprecated
        self.msltemp = EasyDref('sim/weather/temperature_sealevel_c', 'float')
        self.msldewp = EasyDref('sim/weather/dewpoi_sealevel_c', 'float')
        self.visibility = EasyDref('sim/weather/visibility_reported_m', 'float')
        self.pressure = EasyDref('sim/weather/barometer_sealevel_inhg', 'float')

        self.precipitation = EasyDref('sim/weather/rain_percent', 'float')
        self.thunderstorm = EasyDref('sim/weather/thunderstorm_percent', 'float')
        self.runwayFriction = EasyDref('sim/weather/runway_friction', 'float')
        self.patchy = EasyDref('sim/weather/runway_is_patchy', 'float')

        # self.tropo_temp = EasyDref('sim/weather/temperature_tropo_c', 'float')  # default -56.5C  deprecated
        # self.tropo_alt = EasyDref('sim/weather/tropo_alt_mtr', 'float')  # default 11100 meter  deprecated

        self.thermals_prob = EasyDref('sim/weather/thermal_percent', 'float')  # 0 - 0.25
        self.thermals_rate = EasyDref('sim/weather/thermal_rate_ms', 'float')  # seems ft/m 0 - 1000
        self.thermals_alt = EasyDref('sim/weather/thermal_altitude_msl_m', 'float')  # meters, default 10000

        self.mag_deviation = EasyDref('sim/flightmodel/position/magnetic_variation', 'float')

        self.acf_vy = EasyDref('sim/flightmodel/position/local_vy', 'float')

        # Data
        self.weatherData = False
        self.weatherClientThread = False

        self.windAlts = -1

        # Response queue for user queries
        self.queryResponses = []

        # Create client socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.die = threading.Event()
        self.lock = threading.Lock()

        self.newData = False

        self.startWeatherServer()

    def startWeatherClient(self):
        if not self.weatherClientThread:
            self.weatherClientThread = threading.Thread(target=self.weatherClient)
            self.weatherClientThread.start()

    def weatherClient(self):
        """Weather client thread fetches weather from Weather Server"""

        # Send something for windows to bind
        self.weatherClientSend('!ping')

        while True:
            received = self.sock.recv(1024 * 8)
            wdata = pickle.loads(received)
            if self.die.is_set() or wdata == '!bye':
                break
            elif 'info' not in wdata:
                # A metar query response
                self.queryResponses.append(wdata)
            else:
                self.weatherData = wdata
                self.newData = True

    def weatherClientSend(self, msg):
        if self.weatherClientThread:
            self.sock.sendto(msg.encode('utf-8'), ('127.0.0.1', self.conf.server_port))

    def startWeatherServer(self):
        DETACHED_PROCESS = 0x00000008
        args = [xp.pythonExecutable, Path(self.conf.respath, 'weatherServer.py'), self.conf.syspath]

        kwargs = {'close_fds': True}

        try:
            if self.conf.spinfo:
                kwargs.update({'startupinfo': self.conf.spinfo, 'creationflags': DETACHED_PROCESS})
            print("start weather server {} {}".format(args, kwargs))
            subprocess.Popen(args, **kwargs)
        except Exception as e:
            print("Exception while executing subprocess: {}".format(e))

    def shutdown(self):
        # Shutdown client and server
        self.weatherClientSend('!shutdown')
        self.weatherClientThread = False

    def setTurbulence(self, turbulence, elapsed):
        """Set turbulence for all wind layers with our own interpolation"""
        turb = 0

        prevlayer = False
        if len(turbulence) > 1:
            for clayer in turbulence:
                '''apply max value'''
                clayer[1] = min(clayer[1], self.conf.max_turbulence)
                if clayer[0] > self.alt:
                    # last layer
                    break
                else:
                    prevlayer = clayer
            if prevlayer:
                turb = c.interpolate(prevlayer[1], clayer[1], prevlayer[0], clayer[0], self.alt)
            else:
                turb = clayer[1]

        # set turbulence
        turb *= self.conf.turbulence_probability
        turb = c.randPattern('turbulence', turb, elapsed, 20, min_time=1)

        self.winds[0]['turbulence'].value = turb
        self.winds[1]['turbulence'].value = turb
        self.winds[2]['turbulence'].value = turb

    def setWinds(self, winds, elapsed):
        """Set winds: Interpolate layers and transition new data"""
        from random import randrange
        import math

        winds = winds[:]

        alt, hdg, speed, extra = 0, 0, 0, {'metar': False}
        # Append metar layer
        if 'metar' in self.weatherData and 'wind' in self.weatherData['metar']:
            alt = self.weatherData['metar']['elevation']  # in meters

            # Fix temperatures (legacy)
            if not self.conf.set_tropo and 'temperature' in self.weatherData['metar']:
                '''legacy temperature fix'''
                print(f"*** Legacy temperature Fix ***")
                if self.weatherData['metar']['temperature'][0] is not False:
                    temp = self.weatherData['metar']['temperature'][0] + 273.15
                    self.msltemp.value = c.oat2msltemp(temp - 273.15, self.alt)
                if self.weatherData['metar']['temperature'][1] is not False:
                    dew = self.weatherData['metar']['temperature'][1] + 273.15
                    self.msldewp.value = c.oat2msltemp(dew - 273.15, self.alt)

            hdg, speed, gust = self.weatherData['metar']['wind']
            if not gust:
                '''add random wind speed variability'''
                gust = randrange(self.conf.maxRandomWindGust)
            extra = {'gust': gust, 'metar': True}

            if 'variable_wind' in self.weatherData['metar'] and self.weatherData['metar']['variable_wind']:
                h1, h2 = self.weatherData['metar']['variable_wind']
            else:
                '''add random wind direction variability'''
                r = randrange(self.conf.maxRandomWindHdg)
                h1, h2 = hdg - r, hdg + r

            h1 %= 360
            if h1 > h2:
                var = 360 - h1 + h2
            else:
                var = h2 - h1

            hdg = h1
            extra['variation'] = c.randPattern('metar_wind_hdg', var, elapsed, min_time=20, max_time=50)

            alt += self.conf.metar_agl_limit
            alt = c.transition(alt, '0-metar_wind_alt', elapsed, 0.3048)  # 1f/s

            # remove first wind layer if is too close (for high altitude airports)
            # TODO: This can break transitions in some cases.
            if len(winds) > 1 and winds[0][0] < alt + self.conf.metar_agl_limit:
                winds.pop(0)

            winds = [[alt, hdg, speed, extra]] + winds

        elif len(winds) > 0 and self.conf.set_surface_layer:
            # create a fake surface layer from lower GFS layer
            _, hdg, speed, e = winds[0]
            extra.update(e)

            winds = [[alt, hdg, speed, extra]] + winds

        # Search current top and bottom layer:
        blayer, tlayer = False, False
        nlayers = len(winds)

        surface_layer = False
        if nlayers > 0:
            for i, w in enumerate(winds):
                if w[0] > self.alt:
                    tlayer = i
                    break
                else:
                    blayer = i

            # recalculate transition only if layers altitude differ more than 10%
            if not math.isclose(self.windAlts, int(winds[tlayer][0]), rel_tol=0.1):
                # Layer change, reset transitions
                self.windAlts = int(winds[tlayer][0])
                c.transitionClearReferences(exclude=[str(blayer), str(tlayer)])

            twind = self.transWindLayer(winds[tlayer], str(tlayer), elapsed)
            swind = twind

            if blayer is not False and blayer != tlayer:
                # We are between 2 layers, interpolate
                bwind = self.transWindLayer(winds[blayer], str(blayer), elapsed)
                rwind = self.interpolateWindLayer(twind, bwind, self.alt, blayer)

                swind = rwind
                # calculates surface layer limit if activated and conditions are satisfied
                if self.conf.set_surface_layer and nlayers > 2:
                    min_alt = winds[1][0] + self.conf.surface_wind_layer_limit
                    if self.alt > min_alt:
                        swind = [alt, hdg, speed, extra]
                        surface_layer = True

            else:
                # We are below the first layer or above the last one.
                rwind = twind

            if not (surface_layer == self.surface_wind):
                self.surface_wind = surface_layer

            # Set layers
            self.setWindLayer(0, swind)
            self.setWindLayer(1, rwind)
            self.setWindLayer(2, rwind)

            '''add surface wind shear'''
            if self.thunderstorm.value > 0:
                if self.thunderstorm.value > 0.5:
                    self.winds[0]['gust_hdg'].value = randrange(30, 60)
                elif self.thunderstorm.value > 0.25:
                    self.winds[0]['gust_hdg'].value = randrange(15, 30)
                else:
                    self.winds[0]['gust_hdg'].value = randrange(5, 15)
            else:
                self.winds[0]['gust_hdg'].value = 0

            # Force shear direction 0
            self.winds[1]['gust_hdg'].value = 0
            self.winds[2]['gust_hdg'].value = 0

    def setWindLayer(self, index, wlayer):
        alt, hdg, speed, extra = wlayer

        wind = self.winds[index]

        if 'variation' in extra:
            hdg = (hdg + extra['variation']) % 360

        wind['alt'].value, wind['hdg'].value, wind['speed'].value = alt, hdg, speed

        if 'gust' in extra:
            wind['gust'].value = extra['gust']

    def transWindLayer(self, wlayer, id, elapsed):
        """Transition wind layer values"""
        alt, hdg, speed, extra = wlayer

        hdg = c.transitionHdg(hdg, id + '-hdg', elapsed, self.conf.windHdgTransSpeed)
        speed = c.transition(speed, id + '-speed', elapsed, self.conf.windTransSpeed)

        # Extra vars
        for var in ['gust', 'rh', 'dew']:
            if var in extra:
                extra[var] = c.transition(extra[var], id + '-' + var, elapsed, self.conf.windGustTransSpeed)

        # Special cases
        if 'gust_hdg' in extra:
            extra['gust_hdg'] = 0

        return alt, hdg, speed, extra

    def setDrefIfDiff(self, dref, value, max_diff=False):
        """ Set a Dataref if the current value differs
            Returns True if value was updated """

        if max_diff is not False:
            if abs(dref.value - value) > max_diff:
                dref.value = value
                return True
        else:
            if dref.value != value:
                dref.value = value
                return True
        return False

    def interpolateWindLayer(self, wlayer1, wlayer2, current_altitude, nlayer=1):
        """Interpolates 2 wind layers
        layer array: [alt, hdg, speed, extra]"""

        if wlayer1[0] == wlayer2[0]:
            return wlayer1

        layer = [0, 0, 0, {}]

        layer[0] = current_altitude

        # weight heading interpolation using wind speed
        if (wlayer1[2] != 0 or wlayer2[2] != 0):
            expo = 2 * wlayer1[2] / (wlayer1[2] + wlayer2[2])
        else:
            expo = 1

        if nlayer:
            layer[1] = c.expoCosineInterpolateHeading(wlayer1[1], wlayer2[1], wlayer1[0], wlayer2[0],
                                                      current_altitude, expo)
            layer[2] = c.interpolate(wlayer1[2], wlayer2[2], wlayer1[0], wlayer2[0], current_altitude)
        else:
            # First layer
            layer[1] = c.expoCosineInterpolateHeading(wlayer1[1], wlayer2[1], wlayer1[0], wlayer2[0],
                                                      current_altitude, expo)
            layer[2] = c.expoCosineInterpolate(wlayer1[2], wlayer2[2], wlayer1[0], wlayer2[0], current_altitude, expo)

        if 'variation' not in wlayer1[3]:
            wlayer1[3]['variation'] = 0

        # Interpolate extras
        for key in wlayer1[3]:
            if key in wlayer2[3] and wlayer2[3][key] is not False:
                if nlayer:
                    layer[3][key] = c.interpolate(wlayer1[3][key], wlayer2[3][key], wlayer1[0], wlayer2[0],
                                                  current_altitude)
                else:
                    layer[3][key] = c.expoCosineInterpolate(wlayer1[3][key], wlayer2[3][key], wlayer1[0], wlayer2[0],
                                                            current_altitude)
            else:
                # Leave null temp and dew if we can't interpolate
                if key not in ('temp', 'dew'):
                    layer[3][key] = wlayer1[3][key]

        return layer

    def setCloudsOpt(self, ts: float):
        """Takes Cloud layers information from both GFS and nearest METAR
        Optimises GFS layers, tries to merge with METAR layers if available
        Manages XP layers and redraw based on aircraft altitude (opt. option enabled)
        At high altitude (over clouds layers) uses GFS more than METAR to limit redraws"""

        self.opt_clouds['mode'] = 'Optimised'
        self.opt_clouds['gfs_clouds'] = False
        self.opt_clouds['metar_clouds'] = False
        self.opt_clouds['ceiling'] = False
        self.opt_clouds['OVC'] = False
        self.opt_clouds['above_clouds'] = False
        self.opt_clouds['redraw'] = False
        self.opt_clouds['layers'] = []

        clouds = []

        '''XP cloud cover definition'''
        xpClouds = self.conf.xpClouds

        if 'clouds' in self.weatherData['gfs']:
            '''getting GFS cloud layers'''
            # print(f"GFS Clouds: {self.weatherData['gfs']['clouds']}")
            clouds = c.optimise_gfs_clouds(self.weatherData['gfs']['clouds'])
            # print(f"OPT. GFS Clouds: {clouds}")
            self.opt_clouds['gfs_clouds'] = True

        '''evaluate flight situation'''
        overcasted = c.is_overcasted(clouds)
        above_clouds = c.above_cloud_layers(clouds, self.alt, self.clouds)
        self.opt_clouds['OVC'] = overcasted
        self.opt_clouds['above_clouds'] = above_clouds
        if (not (above_clouds and (overcasted or len(clouds) > 1))) and 'metar' in self.weatherData:
            metar = self.weatherData['metar']
            if 'distance' in metar and metar['distance'] < self.conf.metar_distance_limit:
                '''evaluate METAR clouds'''
                # print(f"METAR {metar['icao']}: {metar['metar']}")
                self.opt_clouds['metar_clouds'] = True
                '''delete gfs layers below metar ceiling'''
                clouds = [el for el in clouds if el[0] > metar['ceiling']]
                self.opt_clouds['ceiling'] = metar['ceiling']

                if 'clouds' in metar and len(metar['clouds']):
                    for cloud in metar['clouds']:
                        '''add metar cloud layers'''
                        base, cover, extra = cloud
                        cover, thickness = xpClouds[cover][0], xpClouds[cover][1]
                        if not len(clouds) or all(not c.isclose(el[0], base, 500) for el in clouds):
                            clouds.append([base, base + thickness, cover])
                        else:
                            gfs_layer = next((el for el in clouds if c.isclose(el[0], base, 500)), None)
                            if gfs_layer and not c.above_cloud_layers(clouds, self.alt):
                                '''blending with gfs layers'''
                                gfs_layer[0] = base
                                gfs_layer[2] = cover

            '''sorting layers'''
            clouds = sorted(clouds, key=lambda x: x[0])

        if len(clouds):
            '''choosing layers based on situation'''
            clouds = c.manage_clouds_layers(clouds, self.alt, ts)
            self.opt_clouds['layers'] = [[int(el[0]/100), int(el[1]/100), el[2]] for el in clouds]
            # print(f"layers: {self.opt_clouds['layers']}")

        '''evaluating if it is necessary to redraw layers (preferring minimum redraw to layer precision)'''
        self.opt_clouds['cycles'] += 1
        if c.evaluate_clouds_redrawing(clouds, self.clouds, self.alt):
            self.opt_clouds['redraw'] = True
            self.opt_clouds['total_redraws'] += 1
            for i in range(3):
                if len(clouds) > i:
                    # print(f"redrawing...")
                    # print(f"layer {i}, XP: {self.clouds[i]['bottom'].value}, {self.clouds[i]['top'].value}, {self.clouds[i]['coverage'].value}")
                    base, top, cover = clouds[i]
                    self.clouds[i]['bottom'].value = base
                    self.clouds[i]['top'].value = top
                    self.clouds[i]['coverage'].value = cover
                else:
                    self.clouds[i]['coverage'].value = 0

        # Update datarefs
        bases = []
        tops = []
        covers = []

        for layer in clouds:
            base, top, cover = layer
            bases.append(base)
            tops.append(top)
            covers.append(cover)

        self.data.cloud_base.value = bases
        self.data.cloud_top.value = tops
        self.data.cloud_cover.value = covers

        self.weatherData['cloud_info'] = self.opt_clouds

    def setClouds(self):

        self.opt_clouds['mode'] = 'Legacy'
        self.opt_clouds['gfs_clouds'] = False
        self.opt_clouds['metar_clouds'] = False
        self.opt_clouds['OVC'] = False
        self.opt_clouds['above_clouds'] = False
        self.opt_clouds['redraw'] = False
        self.opt_clouds['layers'] = []

        if 'clouds' in self.weatherData['gfs']:
            gfsClouds = self.weatherData['gfs']['clouds']
        else:
            gfsClouds = []

        # X-Plane cloud limits
        minCloud = c.f2m(2000)
        maxCloud = c.f2m(c.limit(40000, self.conf.max_cloud_height))

        # Minimum redraw difference per layer
        minRedraw = self.conf.minRedraw

        # XP cloud cover definition
        xpClouds = self.conf.xpClouds

        lastBase = 0
        maxTop = 0
        gfsCloudLimit = c.f2m(5600)

        setClouds = []

        if self.weatherData and 'distance' in self.weatherData['metar'] \
                and self.weatherData['metar']['distance'] < self.conf.metar_distance_limit \
                and 'clouds' in self.weatherData['metar']:

            clouds = self.weatherData['metar']['clouds'][:]

            gfsCloudLimit += self.weatherData['metar']['elevation']

            for cloud in reversed(clouds):
                base, cover, extra = cloud
                top = minCloud

                if cover in xpClouds:
                    top = base + xpClouds[cover][1]
                    cover = xpClouds[cover][0]
                if cover == 5 and 'precipitation' in self.weatherData['metar'] \
                        and len(self.weatherData['metar']['precipitation']):
                    cover = 6  # create stratus instead of OVC Cumulus

                # Search for gfs equivalent layer
                for gfsCloud in gfsClouds:
                    gfsBase, gfsTop, gfsCover = gfsCloud

                    if gfsBase > 0 and gfsBase - 1500 < base < gfsTop:
                        top = base + c.limit(gfsTop - gfsBase, maxCloud, minCloud)
                        break

                if lastBase and top > lastBase:
                    top = lastBase
                lastBase = base

                setClouds.append([base, top, cover])

                if not maxTop:
                    maxTop = top

            # add gfs clouds
            for cloud in gfsClouds:
                base, top, cover = cloud

                if len(setClouds) < 3 and base > max(gfsCloudLimit, maxTop):
                    cover = c.cc2xp(cover, base)

                    top = base + c.limit(top - base, maxCloud, minCloud)
                    setClouds = [[base, top, cover]] + setClouds

        else:
            # GFS-only clouds
            for cloud in reversed(gfsClouds):
                base, top, cover = cloud
                cover = c.cc2xp(cover, base)

                if cover > 0 and base > 0 and top > 0:
                    if cover < 3:
                        top = base + minCloud
                    else:
                        top = base + c.limit(top - base, maxCloud, minCloud)

                    if lastBase > top:
                        top = lastBase
                    setClouds.append([base, top, cover])
                    lastBase = base

        # Set the Cloud to Datarefs
        redraw = 0
        nClouds = len(setClouds)
        setClouds = list(reversed(setClouds))

        # Push up gfs clouds to prevent redraws
        if nClouds:
            if nClouds < 3 and setClouds[0][0] > gfsCloudLimit:
                setClouds = [[0, minCloud, 0]] + setClouds
            if 1 < len(setClouds) < 3 and setClouds[1][2] > gfsCloudLimit:
                setClouds = [setClouds[0], [setClouds[0][2], setClouds[0][2] + minCloud, 0], setClouds[1]]

        nClouds = len(setClouds)

        if not self.data.override_clouds.value:
            self.opt_clouds['cycles'] += 1
            self.opt_clouds['layers'] = [[int(el[0] / 100), int(el[1] / 100), el[2]] for el in setClouds]
            for i in range(3):
                if nClouds > i:
                    base, top, cover = setClouds[i]
                    redraw += self.setDrefIfDiff(self.clouds[i]['bottom'], base, minRedraw[i] + self.alt / 10)
                    redraw += self.setDrefIfDiff(self.clouds[i]['top'], top, minRedraw[i] + self.alt / 10)
                    redraw += self.setDrefIfDiff(self.clouds[i]['coverage'], cover)
                else:
                    redraw += self.setDrefIfDiff(self.clouds[i]['coverage'], 0)

        # Update datarefs
        bases = []
        tops = []
        covers = []

        for layer in setClouds:
            base, top, cover = layer
            bases.append(base)
            tops.append(top)
            covers.append(cover)

        self.data.cloud_base.value = bases
        self.data.cloud_top.value = tops
        self.data.cloud_cover.value = covers

        self.weatherData['cloud_info'] = self.opt_clouds

    def setPressure(self, pressure, elapsed):
        c.datarefTransition(self.pressure, pressure, elapsed, 0.005)

    def setTropo(self, tropo, elapsed):
        """Set
        - Troposphere limit altitude and temperature
        - Temperature vertical profile moving MSL temperature with altitude"""

        tropo_alt = tropo['alt']
        tropo_temp = tropo['temp'] - 273.15
        # c.datarefTransition(self.tropo_alt, tropo_alt, elapsed, 50)
        # c.datarefTransition(self.tropo_temp, tropo_temp, elapsed)

        temp_list = self.weatherData['gfs']['temperature']
        surface = self.weatherData['gfs']['surface']
        metar = self.weatherData['metar']

        alt = False
        if metar:
            alt = metar['elevation']
        elif surface:
            alt = surface['alt']
        if alt:
            '''delete temp layers below surface'''
            temp_list = [el for el in temp_list if el[0] > alt + 1000]

        temp, dew = False, False
        if not len(temp_list) or self.alt < temp_list[0][0] or self.alt > tropo_alt:
            '''Set temperature profile using surface and tropo temperature'''
            if 'distance' in metar and 'temperature' in metar and metar['distance'] < self.conf.metar_distance_limit:
                alt = metar['elevation']  # in meters
                if metar['temperature'][0] is not False:
                    temp = metar['temperature'][0] + 273.15  # K
                if metar['temperature'][1] is not False:
                    dew = metar['temperature'][1] + 273.15  # K
            else:
                if surface:
                    alt, temp, _ = surface.values()
        elif len(temp_list) and temp_list[0][0] < self.alt < tropo_alt:
            # level = min(abs(self.alt - el[0]) for el in temp_list)
            level = min(temp_list, key=lambda x: abs(x[0] - self.alt))
            alt, temp, _, _ = level

        if temp:
            mslt = c.oat2msltemp(temp - 273.15, alt, tropo_temp, tropo_alt)
            c.datarefTransition(self.msltemp, mslt, elapsed)
            self.opt_clouds['temp'] = [round(alt), round(temp - 273.15), round(mslt, 1)]
        if dew:
            c.datarefTransition(self.msldewp, c.oat2msltemp(dew - 273.15, alt, tropo_temp, tropo_alt), elapsed)

    def setThermals(self):
        """if TS is in METAR, simulates uplift
            otherwise calculates thermal activity based on temperature delta and lower cloud layer"""
        from random import randrange

        thermals = {
            'grad': 0,
            'alt': 10000,
            'prob': 0,
            'rate': 0,
        }

        '''check if we need to update thermal activity'''
        time = int(self.xpTime.value)
        if self.newData or 'thermals' not in self.weatherData:
            if self.thunderstorm.value > 0:
                '''add simulated uplift under thunderstorms'''
                thermals['grad'] = 'TS'
                thermals['prob'] = max(0.15, min(0.25, self.thunderstorm.value / 2))
                if self.thunderstorm.value > 0.5:
                    thermals['rate'] = randrange(1500, 3000)
                elif self.thunderstorm.value > 0.25:
                    thermals['rate'] = randrange(1000, 2000)
                else:
                    thermals['rate'] = randrange(500, 1500)
            elif 'metar' in self.weatherData:
                alt0, t0, alt1, t1, base, top = False, False, False, False, False, False
                metar = self.weatherData['metar']
                '''get surface info'''
                if 'temperature' in metar:
                    t0 = metar['temperature'][0]
                if 'elevation' in metar:
                    alt0 = metar['elevation']
                '''check if there are conditions for thermal activity'''
                if not ((time < 36000 or time > 66600)  # no thermals before 10 and after 18:30
                        or any(el['coverage'].value > 3 for el in self.clouds)  # no thermals if overcast
                        or metar['visibility'] < 2000):  # no thermals with fog or mist
                    if any(el['coverage'].value > 0 for el in self.clouds):
                        '''get cloud base'''
                        base = self.clouds[0]['bottom'].value
                        top = self.clouds[0]['top'].value
                    if not base or base - alt0 > 500:  # at least 500m high base
                        if 'gfs' in self.weatherData and 'winds' in self.weatherData['gfs']:
                            winds = self.weatherData['gfs']['winds']
                            w = next((el for el in winds if 'temp' in el[3].keys() and el[0] > alt0 + 1000), None)
                            if w:
                                alt1 = w[0]
                                t1 = w[3]['temp'] - 273.15

                if alt0 and alt1 and t0 and t1:
                    gradient = (t1 - t0) / (alt1 - alt0) * 100
                    thermals['grad'] = gradient
                    if gradient < -0.7:
                        '''create thermals'''
                        if base and top:
                            thermals['alt'] = top
                            bonus = 0.1  # clouds convection
                        else:
                            thermals['alt'] = alt0 + 2000
                            bonus = 0
                        if -1 <= gradient:
                            thermals['prob'] = 0.05 + bonus
                            thermals['rate'] = randrange(100, 300)  # .5-1.5 m/s
                        elif -2 <= gradient < -1:
                            thermals['prob'] = 0.1 + bonus
                            thermals['rate'] = randrange(200, 800)  # 1-4 m/s
                        else:
                            thermals['prob'] = 0.15 + bonus
                            thermals['rate'] = randrange(600, 1200)  # 3-6 m/s
            else:
                '''nothing to do'''
                return

            self.weatherData['thermals'] = thermals

            '''update dataRef if needed'''
            self.setDrefIfDiff(self.thermals_prob, thermals['prob'], 0.1)
            self.setDrefIfDiff(self.thermals_rate, thermals['rate'], 100)
            self.setDrefIfDiff(self.thermals_alt, thermals['alt'], 20)

            self.data.thermals_prob = thermals['prob']
            self.data.thermals_rate = thermals['rate']
            self.data.thermals_alt = thermals['alt']


class Data:
    """
    Plugin dataref data publishing
    """

    def __init__(self, plugin):

        EasyDref.plugin = plugin
        self.registered = False
        self.registerTries = 0

        # Overrides
        self.override_clouds = EasyDref('xjpc/XPNoaaWeather/config/override_clouds', 'int',
                                        register=True, writable=True)
        self.override_winds = EasyDref('xjpc/XPNoaaWeather/config/override_winds', 'int',
                                       register=True, writable=True)
        self.override_visibility = EasyDref('xjpc/XPNoaaWeather/config/override_visibility', 'int',
                                            register=True, writable=True)
        self.override_turbulence = EasyDref('xjpc/XPNoaaWeather/config/override_turbulence', 'int',
                                            register=True, writable=True)
        self.override_pressure = EasyDref('xjpc/XPNoaaWeather/config/override_pressure', 'int',
                                          register=True, writable=True)
        self.override_precipitation = EasyDref('xjpc/XPNoaaWeather/config/override_precipitation', 'int',
                                               register=True, writable=True)
        self.override_tropo = EasyDref('xjpc/XPNoaaWeather/config/override_tropo', 'int',
                                       register=True, writable=True)
        self.override_thermals = EasyDref('xjpc/XPNoaaWeather/config/override_thermals', 'int',
                                          register=True, writable=True)
        self.override_runway_friction = EasyDref('xjpc/XPNoaaWeather/config/override_runway_friction', 'int',
                                                 register=True, writable=True)
        self.override_runway_is_patchy = EasyDref('xjpc/XPNoaaWeather/config/override_runway_is_patchy', 'int',
                                                  register=True, writable=True)

        # Weather variables
        self.ready = EasyDref('xjpc/XPNoaaWeather/weather/ready', 'float', register=True)
        self.visibility = EasyDref('xjpc/XPNoaaWeather/weather/visibility', 'float', register=True)

        self.nwinds = EasyDref('xjpc/XPNoaaWeather/weather/gfs_nwinds', 'int', register=True)
        self.wind_alt = EasyDref('xjpc/XPNoaaWeather/weather/gfs_wind_alt[16]', 'float', register=True)
        self.wind_hdg = EasyDref('xjpc/XPNoaaWeather/weather/gfs_wind_hdg[16]', 'float', register=True)
        self.wind_speed = EasyDref('xjpc/XPNoaaWeather/weather/gfs_wind_speed[16]', 'float', register=True)
        self.wind_temp = EasyDref('xjpc/XPNoaaWeather/weather/gfs_wind_temp[16]', 'float', register=True)

        self.tropo_alt = EasyDref('xjpc/XPNoaaWeather/weather/tropo_alt', 'float', register=True)
        self.tropo_temp = EasyDref('xjpc/XPNoaaWeather/weather/tropo_temp', 'float', register=True)

        self.thermals_prob = EasyDref('xjpc/XPNoaaWeather/weather/thermals_prob', 'float', register=True)
        self.thermals_rate = EasyDref('xjpc/XPNoaaWeather/weather/thermals_rate', 'float', register=True)
        self.thermals_alt = EasyDref('xjpc/XPNoaaWeather/weather/thermals_alt', 'float', register=True)

        self.cloud_base = EasyDref('xjpc/XPNoaaWeather/weather/cloud_base[3]', 'float', register=True)
        self.cloud_top = EasyDref('xjpc/XPNoaaWeather/weather/cloud_top[3]', 'float', register=True)
        self.cloud_cover = EasyDref('xjpc/XPNoaaWeather/weather/cloud_cover[3]', 'float', register=True)

        self.nturbulence = EasyDref('xjpc/XPNoaaWeather/weather/wafs_nturb', 'int', register=True)
        self.turbulence_alt = EasyDref('xjpc/XPNoaaWeather/weather/turbulence_alt[16]', 'float', register=True)
        self.turbulence_sev = EasyDref('xjpc/XPNoaaWeather/weather/turbulence_sev[16]', 'float', register=True)

        # Metar variables
        self.metar_temperature = EasyDref('xjpc/XPNoaaWeather/weather/metar_temperature', 'float', register=True)
        self.metar_dewpoint = EasyDref('xjpc/XPNoaaWeather/weather/metar_dewpoint', 'float', register=True)
        self.metar_pressure = EasyDref('xjpc/XPNoaaWeather/weather/metar_pressure', 'float', register=True)
        self.metar_visibility = EasyDref('xjpc/XPNoaaWeather/weather/metar_visibility', 'float', register=True)
        self.metar_precipitation = EasyDref('xjpc/XPNoaaWeather/weather/metar_precipitation', 'int', register=True)
        self.metar_thunderstorm = EasyDref('xjpc/XPNoaaWeather/weather/metar_thunderstorm', 'int', register=True)
        self.metar_runwayFriction = EasyDref('xjpc/XPNoaaWeather/weather/metar_runwayFriction', 'float', register=True)

    def updateData(self, wdata):
        """Publish raw Dataref data
        some data is published elsewhere
        """

        if not self.registered:
            self.registered = EasyDref.DataRefEditorRegister()
            self.registerTries += 1
            if self.registerTries > 20:
                self.registered = True

        if not wdata:
            self.ready.value = 0
        else:
            self.ready.value = 1
            if 'metar' in wdata and 'icao' in wdata['metar']:
                self.metar_temperature.value = wdata['metar']['temperature'][0]
                self.metar_dewpoint.value = wdata['metar']['temperature'][1]
                self.metar_pressure.value = wdata['metar']['pressure']
                self.metar_visibility.value = wdata['metar']['visibility']

            if 'gfs' in wdata:
                if 'winds' in wdata['gfs']:

                    alts = []
                    hdgs = []
                    speeds = []
                    temps = []

                    for layer in wdata['gfs']['winds']:
                        alt, hdg, speed, extra = layer
                        alts.append(alt)
                        hdgs.append(hdg)
                        speeds.append(speed)
                        temps.append(extra['temp'])

                    self.nwinds = len(alts)
                    self.wind_alt.value = alts
                    self.wind_hdg.value = hdgs
                    self.wind_speed.value = speeds
                    self.wind_temp.value = temps
                if 'tropo' in wdata['gfs'] and 'temp' in wdata['gfs']['tropo']:
                    # self.tropo_alt.value_f = wdata['gfs']['tropo']['alt']
                    # self.tropo_temp.value_f = wdata['gfs']['tropo']['temp']
                    pass

            if 'wafs' in wdata:

                turb_fl = []
                turb_sev = []
                for layer in wdata['wafs']:
                    turb_fl.append(layer[0])
                    turb_fl.append(layer[1])

                self.nturbulence = len(turb_fl)
                self.turbulence_alt = turb_fl
                self.turbulence_sev = turb_sev


class PythonInterface:
    """
    Xplane plugin
    """

    def XPluginStart(self):
        self.syspath = []
        self.conf = Conf(XPLMGetSystemPath(self.syspath)[:-1])
        print("Conf is {}".format(self.conf))

        self.Name = "NOAA Weather - " + self.conf.__VERSION__
        self.Sig = "noaaweather.xppython3"
        self.Desc = "NOAA GFS Weather Data in X-Plane"

        self.latdr = EasyDref('sim/flightmodel/position/latitude', 'double')
        self.londr = EasyDref('sim/flightmodel/position/longitude', 'double')
        self.altdr = EasyDref('sim/flightmodel/position/elevation', 'double')

        self.data = Data(self)
        self.weather = Weather(self.conf, self.data)

        # floop
        self.floop = self.floopCallback
        XPLMRegisterFlightLoopCallback(self.floop, -1, 0)

        # Menu / About
        self.Mmenu = self.mainMenuCB
        self.aboutWindow = False
        self.metarWindow = False
        self.mPluginItem = XPLMAppendMenuItem(XPLMFindPluginsMenu(), 'XP NOAA Weather', 0)
        self.mMain = XPLMCreateMenu('XP NOAA Weather', XPLMFindPluginsMenu(), self.mPluginItem, self.Mmenu, 0)
        # Menu Items
        XPLMAppendMenuItem(self.mMain, 'Configuration', 1)
        XPLMAppendMenuItem(self.mMain, 'Metar Query', 2)

        # Register commands
        self.metarWindowCMD = EasyCommand(self, 'metar_query_window_toggle', self.metarQueryWindowToggle,
                                          description="Toggle METAR query window.")

        # Flightloop counters
        self.flcounter = 0
        self.fltime = 1
        self.lastParse = 0

        self.newAptLoaded = False

        self.aboutlines = 28

        return self.Name, self.Sig, self.Desc

    def mainMenuCB(self, menuRef, menuItem):
        """Main menu Callback"""

        if menuItem == 1:
            if not self.aboutWindow:
                self.CreateAboutWindow(221, 640)
                self.aboutWindow = True
            elif not XPIsWidgetVisible(self.aboutWindowWidget):
                XPShowWidget(self.aboutWindowWidget)

        elif menuItem == 2:
            if not self.metarWindow:
                self.createMetarWindow()
            elif not XPIsWidgetVisible(self.metarWindowWidget):
                XPShowWidget(self.metarWindowWidget)
                XPSetKeyboardFocus(self.metarQueryInput)

    def CreateAboutWindow(self, x, y):
        x2 = x + 780
        y2 = y - 85 - 20 * 24
        Buffer = f"X-Plane NOAA GFS Weather - {self.conf.__VERSION__}  -- Thanks to all betatesters! --"
        top = y

        # Create the Main Widget window
        self.aboutWindowWidget = XPCreateWidget(x, y, x2, y2, 1, Buffer, 1, 0, xpWidgetClass_MainWindow)
        window = self.aboutWindowWidget

        ## MAIN CONFIGURATION ##

        # Config Sub Window, style
        subw = XPCreateWidget(x + 10, y - 30, x + 180 + 10, y2 + 40 - 25, 1, "", 0, window, xpWidgetClass_SubWindow)
        XPSetWidgetProperty(subw, xpProperty_SubWindowType, xpSubWindowStyle_SubWindow)
        x += 15
        y -= 40

        # Main enable
        XPCreateWidget(x, y, x + 20, y - 20, 1, 'Enable Plugin', 0, window, xpWidgetClass_Caption)
        self.enableCheck = XPCreateWidget(x + 120, y, x + 140, y - 20, 1, '', 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.enableCheck, xpProperty_ButtonType, xpRadioButton)
        XPSetWidgetProperty(self.enableCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        XPSetWidgetProperty(self.enableCheck, xpProperty_ButtonState, self.conf.enabled)
        y -= 40

        if not self.conf.GFS_disabled:
            # Winds enable
            XPCreateWidget(x + 5, y, x + 20, y - 20, 1, 'Wind levels', 0, window, xpWidgetClass_Caption)
            self.windsCheck = XPCreateWidget(x + 110, y, x + 120, y - 20, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.windsCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.windsCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.windsCheck, xpProperty_ButtonState, self.conf.set_wind)
            y -= 20

            # Clouds enable
            XPCreateWidget(x + 5, y, x + 20, y - 20, 1, 'Cloud levels', 0, window, xpWidgetClass_Caption)
            self.cloudsCheck = XPCreateWidget(x + 110, y, x + 120, y - 20, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.cloudsCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.cloudsCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.cloudsCheck, xpProperty_ButtonState, self.conf.set_clouds)
            y -= 20

            # Optimised clouds layers update for liners
            XPCreateWidget(x + 5, y, x + 20, y - 20, 1, 'Opt. redraw', 0, window, xpWidgetClass_Caption)
            self.optUpdCheck = XPCreateWidget(x + 110, y, x + 120, y - 20, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.optUpdCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.optUpdCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.optUpdCheck, xpProperty_ButtonState, self.conf.opt_clouds_update)
            y -= 20

            # Temperature enable
            XPCreateWidget(x + 5, y, x + 20, y - 20, 1, 'Temperature', 0, window, xpWidgetClass_Caption)
            self.tempCheck = XPCreateWidget(x + 110, y, x + 120, y - 20, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.tempCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.tempCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.tempCheck, xpProperty_ButtonState, self.conf.set_temp)
            y -= 20

            # Pressure enable
            XPCreateWidget(x + 5, y, x + 20, y - 20, 1, 'Pressure', 0, window, xpWidgetClass_Caption)
            self.pressureCheck = XPCreateWidget(x + 110, y, x + 120, y - 20, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.pressureCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.pressureCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.pressureCheck, xpProperty_ButtonState, self.conf.set_pressure)
            y -= 20

            # Turbulence enable
            XPCreateWidget(x + 5, y, x + 20, y - 20, 1, 'Turbulence', 0, window, xpWidgetClass_Caption)
            self.turbCheck = XPCreateWidget(x + 110, y, x + 120, y - 20, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.turbCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.turbCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.turbCheck, xpProperty_ButtonState, self.conf.set_turb)
            y -= 20

            self.turbulenceCaption = XPCreateWidget(x + 5, y, x + 80, y - 20, 1,
                                                    f"Turbulence prob.  {self.conf.turbulence_probability * 100}%",
                                                    0, window, xpWidgetClass_Caption)
            self.turbulenceSlider = XPCreateWidget(x + 10, y - 20, x + 160, y - 40, 1, '', 0, window,
                                                   xpWidgetClass_ScrollBar)
            XPSetWidgetProperty(self.turbulenceSlider, xpProperty_ScrollBarType, xpScrollBarTypeSlider)
            XPSetWidgetProperty(self.turbulenceSlider, xpProperty_ScrollBarMin, 10)
            XPSetWidgetProperty(self.turbulenceSlider, xpProperty_ScrollBarMax, 1000)
            XPSetWidgetProperty(self.turbulenceSlider, xpProperty_ScrollBarPageAmount, 1)

            XPSetWidgetProperty(self.turbulenceSlider, xpProperty_ScrollBarSliderPosition,
                                int(self.conf.turbulence_probability * 1000))
            y -= 40

            # Tropo enable
            XPCreateWidget(x + 5, y, x + 20, y - 20, 1, 'Tropo Temp', 0, window, xpWidgetClass_Caption)
            self.tropoCheck = XPCreateWidget(x + 110, y, x + 120, y - 20, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.tropoCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.tropoCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.tropoCheck, xpProperty_ButtonState, self.conf.set_tropo)
            y -= 20

            # Thermals enable
            XPCreateWidget(x + 5, y, x + 20, y - 20, 1, 'Thermals', 0, window, xpWidgetClass_Caption)
            self.thermalsCheck = XPCreateWidget(x + 110, y, x + 120, y - 20, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.thermalsCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.thermalsCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.thermalsCheck, xpProperty_ButtonState, self.conf.set_thermals)
            y -= 20

            # Surface Wind Layer enable
            XPCreateWidget(x + 5, y, x + 20, y - 20, 1, 'Surface Wind', 0, window, xpWidgetClass_Caption)
            self.surfaceCheck = XPCreateWidget(x + 110, y, x + 120, y - 20, 1, '', 0, window, xpWidgetClass_Button)
            XPSetWidgetProperty(self.surfaceCheck, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(self.surfaceCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(self.surfaceCheck, xpProperty_ButtonState, self.conf.set_surface_layer)
            y -= 40

        # Metar source radios
        x1 = x + 5
        XPCreateWidget(x, y, x + 20, y - 20, 1, 'METAR SOURCE', 0, window, xpWidgetClass_Caption)
        XPCreateWidget(x1, y - 20, x1 + 20, y - 40, 1, 'NOAA', 0, window, xpWidgetClass_Caption)
        mtNoaCheck = XPCreateWidget(x1 + 42, y - 20, x1 + 45, y - 40, 1, '', 0, window, xpWidgetClass_Button)
        x1 += 54
        XPCreateWidget(x1, y - 20, x1 + 20, y - 40, 1, 'IVAO', 0, window, xpWidgetClass_Caption)
        mtIvaoCheck = XPCreateWidget(x1 + 36, y - 20, x1 + 45, y - 40, 1, '', 0, window, xpWidgetClass_Button)
        x1 += 52
        XPCreateWidget(x1, y - 20, x1 + 20, y - 40, 1, 'VATSIM', 0, window, xpWidgetClass_Caption)
        mtVatsimCheck = XPCreateWidget(x1 + 46, y - 20, x1 + 60, y - 40, 1, '', 0, window, xpWidgetClass_Button)
        x1 += 52

        self.mtSourceChecks = {mtNoaCheck: 'NOAA',
                               mtIvaoCheck: 'IVAO',
                               mtVatsimCheck: 'VATSIM'
                               }

        for check in self.mtSourceChecks:
            XPSetWidgetProperty(check, xpProperty_ButtonType, xpRadioButton)
            XPSetWidgetProperty(check, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            XPSetWidgetProperty(check, xpProperty_ButtonState,
                                int(self.conf.metar_source == self.mtSourceChecks[check]))

        y -= 40

        # Ignore AUTO METAR sources
        XPCreateWidget(x + 5, y, x + 20, y - 20, 1, 'Ignore AUTO:', 0, window, xpWidgetClass_Caption)
        self.autoMetarCheck = XPCreateWidget(x + 120, y, x + 140, y - 20, 1, '', 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.autoMetarCheck, xpProperty_ButtonType, xpRadioButton)
        XPSetWidgetProperty(self.autoMetarCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        XPSetWidgetProperty(self.autoMetarCheck, xpProperty_ButtonState, self.conf.metar_ignore_auto)
        y -= 20

        # Use XP12 Real weather files to populate METAR.rwx file
        XPCreateWidget(x + 5, y, x + 20, y - 20, 1, 'Use XP12 RW as source', 0, window, xpWidgetClass_Caption)
        XPCreateWidget(x + 5, y - 20, x + 20, y - 40, 1, '   for METAR.rwx:', 0, window, xpWidgetClass_Caption)
        self.xp12MetarCheck = XPCreateWidget(x + 120, y - 20, x + 140, y - 40, 1, '', 0, window, xpWidgetClass_Button)
        XPCreateWidget(x + 5, y - 40, x + 20, y - 60, 1, '   READ the README file!', 0, window, xpWidgetClass_Caption)
        XPSetWidgetProperty(self.xp12MetarCheck, xpProperty_ButtonType, xpRadioButton)
        XPSetWidgetProperty(self.xp12MetarCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        XPSetWidgetProperty(self.xp12MetarCheck, xpProperty_ButtonState, self.conf.metar_use_xp12)
        y -= 60

        XPCreateWidget(x + 5, y, x + 20, y - 20, 1, 'Ignore Stations:', 0, window, xpWidgetClass_Caption)
        self.stationIgnoreInput = XPCreateWidget(x + 5, y - 20, x + 160, y - 40, 1,
                                                 ' '.join(self.conf.ignore_metar_stations), 0, window,
                                                 xpWidgetClass_TextField)
        XPSetWidgetProperty(self.stationIgnoreInput, xpProperty_TextFieldType, xpTextEntryField)
        XPSetWidgetProperty(self.stationIgnoreInput, xpProperty_Enabled, 1)

        y -= 60

        if not self.conf.GFS_disabled:
            # Performance Tweaks
            XPCreateWidget(x, y, x + 80, y - 20, 1, 'Performance Tweaks', 0, window, xpWidgetClass_Caption)
            XPCreateWidget(x + 5, y - 20, x + 80, y - 40, 1, 'Max Visibility (sm)', 0, window, xpWidgetClass_Caption)
            self.maxVisInput = XPCreateWidget(x + 119, y - 20, x + 160, y - 40, 1,
                                              c.convertForInput(self.conf.max_visibility, 'm2sm'), 0, window,
                                              xpWidgetClass_TextField)
            XPSetWidgetProperty(self.maxVisInput, xpProperty_TextFieldType, xpTextEntryField)
            XPSetWidgetProperty(self.maxVisInput, xpProperty_Enabled, 1)
            y -= 40
            XPCreateWidget(x + 5, y, x + 80, y - 20, 1, 'Max cloud height (ft)', 0, window, xpWidgetClass_Caption)
            self.maxCloudHeightInput = XPCreateWidget(x + 119, y, x + 160, y - 20, 1,
                                                      c.convertForInput(self.conf.max_cloud_height, 'm2ft'), 0, window,
                                                      xpWidgetClass_TextField)
            XPSetWidgetProperty(self.maxCloudHeightInput, xpProperty_TextFieldType, xpTextEntryField)
            XPSetWidgetProperty(self.maxCloudHeightInput, xpProperty_Enabled, 1)
            y -= 40

        # METAR window
        XPCreateWidget(x, y, x + 80, y - 20, 1, 'Metar window bug', 0, window, xpWidgetClass_Caption)
        self.bugCheck = XPCreateWidget(x + 120, y, x + 140, y - 20, 1, '', 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.bugCheck, xpProperty_ButtonType, xpRadioButton)
        XPSetWidgetProperty(self.bugCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        XPSetWidgetProperty(self.bugCheck, xpProperty_ButtonState, self.conf.inputbug)
        y -= 40

        # Save
        self.saveButton = XPCreateWidget(x + 25, y, x + 125, y - 20, 1, "Apply & Save", 0, window,
                                         xpWidgetClass_Button)
        XPSetWidgetProperty(self.saveButton, xpProperty_ButtonType, xpPushButton)
        self.saveButtonCaption = XPCreateWidget(x + 5, y - 20, x + 80, y - 40, 1, "", 0,  window, xpWidgetClass_Caption)

        x += 170
        y = top

        # ABOUT/ STATUS Sub Window
        subw = XPCreateWidget(x + 10, y - 30, x2 - 20 + 10, y - (18 * (self.aboutlines - 1)) - 10, 1, "", 0, window,
                              xpWidgetClass_SubWindow)
        # Set the style to sub window
        XPSetWidgetProperty(subw, xpProperty_SubWindowType, xpSubWindowStyle_SubWindow)
        x += 20
        y -= 20

        # Add Close Box decorations to the Main Widget
        XPSetWidgetProperty(window, xpProperty_MainWindowHasCloseBoxes, 1)

        # Create status captions
        self.statusBuff = []
        for i in range(self.aboutlines):
            y -= 15
            self.statusBuff.append(XPCreateWidget(x, y, x + 40, y - 20, 1, '--', 0, window, xpWidgetClass_Caption))

        self.updateStatus()

        # Enable download
        y -= 20
        # XPCreateWidget(x, y, x + 20, y - 20, 1, 'Download latest data', 0, window, xpWidgetClass_Caption)
        # self.downloadCheck = XPCreateWidget(x + 130, y, x + 134, y - 20, 1, '', 0, window, xpWidgetClass_Button)
        # XPSetWidgetProperty(self.downloadCheck, xpProperty_ButtonType, xpRadioButton)
        # XPSetWidgetProperty(self.downloadCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        # XPSetWidgetProperty(self.downloadCheck, xpProperty_ButtonState, self.conf.download)

        # XPCreateWidget(x + 160, y, x + 260, y - 20, 1, 'Ignore Stations:', 0, window, xpWidgetClass_Caption)
        # self.stationIgnoreInput = XPCreateWidget(x + 260, y, x + 540, y - 20, 1,
        #                                          ' '.join(self.conf.ignore_metar_stations), 0, window,
        #                                          xpWidgetClass_TextField)
        # XPSetWidgetProperty(self.stationIgnoreInput, xpProperty_TextFieldType, xpTextEntryField)
        # XPSetWidgetProperty(self.stationIgnoreInput, xpProperty_Enabled, 1)
        #
        # y -= 20

        # DumpLog Button
        self.dumpLogButton = XPCreateWidget(x + 320, y, x + 420, y - 20, 1, "DumpLog", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.dumpLogButton, xpProperty_ButtonType, xpPushButton)

        self.dumpLabel = XPCreateWidget(x + 270, y, x + 380, y - 20, 1, '', 0, window, xpWidgetClass_Caption)

        y -= 30
        subw = XPCreateWidget(x - 10, y - 15, x2 - 20 + 10, y2 + 15, 1, "", 0, window, xpWidgetClass_SubWindow)
        x += 10
        # Set the style to sub window

        y -= 20
        sysinfo = [
            f"X-Plane 12 NOAA Weather: {self.conf.__VERSION__}",
            '(c) antonio golfari 2022',
        ]
        for label in sysinfo:
            XPCreateWidget(x, y, x + 120, y - 10, 1, label, 0, window, xpWidgetClass_Caption)
            y -= 15

        # Visit site Button
        x += 190
        y += 15
        self.aboutVisit = XPCreateWidget(x + 120, y, x + 220, y - 20, 1, "Official site", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.aboutVisit, xpProperty_ButtonType, xpPushButton)

        self.aboutForum = XPCreateWidget(x + 240, y, x + 340, y - 20, 1, "Support", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.aboutForum, xpProperty_ButtonType, xpPushButton)

        # Register our widget handler
        self.aboutWindowHandlerCB = self.aboutWindowHandler
        XPAddWidgetCallback(window, self.aboutWindowHandlerCB)

        self.aboutWindow = window

    def aboutWindowHandler(self, inMessage, inWidget, inParam1, inParam2):
        # About window events
        if inMessage == xpMessage_CloseButtonPushed:
            if self.aboutWindow:
                XPDestroyWidget(self.aboutWindowWidget, 1)
                self.aboutWindow = False
            return 1

        if inMessage == xpMsg_ButtonStateChanged and inParam1 in self.mtSourceChecks:
            if inParam2:
                for i in self.mtSourceChecks:
                    if i != inParam1:
                        XPSetWidgetProperty(i, xpProperty_ButtonState, 0)
            else:
                XPSetWidgetProperty(inParam1, xpProperty_ButtonState, 1)
            return 1

        if inMessage == xpMsg_ScrollBarSliderPositionChanged and inParam1 == self.turbulenceSlider:
            val = XPGetWidgetProperty(self.turbulenceSlider, xpProperty_ScrollBarSliderPosition, None)
            XPSetWidgetDescriptor(self.turbulenceCaption, f"Turbulence probability {round(val/10)}%")
            return 1

        # Handle any button pushes
        if inMessage == xpMsg_PushButtonPressed:

            if (inParam1 == self.aboutVisit):
                from webbrowser import open_new
                open_new('https://github.com/biuti/XplaneNoaaWeather')
                return 1
            if inParam1 == self.aboutForum:
                from webbrowser import open_new
                open_new(
                    'http://forums.x-plane.org/index.php?/forums/topic/72313-noaa-weather-plugin/&do=getNewComment')
                return 1
            if inParam1 == self.saveButton:
                # Save configuration
                self.conf.enabled = XPGetWidgetProperty(self.enableCheck, xpProperty_ButtonState, None)
                if not self.conf.GFS_disabled:
                    self.conf.set_wind = XPGetWidgetProperty(self.windsCheck, xpProperty_ButtonState, None)
                    self.conf.set_clouds = XPGetWidgetProperty(self.cloudsCheck, xpProperty_ButtonState, None)
                    self.conf.opt_clouds_update = XPGetWidgetProperty(self.optUpdCheck, xpProperty_ButtonState, None)
                    self.conf.set_temp = XPGetWidgetProperty(self.tempCheck, xpProperty_ButtonState, None)
                    self.conf.set_pressure = XPGetWidgetProperty(self.pressureCheck, xpProperty_ButtonState, None)
                    self.conf.set_tropo = XPGetWidgetProperty(self.tropoCheck, xpProperty_ButtonState, None)
                    self.conf.set_thermals = XPGetWidgetProperty(self.thermalsCheck, xpProperty_ButtonState, None)
                    self.conf.set_surface_layer = XPGetWidgetProperty(self.surfaceCheck, xpProperty_ButtonState, None)
                    self.conf.turbulence_probability = XPGetWidgetProperty(self.turbulenceSlider,
                                                                           xpProperty_ScrollBarSliderPosition,
                                                                           None) / 1000.0
                    # Zero turbulence data if disabled
                    self.conf.set_turb = XPGetWidgetProperty(self.turbCheck, xpProperty_ButtonState, None)
                    if not self.conf.set_turb:
                        for i in range(3):
                            self.weather.winds[i]['turbulence'].value = 0

                    buff = XPGetWidgetDescriptor(self.maxCloudHeightInput)
                    self.conf.max_cloud_height = c.convertFromInput(buff, 'f2m', min=c.f2m(2000))

                    buff = XPGetWidgetDescriptor(self.maxVisInput)
                    self.conf.max_visibility = c.convertFromInput(buff, 'sm2m')

                # Metar station ignore
                buff = XPGetWidgetDescriptor(self.stationIgnoreInput)
                ignore_stations = []
                for icao in buff.split(' '):
                    if len(icao) == 4:
                        ignore_stations.append(icao.upper())

                self.conf.metar_ignore_auto = XPGetWidgetProperty(self.autoMetarCheck, xpProperty_ButtonState, None)
                self.conf.ignore_metar_stations = ignore_stations

                # Check metar source
                prev_metar_source = self.conf.metar_source
                for check in self.mtSourceChecks:
                    if XPGetWidgetProperty(check, xpProperty_ButtonState, None):
                        self.conf.metar_source = self.mtSourceChecks[check]

                # Check METAR.rwx source
                prev_file_source = self.conf.metar_use_xp12
                print(f"METAR.rwx source: {'XP12' if self.conf.metar_use_xp12 else self.conf.metar_source}")
                self.conf.metar_use_xp12 = XPGetWidgetProperty(self.xp12MetarCheck, xpProperty_ButtonState, None)
                self.conf.inputbug = XPGetWidgetProperty(self.bugCheck, xpProperty_ButtonState, None)

                # Save config and tell server to reload it
                self.conf.pluginSave()
                print(f"Config saved. Weather client reloading ...")
                self.weather.weatherClientSend('!reload')

                # If metar source has changed tell server to reinit metar database
                if self.conf.metar_source != prev_metar_source:
                    print(f"METAR source changed. Get from: {self.conf.metar_source}")
                    self.weather.weatherClientSend('!resetMetar')

                # If metar source for METAR.rwx file has changed tell server to reinit rwmetar database
                if self.conf.metar_use_xp12 != prev_file_source:
                    print(f"METAR.rwx source changed. Get from XP12: {self.conf.metar_use_xp12}")
                    self.weather.weatherClientSend('!resetRWMetar')

                self.weather.startWeatherClient()
                self.aboutWindowUpdate()

                # Reset things
                self.weather.newData = True
                self.newAptLoaded = True

                return 1
            if inParam1 == self.dumpLogButton:
                dumpfile = self.dumpLog()
                XPSetWidgetDescriptor(self.dumpLabel, Path(*dumpfile.parts[-3:]))
                return 1
        return 0

    def aboutWindowUpdate(self):
        XPSetWidgetProperty(self.enableCheck, xpProperty_ButtonState, self.conf.enabled)
        XPSetWidgetDescriptor(self.stationIgnoreInput, ' '.join(self.conf.ignore_metar_stations))
        XPSetWidgetProperty(self.autoMetarCheck, xpProperty_ButtonState, self.conf.metar_ignore_auto)
        XPSetWidgetProperty(self.xp12MetarCheck, xpProperty_ButtonState, self.conf.metar_use_xp12)

        if not self.conf.GFS_disabled:
            XPSetWidgetProperty(self.windsCheck, xpProperty_ButtonState, self.conf.set_wind)
            XPSetWidgetProperty(self.cloudsCheck, xpProperty_ButtonState, self.conf.set_clouds)
            XPSetWidgetProperty(self.optUpdCheck, xpProperty_ButtonState, self.conf.opt_clouds_update)
            XPSetWidgetProperty(self.tempCheck, xpProperty_ButtonState, self.conf.set_temp)
            XPSetWidgetProperty(self.tropoCheck, xpProperty_ButtonState, self.conf.set_tropo)
            XPSetWidgetProperty(self.thermalsCheck, xpProperty_ButtonState, self.conf.set_thermals)
            XPSetWidgetProperty(self.surfaceCheck, xpProperty_ButtonState, self.conf.set_surface_layer)
            XPSetWidgetDescriptor(self.maxVisInput, c.convertForInput(self.conf.max_visibility, 'm2sm'))
            XPSetWidgetDescriptor(self.maxCloudHeightInput, c.convertForInput(self.conf.max_cloud_height, 'm2ft'))


        self.updateStatus()

    def updateStatus(self):
        """Updates status window"""

        sysinfo = self.weatherInfo()

        i = 0
        for label in sysinfo:
            XPSetWidgetDescriptor(self.statusBuff[i], label)
            i += 1
            if i > self.aboutlines - 1:
                break

        text = ""
        if self.conf.settingsfile.is_file():
            d = int(time.time() - self.conf.settingsfile.stat().st_mtime)
            if d < 15:
                text = f"Reloading ({15 - d} sec.) ..."
        XPSetWidgetDescriptor(self.saveButtonCaption, text)

    def weatherInfo(self):
        """Return an array of strings with formatted weather data"""
        verbose = self.conf.verbose
        sysinfo = [f"XPNoaaWeather for XP12 {self.conf.__VERSION__} Status:"]

        if not self.weather.weatherData:
            sysinfo += ['* Data not ready. Please wait.']
        else:
            wdata = self.weather.weatherData
            if 'info' in wdata:
                sysinfo += [
                    '   LAT: %.2f/%.2f LON: %.2f/%.2f FL: %02.f MAGNETIC DEV: %.2f' % (
                        self.latdr.value, wdata['info']['lat'], self.londr.value, wdata['info']['lon'],
                        c.m2ft(self.altdr.value) / 100, self.weather.mag_deviation.value)
                ]
                if 'None' in wdata['info']['gfs_cycle']:
                    sysinfo += ['   XP12 is still downloading weather info ...']
                else:
                    sysinfo += [f"   GFS Cycle: {wdata['info']['gfs_cycle']}"]

            if 'metar' in wdata and 'icao' in wdata['metar']:
                sysinfo += ['']
                # Split metar if needed
                splitlen = 80
                metar = f"{self.conf.metar_source} METAR: {wdata['metar']['icao']} {wdata['metar']['metar']}"
                if len(metar) > splitlen:
                    icut = metar.rfind(' ', 0, splitlen)
                    sysinfo += [metar[:icut], metar[icut + 1:]]
                else:
                    sysinfo += [metar]

                sysinfo += [
                    f"   Apt altitude: {int(c.m2ft(wdata['metar']['elevation']))}ft, "
                    f"Apt distance: {round(wdata['metar']['distance'] / 1000, 1)}km",
                    f"   Temp: {round(wdata['metar']['temperature'][0])}, "
                    f"Dewpoint: {round(wdata['metar']['temperature'][1])}, "
                    f"Visibility: {wdata['metar']['visibility']}m, "
                    f"Press: {wdata['metar']['pressure']} inhg "
                ]

                wind = f"   Wind:  {wdata['metar']['wind'][0]} {wdata['metar']['wind'][1]}kt"
                if wdata['metar']['wind'][2]:
                    wind += f", gust {wdata['metar']['wind'][2]}kt"
                if 'variable_wind' in wdata['metar'] and wdata['metar']['variable_wind']:
                    wind += f" Variable: {wdata['metar']['variable_wind'][0]}-{wdata['metar']['variable_wind'][1]}"
                sysinfo += [wind]

                if 'precipitation' in wdata['metar'] and len(wdata['metar']['precipitation']):
                    precip = ''
                    for type in wdata['metar']['precipitation']:
                        if wdata['metar']['precipitation'][type]['recent']:
                            precip += wdata['metar']['precipitation'][type]['recent']
                        precip += f"{wdata['metar']['precipitation'][type]['int']}{type} "
                    sysinfo += [f"   Precipitation: {precip}"]

                if 'clouds' in wdata['metar']:
                    if len(wdata['metar']['clouds']):
                        clouds = '   Clouds: BASE|COVER    '
                        for cloud in wdata['metar']['clouds']:
                            alt, coverage, type = cloud
                            clouds += f"{c.m2fl(alt):03}|{coverage}{type} "
                    else:
                        clouds = '   Clouds and Visibility OK'
                    sysinfo += [clouds]
                if 'rwmetar' in wdata:
                    if not wdata['rwmetar']['file_time']:
                        sysinfo += ['XP12 REAL WEATHER METAR:', '   no METAR file, still downloading...']
                    else:
                        sysinfo += [f"XP12 REAL WEATHER METAR ({wdata['rwmetar']['file_time']}):"]
                        for line in wdata['rwmetar']['reports'][:2]:
                            sysinfo += [f"   {line}"]
                    sysinfo += ['']

            if not self.conf.meets_wgrib2_requirements:
                '''not a compatible OS with wgrib2'''
                sysinfo += ['',
                            '*** *** WGRIB2 decoder not available for your OS version *** ***',
                            'Windows 7 or above, MacOS 10.14 or above, Linux kernel 4.0 or above.',
                            ''
                            ]
            elif self.conf.GFS_disabled:
                sysinfo += [
                    '*** *** GFS weather data download is disabled (XP12 early release) *** ***',
                    'Some features will be added back as soon as a XP12 final version is available',
                    'At this stage the plugin writes missing METAR.rwx file and monitors XP12 real weather.',
                    ''
                ]

                if 'gfs' in wdata:
                    if 'winds' in wdata['gfs']:
                        sysinfo += ['XP12 REAL WEATHER WIND LAYERS: FL | HDG | KT | TEMP | DEV']
                        wlayers = ''
                        out = []
                        for i, layer in enumerate(wdata['gfs']['winds'], 1):
                            alt, hdg, speed, extra = layer
                            wlayers += f"    F{c.m2fl(alt):03.0F} | {hdg:03.0F} | {int(speed):03}kt" \
                                       f" | {round(c.kel2cel(extra['temp'])):02} | {round(c.kel2cel(extra['dev'])):02}"
                            if i % 3 == 0 or i == len(wdata['gfs']['winds']):
                                out.append(wlayers)
                                wlayers = ''
                        sysinfo += out

                    if 'clouds' in wdata['gfs']:
                        sysinfo += ['XP12 REAL WEATHER CLOUD LAYERS  FLBASE | FLTOP | COVER']
                        clayers = ''
                        clouds = [el for el in wdata['gfs']['clouds'] if el[0] > 0]
                        out = []
                        if not len(clouds):
                            sysinfo += ['    None reported']
                        else:
                            for i, layer in enumerate(clouds, 1):
                                base, top, cover = layer
                                clayers += f"    {c.m2fl(base):03} | {c.m2fl(top):03} | {cover}%"
                                if i % 3 == 0 or i == len(clouds):
                                    out.append(clayers)
                                    clayers = ''
                            sysinfo += out

                    if 'wafs' in wdata:
                        tblayers = ''
                        for layer in wdata['wafs']:
                            tblayers += f"    {round(layer[0] * 3.28084 / 100)}|{round(layer[1], 2)}" \
                                        f"{'*' if layer[1] >= self.conf.max_turbulence else ''}"

                        sysinfo += [f"XP12 REAL WEATHER TURBULENCE ({len(wdata['wafs'])}):  "
                                    f"FL | SEV (max {self.conf.max_turbulence}) ",
                                    tblayers]
                    sysinfo += ['']

            else:
                '''Normal GFS mode'''
                if 'gfs' in wdata:
                    if 'winds' in wdata['gfs']:
                        sysinfo += ['', f"GFS WIND LAYERS: {len(wdata['gfs']['winds'])} FL|HDG|KT|TEMP|DEV"]
                        wlayers = ''
                        i = 0
                        for layer in wdata['gfs']['winds']:
                            i += 1
                            alt, hdg, speed, extra = layer
                            wlayers += f"   F{c.m2fl(alt):03}|{hdg:03}|{speed:02}kt|" \
                                       f"{round(c.kel2cel(extra['temp'])):02}|{round(c.kel2cel(extra['dev'])):02}"
                            if i > 3:
                                i = 0
                                sysinfo += [wlayers]
                                wlayers = ''

                    if 'clouds' in wdata['gfs']:
                        clouds = 'GFS CLOUDS  FLBASE|FLTOP|COVER'
                        for layer in wdata['gfs']['clouds']:
                            base, top, cover = layer
                            if base > 0:
                                clouds += f"    {c.m2fl(base):03} | {c.m2fl(top):03} | {cover}%"
                        sysinfo += [clouds]

                    if 'tropo' in wdata['gfs']:
                        alt, temp, dev = wdata['gfs']['tropo'].values()
                        if alt and temp and dev:
                            sysinfo += [f"TROPO LIMIT: {round(alt)}m "
                                        f"temp {round(c.kel2cel(temp)):02}C ISA Dev {round(c.kel2cel(dev)):02}C"]

                if 'wafs' in wdata:
                    tblayers = ''
                    for layer in wdata['wafs']:
                        tblayers += f"   {c.m2fl(layer[0]):03}|{round(layer[1], 2)}" \
                                    f"{'*' if layer[1]>=self.conf.max_turbulence else ''}"

                    sysinfo += [f"WAFS TURBULENCE ({len(wdata['wafs'])}): FL|SEV (max {self.conf.max_turbulence}) ",
                                tblayers]

                sysinfo += ['']
                if 'thermals' in wdata:
                    t = wdata['thermals']

                    if not t['grad']:
                        s = "THERMALS: N/A"
                    else:
                        if t['grad'] == "TS":
                            s = "THERMALS (TS mode): "
                        else:
                            s = f"THERMALS: grad. {round(t['grad'], 2)} ºC/100m, "
                        s += f"h {round(t['alt'])}m, p {round(t['prob']*100)}%, r {round(t['rate']*0.00508)}m/s"
                    sysinfo += [s]

                if self.conf.set_surface_layer:
                    s = 'NOT ACTIVE' if not self.weather.surface_wind else 'ACTIVE'
                    sysinfo += [f"SURFACE WIND LAYER: {s}"]

                if 'cloud_info' in wdata and self.conf.opt_clouds_update:
                    ci = wdata['cloud_info']
                    s = 'OPTIMISED FOR BEST PERFORMANCE' if ci['OVC'] and ci['above_clouds'] else 'MERGED'
                    sysinfo += [f"CLOUD LAYERS MODE: {s}"]
                    if verbose:
                        sysinfo += [f"{ci['layers']}"]

        sysinfo += ['--'] * (self.aboutlines - len(sysinfo))

        return sysinfo

    def createMetarWindow(self):
        x = 100
        w = 480
        y = 600
        h = 180
        x2 = x + w
        y2 = y - h
        windowTitle = "METAR Request"

        # Create the Main Widget window
        self.metarWindow = True
        self.metarWindowWidget = XPCreateWidget(x, y, x2, y2, 1, windowTitle, 1, 0, xpWidgetClass_MainWindow)
        XPSetWidgetProperty(self.metarWindowWidget, xpProperty_MainWindowType, xpMainWindowStyle_Translucent)

        # Config Sub Window, style
        # subw = XPCreateWidget(x+10, y-30, x2-20 + 10, y2+40 -25, 1, "" ,  0,self.metarWindowWidget , xpWidgetClass_SubWindow)
        # XPSetWidgetProperty(subw, xpProperty_SubWindowType, xpSubWindowStyle_SubWindow)
        XPSetWidgetProperty(self.metarWindowWidget, xpProperty_MainWindowHasCloseBoxes, 1)
        x += 10
        y -= 20

        cap = XPCreateWidget(x, y, x + 40, y - 20, 1, 'Airport ICAO code:', 0, self.metarWindowWidget,
                             xpWidgetClass_Caption)
        XPSetWidgetProperty(cap, xpProperty_CaptionLit, 1)

        y -= 20
        # Airport input
        self.metarQueryInput = XPCreateWidget(x + 5, y, x + 120, y - 20, 1, "", 0, self.metarWindowWidget,
                                              xpWidgetClass_TextField)
        XPSetWidgetProperty(self.metarQueryInput, xpProperty_TextFieldType, xpTextEntryField)
        XPSetWidgetProperty(self.metarQueryInput, xpProperty_Enabled, 1)
        XPSetWidgetProperty(self.metarQueryInput, xpProperty_TextFieldType, xpTextTranslucent)

        self.metarQueryButton = XPCreateWidget(x + 140, y, x + 210, y - 20, 1, "Request", 0, self.metarWindowWidget,
                                               xpWidgetClass_Button)
        XPSetWidgetProperty(self.metarQueryButton, xpProperty_ButtonType, xpPushButton)
        XPSetWidgetProperty(self.metarQueryButton, xpProperty_Enabled, 1)

        y -= 20
        # Help caption
        cap = XPCreateWidget(x, y, x + 300, y - 20, 1,
                             f"{self.conf.metar_source}:", 0, self.metarWindowWidget, xpWidgetClass_Caption)
        XPSetWidgetProperty(cap, xpProperty_CaptionLit, 1)

        y -= 20
        # Query output
        self.metarQueryOutput = XPCreateWidget(x + 5, y, x + 450, y - 20, 1, "", 0, self.metarWindowWidget,
                                               xpWidgetClass_TextField)
        XPSetWidgetProperty(self.metarQueryOutput, xpProperty_TextFieldType, xpTextEntryField)
        XPSetWidgetProperty(self.metarQueryOutput, xpProperty_Enabled, 1)
        XPSetWidgetProperty(self.metarQueryOutput, xpProperty_TextFieldType, xpTextTranslucent)

        y -= 20
        # Help caption
        cap = XPCreateWidget(x, y, x + 300, y - 20, 1, f"XP12 Real Weather:", 0, self.metarWindowWidget,
                             xpWidgetClass_Caption)
        XPSetWidgetProperty(cap, xpProperty_CaptionLit, 1)

        y -= 20
        # Query output
        self.metarQueryXP12 = XPCreateWidget(x + 5, y, x + 450, y - 20, 1, "", 0, self.metarWindowWidget,
                                               xpWidgetClass_TextField)
        XPSetWidgetProperty(self.metarQueryXP12, xpProperty_TextFieldType, xpTextEntryField)
        XPSetWidgetProperty(self.metarQueryXP12, xpProperty_Enabled, 1)
        XPSetWidgetProperty(self.metarQueryXP12, xpProperty_TextFieldType, xpTextTranslucent)

        if not self.conf.inputbug:
            # Register our sometimes buggy widget handler
            self.metarQueryInputHandlerCB = self.metarQueryInputHandler
            XPAddWidgetCallback(self.metarQueryInput, self.metarQueryInputHandlerCB)

        # Register our widget handler
        self.metarWindowHandlerCB = self.metarWindowHandler
        XPAddWidgetCallback(self.metarWindowWidget, self.metarWindowHandlerCB)

        XPSetKeyboardFocus(self.metarQueryInput)

    def metarQueryInputHandler(self, inMessage, inWidget, inParam1, inParam2):
        """Override Texfield keyboard input to be more friendly"""
        if inMessage == xpMsg_KeyPress:

            key, flags, vkey = inParam1

            if flags == 8:
                cursor = XPGetWidgetProperty(self.metarQueryInput, xpProperty_EditFieldSelStart, None)
                text = XPGetWidgetDescriptor(self.metarQueryInput).strip()
                if key in (8, 127):
                    # pass
                    XPSetWidgetDescriptor(self.metarQueryInput, text[:-1])
                    cursor -= 1
                elif key == 13:
                    # Enter
                    self.metarQuery()
                elif key == 27:
                    # ESC
                    XPLoseKeyboardFocus(self.metarQueryInput)
                elif 65 <= key <= 90 or 97 <= key <= 122 and len(text) < 4:
                    text += chr(key).upper()
                    XPSetWidgetDescriptor(self.metarQueryInput, text)
                    cursor += 1

                ltext = len(text)
                if cursor < 0: cursor = 0
                if cursor > ltext: cursor = ltext

                XPSetWidgetProperty(self.metarQueryInput, xpProperty_EditFieldSelStart, cursor)
                XPSetWidgetProperty(self.metarQueryInput, xpProperty_EditFieldSelEnd, cursor)

                return 1
        elif inMessage in (xpMsg_MouseDrag, xpMsg_MouseDown, xpMsg_MouseUp):
            XPSetKeyboardFocus(self.metarQueryInput)
            return 1
        return 0

    def metarWindowHandler(self, inMessage, inWidget, inParam1, inParam2):
        if inMessage == xpMessage_CloseButtonPushed:
            if self.metarWindow:
                XPHideWidget(self.metarWindowWidget)
                return 1
        if inMessage == xpMsg_PushButtonPressed:
            if inParam1 == self.metarQueryButton:
                self.metarQuery()
                return 1
        return 0

    def metarQuery(self):
        query = XPGetWidgetDescriptor(self.metarQueryInput).strip().upper()
        XPSetWidgetDescriptor(self.metarQueryXP12, '')
        if len(query) == 4:
            self.weather.weatherClientSend('?' + query)
            XPSetWidgetDescriptor(self.metarQueryOutput, 'Querying, please wait.')
        else:
            XPSetWidgetDescriptor(self.metarQueryOutput, 'Please insert a valid ICAO code.')

    def metarQueryCallback(self, msg):
        """Callback for metar queries"""

        if self.metarWindow:
            # Filter metar text
            metar = ''.join(filter(lambda x: x in self.conf.printableChars, msg['metar']['metar']))
            rwmetar = ''.join(filter(lambda x: x in self.conf.printableChars, msg['rwmetar']['metar']))
            # XPSetWidgetDescriptor(self.metarQueryOutput, '%s %s' % (msg['metar']['icao'], metar))
            # adding source and internal XP12 METARs
            # XPSetWidgetDescriptor(self.metarQueryOutput, f"STATION: {msg['metar']['icao']}")
            XPSetWidgetDescriptor(self.metarQueryOutput, f"{msg['metar']['icao']} {metar}")
            XPSetWidgetDescriptor(self.metarQueryXP12, f"{msg['rwmetar']['icao']} {rwmetar}")

    def metarQueryWindowToggle(self):
        """Metar window toggle command"""
        if self.metarWindow:
            if XPIsWidgetVisible(self.metarWindowWidget):
                XPHideWidget(self.metarWindowWidget)
            else:
                XPShowWidget(self.metarWindowWidget)
        else:
            self.createMetarWindow()

    def dumpLog(self):
        """Dumps all the information to a file to report bugs"""

        dumpath = Path(self.conf.cachepath, 'dumplogs')
        dumpath.mkdir(parents=True, exist_ok=True)

        dumplog = Path(dumpath, datetime.utcnow().strftime('%Y%m%d_%H%M%SZdump.txt'))

        f = open(dumplog, 'w')

        import platform
        from pprint import pprint

        xpver, sdkver, hid = XPLMGetVersions()
        output = ['--- Platform Info ---\n',
                  f"Plugin version: {self.conf.__VERSION__}\n",
                  f"Xplane Version: {round(xpver/1000, 3)}, SDK Version: {round(sdkver/100, 2)}\n",
                  f"Platform: {platform.platform()}\n",
                  f"Python version: {platform.python_version()}\n",
                  '\n--- Weather Status ---\n'
                  ]

        for line in self.weatherInfo():
            output.append(f"{line}\n")

        output += ['\n--- Weather Data ---\n']

        for line in output:
            f.write(line)

        pprint(self.weather.weatherData, f, width=160)
        f.write('\n--- Transition data Data --- \n')
        pprint(c.transrefs, f, width=160)

        f.write('\n--- Weather Datarefs --- \n')
        # Dump winds datarefs
        datarefs = {'winds': self.weather.winds,
                    'clouds': self.weather.clouds,
                    }

        pdrefs = {}
        for item in datarefs:
            pdrefs[item] = []
            for i in range(len(datarefs[item])):
                wdata = {}
                for key in datarefs[item][i]:
                    wdata[key] = datarefs[item][i][key].value
                pdrefs[item].append(wdata)
        pprint(pdrefs, f, width=160)

        vars = {}
        f.write('\n')
        for var in self.weather.__dict__:
            if isinstance(self.weather.__dict__[var], EasyDref):
                vars[var] = self.weather.__dict__[var].value
        vars['altitude'] = self.altdr.value
        pprint(vars, f, width=160)

        f.write('\n--- Overrides ---\n')

        vars = {}
        f.write('\n')
        for var in self.data.__dict__:
            if 'override' in var and isinstance(self.data.__dict__[var], EasyDref):
                vars[var] = self.data.__dict__[var].value
        pprint(vars, f, width=160)

        f.write('\n--- Configuration ---\n')
        vars = {}
        for var in self.conf.__dict__:
            if type(self.conf.__dict__[var]) in (str, int, float, list, tuple, dict):
                vars[var] = self.conf.__dict__[var]
        pprint(vars, f, width=160)

        # Append tail of PythonInterface log files
        logfiles = ['PythonInterfaceLog.txt',
                    'PythonInterfaceOutput.txt',
                    Path('noaweather', 'weatherServerLog.txt'),
                    ]

        for logfile in logfiles:
            try:
                import XPPython
                filepath = Path(XPPython.PLUGINSPATH, logfile)
            except ImportError:
                filepath = Path(self.conf.syspath, 'Resources', 'plugins', 'PythonScripts', logfile)
            if filepath.is_file():

                lfsize = filepath.stat().st_size
                lf = open(filepath, 'r')
                lf.seek(0, os.SEEK_END)
                lf.seek(lf.tell() - c.limit(1024 * 6, lfsize), os.SEEK_SET)
                f.write(f"\n--- {logfile} ---\n\n")
                for line in lf.readlines():
                    f.write(line.strip('\r'))
                lf.close()

        f.close()

        return dumplog

    def floopCallback(self, elapsedMe, elapsedSim, counter, refcon):
        """Flight Loop Callback"""

        # Update status window
        if self.aboutWindow and XPIsWidgetVisible(self.aboutWindowWidget):
            self.updateStatus()

        # Handle server misc requests
        if len(self.weather.queryResponses):
            msg = self.weather.queryResponses.pop()
            if 'metar' in msg:
                self.metarQueryCallback(msg)

        ''' Return if the plugin is disabled '''
        if not self.conf.enabled:
            return -1

        ''' Request new data from the weather server (if required)'''
        self.flcounter += elapsedMe
        self.fltime += elapsedMe
        if self.flcounter > self.conf.parserate and self.weather.weatherClientThread:

            lat, lon = round(self.latdr.value, 1), round(self.londr.value, 1)

            # Request data on postion change, every 0.1 degree or 60 seconds
            if (lat, lon) != (self.weather.last_lat, self.weather.last_lon) or (self.fltime - self.lastParse) > 60:
                self.weather.last_lat, self.weather.last_lon = lat, lon

                self.weather.weatherClientSend(f"?{round(lat, 2)}|{round(lon, 2)}\n")

                self.flcounter = 0
                self.lastParse = self.fltime

        # Store altitude
        self.weather.alt = self.altdr.value

        wdata = self.weather.weatherData

        ''' Return if there's no weather data'''
        if self.weather.weatherData is False:
            return -1

        ''' Data set on new weather Data '''
        if self.weather.newData:
            rain, ts, friction, patchy = 0, 0, 0, 0

            # Clear transitions on airport load
            if self.newAptLoaded:
                c.transitionClearReferences()
                c.randRefs = {}
                self.newAptLoaded = False

            if not self.conf.metar_disabled:
                # Set metar values
                if 'visibility' in wdata['metar']:
                    visibility = c.limit(wdata['metar']['visibility'], self.conf.max_visibility)

                    if not self.data.override_visibility.value:
                        self.weather.visibility.value = visibility

                    self.data.visibility.value = visibility

                if 'precipitation' in wdata['metar']:
                    p = wdata['metar']['precipitation']
                    for el in p:
                        precip, wet, is_patchy = c.metar2xpprecipitation(el, p[el]['int'], p[el]['int'], p[el]['recent'])

                        if precip is not False:
                            rain = precip
                        if wet is not False:
                            friction = wet
                        if is_patchy is not False:
                            patchy = 1

                    if 'TS' in p:
                        ts = 0.5
                        if p['TS']['int'] == '-':
                            ts = 0.25
                        elif p['TS']['int'] == '+':
                            ts = 1

                if not self.data.override_precipitation.value:
                    self.weather.thunderstorm.value = ts
                    self.weather.precipitation.value = rain

                self.data.metar_precipitation.value = rain
                self.data.metar_thunderstorm.value = ts

                if not self.data.override_runway_friction.value:
                    self.weather.runwayFriction.value = friction

                if not self.data.override_runway_is_patchy.value:
                    self.weather.patchy.value = patchy

                self.data.metar_runwayFriction.value = friction
                self.weather.patchy.value = patchy

            self.weather.newData = False

            # Set clouds
            if self.conf.set_clouds:
                if self.conf.opt_clouds_update:
                    self.weather.setCloudsOpt(ts=ts)
                else:
                    self.weather.setClouds()

            # Update Dataref data
            self.data.updateData(wdata)

        ''' Data enforced/interpolated/transitioned on each cycle '''
        if not self.data.override_pressure.value and self.conf.set_pressure:
            # Set METAR or GFS pressure
            if 'pressure' in wdata['metar'] and wdata['metar']['pressure'] is not False:
                self.weather.setPressure(wdata['metar']['pressure'], elapsedMe)
            elif self.conf.set_pressure and 'pressure' in wdata['gfs']:
                self.weather.setPressure(wdata['gfs']['pressure'], elapsedMe)

        # Set winds
        if (not self.data.override_winds.value and self.conf.set_wind
                and 'winds' in wdata['gfs'] and len(wdata['gfs']['winds'])):
            self.weather.setWinds(wdata['gfs']['winds'], elapsedMe)

        # Set Atmosphere
        if (not self.data.override_tropo.value and self.conf.set_tropo
                and 'tropo' in wdata['gfs'] and 'temp' in wdata['gfs']['tropo']):
            self.weather.setTropo(wdata['gfs']['tropo'], elapsedMe)

        # Set turbulence
        if not self.data.override_turbulence.value and self.conf.set_turb:
            self.weather.setTurbulence(wdata['wafs'], elapsedMe)

        '''create thermals / uplift in thunderstorms'''
        if not self.data.override_thermals.value and self.conf.set_thermals:
            self.weather.setThermals()

        return -1

    def XPluginStop(self):

        # Destroy windows
        if self.aboutWindow:
            XPDestroyWidget(self.aboutWindowWidget, 1)
        if self.metarWindow:
            XPDestroyWidget(self.metarWindowWidget, 1)

        self.metarWindowCMD.destroy()

        XPLMUnregisterFlightLoopCallback(self.floop, 0)

        # kill weather server/client
        self.weather.shutdown()

        XPLMDestroyMenu(self.mMain)
        self.conf.pluginSave()

        # Unregister datarefs
        EasyDref.cleanup()

    def XPluginEnable(self):
        return 1

    def XPluginDisable(self):
        pass

    def XPluginReceiveMessage(self, inFromWho, inMessage, inParam):
        if (inParam is None or inParam == XPLM_PLUGIN_XPLANE) and inMessage == XPLM_MSG_AIRPORT_LOADED:
            self.weather.startWeatherClient()
            self.newAptLoaded = True
        elif inMessage == (0x8000000 | 8090) and inParam == 1:
            # inSimUpdater wants to shutdown
            self.XPluginStop()
