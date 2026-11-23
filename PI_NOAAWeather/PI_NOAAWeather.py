"""
X-plane NOAA GFS weather plugin.

Development version for X-Plane 12


For support visit:
http://forums.x-plane.org/index.php?showtopic=72313

Github project page:
https://github.com/biuti/XplaneNoaaWeather

Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2026 Antonio Golfari
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

from .scripts import xp, widget, log


# XPython3 required plugin parameters
NAME = "NOAA Weather"
SIG = "noaaweather.xppython3"
DESC = "NOAA GFS Weather Data in X-Plane 12"

class PythonInterface(widget.Widget):
    """
    Xplane plugin
    """

    def __init__(self) -> None:

        super().__init__()
        self.name = f"{NAME} - {self.conf.__VERSION__}"
        self.sig = SIG
        self.desc = DESC
        self.loop_id = None
        self.stopped = False

    def floopCallback(self, elapsedMe, elapsedSim, counter, refcon) -> int:
        """Flight Loop Callback"""

        # Update status window
        if (
            (self.info_window and xp.isWidgetVisible(self.info_window_widget))
            or (self.config_window and xp.isWidgetVisible(self.config_window_widget))
        ):
            self.updateStatus()

        # Handle server misc requests
        if len(self.weather.query_response):
            msg = self.weather.query_response.pop()
            if 'metar' in msg:
                self.metarQueryCallback(msg)

        ''' Return if the plugin is disabled '''
        if not self.conf.enabled:
            return -1

        ''' Request new data from the weather server (if required)'''
        self.flcounter += elapsedMe
        self.fltime += elapsedMe
        if self.flcounter > self.conf.parse_rate and self.weather.weather_client_thread:

            lat, lon = round(self.data.latdr.value, 1), round(self.data.londr.value, 1)

            # Request data on postion change, every 0.1 degree or 60 seconds
            if (lat, lon) != (self.weather.last_lat, self.weather.last_lon) or (self.fltime - self.lastParse) > 60:
                self.weather.last_lat, self.weather.last_lon = lat, lon
                hdg = round(self.data.track)
                gs = round(self.data.groundspeed)

                self.weather.weather_client_send(f"?{round(lat, 2)}|{round(lon, 2)}|{hdg}|{gs}\n")

                self.flcounter = 0
                self.lastParse = self.fltime

        # Store altitude
        self.weather.alt = self.data.altdr.value

        wdata = self.weather.weather_data
        # Return if there's no weather data
        if wdata is False:
            return -1

        if self.conf.use_real_weather_data:
            if self.newAptLoaded:
                log.info(" *** NEW APT LOADED ***")
                self.weather.reset_weather()
                self.newAptLoaded = False
            if self.conf.set_snow:
                # ATM we need to overwrite dref value every cycle
                self.weather.set_snow(elapsed=elapsedMe)
            if self.conf.set_friction:
                self.weather.set_runway_friction(elapsed=elapsedMe)
            if self.weather.new_data:
                # Real Weather active
                pass

        ''' Data set on new weather Data '''
        if not self.conf.use_real_weather_data and self.weather.new_data:
            # Update Dataref data
            # self.data.updateData(wdata)
            pass

        self.weather.new_data = False
        return -1

    def XPluginStart(self) -> tuple[str, str, str]:
        self.stopped = False
        return self.name, self.sig, self.desc

    def XPluginEnable(self) -> int:
        # floop
        self.floop = self.floopCallback
        if self.loop_id is None:
            self.loop_id = xp.createFlightLoop(self.floop, phase=1)
        xp.scheduleFlightLoop(self.loop_id, interval=-1)
        return 1

    def XPluginDisable(self) -> None:
        # no flight loop while disabled: Enable creates it again
        self.destroy_flight_loop()

    def destroy_flight_loop(self) -> None:
        if self.loop_id is not None:
            xp.destroyFlightLoop(self.loop_id)
            self.loop_id = None

    def XPluginReceiveMessage(self, inFromWho, inMessage, inParam) -> None:
        if (inParam is None or inParam == xp.PLUGIN_XPLANE) and inMessage == xp.MSG_AIRPORT_LOADED:
            self.weather.start_weather_client()
            self.newAptLoaded = True
        elif inMessage == (0x8000000 | 8090) and inParam == 1:
            # inSimUpdater wants to shutdown
            self.XPluginStop()

    def XPluginStop(self) -> None:
        # the updater message above stops us, then X-Plane calls this again: only the first call does the work
        if self.stopped:
            return
        self.stopped = True
        # kill flightloop
        self.destroy_flight_loop()
        # save windows position
        self.save_windows_position()
        # kill widget windows and menu
        self.shutdown_widget()
        # kill weather server/client
        self.weather.shutdown()
        self.conf.save_plugin_settings()
