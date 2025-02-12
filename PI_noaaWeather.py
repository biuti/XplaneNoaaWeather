"""
X-plane NOAA GFS weather plugin.

Development version for X-Plane 12


For support visit:
http://forums.x-plane.org/index.php?showtopic=72313

Github project page:
https://github.com/biuti/XplaneNoaaWeather

Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2024 Antonio Golfari
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

from noaaweather import xp, widget


# XPython3 required plugin parameters
name = "NOAA Weather"
sig = "noaaweather.xppython3"
desc = "NOAA GFS Weather Data in X-Plane 12"

class PythonInterface(widget.Widget):
    """
    Xplane plugin
    """

    def __init__(self):

        super().__init__()
        self.name = f"{name} - {self.conf.__VERSION__}"
        self.sig = "noaaweather.xppython3"
        self.desc = "NOAA GFS Weather Data in X-Plane"

    def floopCallback(self, elapsedMe, elapsedSim, counter, refcon):
        """Flight Loop Callback"""

        # Update status window
        if (
            (self.info_window and xp.isWidgetVisible(self.info_window_widget))
            or (self.config_window and xp.isWidgetVisible(self.config_window_widget))
        ):
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

            lat, lon = round(self.data.latdr.value, 1), round(self.data.londr.value, 1)

            # Request data on postion change, every 0.1 degree or 60 seconds
            if (lat, lon) != (self.weather.last_lat, self.weather.last_lon) or (self.fltime - self.lastParse) > 60:
                self.weather.last_lat, self.weather.last_lon = lat, lon
                hdg = round(self.data.track)
                gs = round(self.data.groundspeed)

                self.weather.weatherClientSend(f"?{round(lat, 2)}|{round(lon, 2)}|{hdg}|{gs}\n")

                self.flcounter = 0
                self.lastParse = self.fltime

        # Store altitude
        self.weather.alt = self.data.altdr.value

        wdata = self.weather.weatherData
        # Return if there's no weather data
        if wdata is False:
            return -1

        if self.conf.use_real_weather_data and self.conf.download_GFS:
            if self.newAptLoaded:
                xp.log(f" *** NEW APT LOADED ***")
                self.weather.reset_weather()
                self.newAptLoaded = False
            if self.conf.set_snow:
                # ATM we need to overwrite dref value every cycle
                self.weather.setSnow(elapsed=elapsedMe)
            if self.weather.newData:
                # Real Weather active
                # check Dref values, RW overwrites them. Probably needed for any change to Real Weather data
                # looking at actual weather, does not seem to have any impact tho.
                # if not self.data.metar_runwayFriction.value or self.weather.runwayFriction.value != self.data.metar_runwayFriction.value:
                #     self.data.metar_runwayFriction.value = self.weather.runwayFriction.value
                # if self.weather.runwayFriction.value > 6:
                #     # set runway friction to Puddly, to avoid extreme and unrealistic slippery conditions.
                #     self.weather.friction = self.weather.runwayFriction.value
                #     self.weather.runwayFriction.value = 6 if self.weather.friction < 10 else 9
                # xp.log(f"New GFS data downloaded: {self.weather.data}")
                # Clear transitions on airport load
                pass

        ''' Data set on new weather Data '''
        if not self.conf.use_real_weather_data and self.weather.newData:
            pass
            # Update Dataref data
            # self.data.updateData(wdata)

        self.weather.newData = False
        return -1

    def XPluginStart(self):

        return self.name, self.sig, self.desc

    def XPluginStop(self):

        # kill flightloop
        xp.destroyFlightLoop(self.loop_id)

        # save windows position
        self.save_windows_position()

        # kill widget windows and menu
        self.shutdown_widget()

        # kill weather server/client
        self.weather.shutdown()

        self.conf.pluginSave()

        # Unregister datarefs
        self.data.cleanup()

    def XPluginEnable(self):
        # floop
        self.floop = self.floopCallback
        self.loop_id = xp.createFlightLoop(self.floop, phase=0)
        xp.scheduleFlightLoop(self.loop_id, interval=-1)
        return 1

    def XPluginDisable(self):
        pass

    def XPluginReceiveMessage(self, inFromWho, inMessage, inParam):
        if (inParam is None or inParam == xp.PLUGIN_XPLANE) and inMessage == xp.MSG_AIRPORT_LOADED:
            self.weather.startWeatherClient()
            self.newAptLoaded = True
        elif inMessage == (0x8000000 | 8090) and inParam == 1:
            # inSimUpdater wants to shutdown
            self.XPluginStop()
