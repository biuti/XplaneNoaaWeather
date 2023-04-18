"""
X-plane NOAA GFS weather plugin.

Development version for X-Plane 12


For support visit:
http://forums.x-plane.org/index.php?showtopic=72313

Github project page:
https://github.com/biuti/XplaneNoaaWeather

Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2023 Antonio Golfari
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
# import xp

import time
import pickle
import socket
import threading
import subprocess
import os

from datetime import datetime
from pathlib import Path
from noaaweather import Conf, c, EasyCommand, util, dref, weather


class PythonInterface:
    """
    Xplane plugin
    """

    def __init__(self):
        self.syspath = []
        self.conf = Conf(XPLMGetSystemPath(self.syspath)[:-1])
        self.name = "NOAA Weather - " + self.conf.__VERSION__
        self.sig = "noaaweather.xppython3"
        self.desc = "NOAA GFS Weather Data in X-Plane"
        self.windowId = None

    def XPluginStart(self):
        # self.syspath = []
        # self.conf = Conf(XPLMGetSystemPath(self.syspath)[:-1])
        print(f"Conf is {self.conf}")

        self.data = dref.Dref()
        self.weather = weather.Weather(self.conf, self.data)

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

        return self.name, self.sig, self.desc

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

        # METAR decoding
        XPCreateWidget(x, y, x + 20, y - 20, 1, 'METAR Decoding', 0, window, xpWidgetClass_Caption)
        self.decodeCheck = XPCreateWidget(x + 120, y, x + 140, y - 20, 1, '', 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.decodeCheck, xpProperty_ButtonType, xpRadioButton)
        XPSetWidgetProperty(self.decodeCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        XPSetWidgetProperty(self.decodeCheck, xpProperty_ButtonState, self.conf.metar_decode)
        y -= 40

        if self.conf.real_weather_enabled:
            # WAFS download enable
            # XPCreateWidget(x, y, x + 20, y - 20, 1, 'WAFS download', 0, window, xpWidgetClass_Caption)
            # self.WAFSCheck = XPCreateWidget(x + 120, y, x + 140, y - 20, 1, '', 0, window, xpWidgetClass_Button)
            # XPSetWidgetProperty(self.WAFSCheck, xpProperty_ButtonType, xpRadioButton)
            # XPSetWidgetProperty(self.WAFSCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
            # XPSetWidgetProperty(self.WAFSCheck, xpProperty_ButtonState, self.conf.download_WAFS)
            # y -= 40
            pass

        else:
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

        # # Accumulated snow
        # XPCreateWidget(x + 5, y, x + 20, y - 20, 1, 'Snow Depth', 0, window, xpWidgetClass_Caption)
        # self.snowCheck = XPCreateWidget(x + 120, y, x + 140, y - 20, 1, '', 0, window, xpWidgetClass_Button)
        # XPSetWidgetProperty(self.snowCheck, xpProperty_ButtonType, xpRadioButton)
        # XPSetWidgetProperty(self.snowCheck, xpProperty_ButtonBehavior, xpButtonBehaviorCheckBox)
        # XPSetWidgetProperty(self.snowCheck, xpProperty_ButtonState, self.conf.set_snow)
        # y -= 40

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

        if not self.conf.real_weather_enabled:
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

        # DumpLog Button
        self.dumpLogButton = XPCreateWidget(x + 10, y, x + 130, y - 20, 1, "DumpLog", 0, window, xpWidgetClass_Button)
        XPSetWidgetProperty(self.dumpLogButton, xpProperty_ButtonType, xpPushButton)

        self.dumpLabel = XPCreateWidget(x + 150, y, x + 420, y - 20, 1, '', 0, window, xpWidgetClass_Caption)

        y -= 45
        subw = XPCreateWidget(x - 10, y, x2 - 20 + 10, y2 + 15, 1, "", 0, window, xpWidgetClass_SubWindow)
        x += 10
        # Set the style to sub window

        y -= 5
        sysinfo = [
            f"X-Plane 12 NOAA Weather: {self.conf.__VERSION__}",
            '(c) antonio golfari 2023',
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

            if inParam1 == self.aboutVisit:
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
                self.conf.metar_decode = XPGetWidgetProperty(self.decodeCheck, xpProperty_ButtonState, None)
                if self.conf.real_weather_enabled:
                    # nothing to do now
                    # self.conf.download_WAFS = XPGetWidgetProperty(self.WAFSCheck, xpProperty_ButtonState, None)
                    pass
                else:
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
                            self.data.winds[i]['turb'].value = 0

                    buff = XPGetWidgetDescriptor(self.maxCloudHeightInput)
                    self.conf.max_cloud_height = c.convertFromInput(buff, 'f2m', min=c.f2m(2000))

                    buff = XPGetWidgetDescriptor(self.maxVisInput)
                    self.conf.max_visibility = c.convertFromInput(buff, 'sm2m')

                # self.conf.set_snow = XPGetWidgetProperty(self.snowCheck, xpProperty_ButtonState, None)

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
                self.conf.metar_use_xp12 = XPGetWidgetProperty(self.xp12MetarCheck, xpProperty_ButtonState, None)

                # Save config and tell server to reload it
                self.conf.pluginSave()
                print(f"Config saved. Weather client reloading ...")
                self.weather.weatherClientSend('!reload')

                # If metar source has changed tell server to reinit metar database
                if self.conf.metar_source != prev_metar_source:
                    self.weather.weatherClientSend('!resetMetar')

                # If metar source for METAR.rwx file has changed tell server to reinit rwmetar database
                if self.conf.metar_use_xp12 != prev_file_source:
                    self.weather.weatherClientSend('!resetRWMetar')

                self.weather.startWeatherClient()
                self.aboutWindowUpdate()

                # Reset things
                self.weather.newData = True
                self.newAptLoaded = True

                return 1

            if inParam1 == self.dumpLogButton:
                print(f"Creating dumplog file...")
                dumpfile = self.weather.dumpLog()
                XPSetWidgetDescriptor(self.dumpLabel, f"created {dumpfile.name} in cache folder")
                return 1
        return 0

    def aboutWindowUpdate(self):
        XPSetWidgetProperty(self.enableCheck, xpProperty_ButtonState, self.conf.enabled)
        XPSetWidgetProperty(self.decodeCheck, xpProperty_ButtonState, self.conf.metar_decode)
        XPSetWidgetDescriptor(self.stationIgnoreInput, ' '.join(self.conf.ignore_metar_stations))
        XPSetWidgetProperty(self.autoMetarCheck, xpProperty_ButtonState, self.conf.metar_ignore_auto)
        XPSetWidgetProperty(self.xp12MetarCheck, xpProperty_ButtonState, self.conf.metar_use_xp12)

        if self.conf.real_weather_enabled:
            # nothing to do now
            # XPSetWidgetProperty(self.WAFSCheck, xpProperty_ButtonState, self.conf.download_WAFS)
            pass

        else:
            XPSetWidgetProperty(self.windsCheck, xpProperty_ButtonState, self.conf.set_wind)
            XPSetWidgetProperty(self.cloudsCheck, xpProperty_ButtonState, self.conf.set_clouds)
            XPSetWidgetProperty(self.optUpdCheck, xpProperty_ButtonState, self.conf.opt_clouds_update)
            XPSetWidgetProperty(self.tempCheck, xpProperty_ButtonState, self.conf.set_temp)
            XPSetWidgetProperty(self.turbCheck, xpProperty_ButtonState, self.conf.set_turb)
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

    def weatherInfo(self) -> list[str]:
        """Return an array of strings with formatted weather data"""
        verbose = self.conf.verbose
        sysinfo = [f"XPNoaaWeather for XP12 {self.conf.__VERSION__} Status:"]

        if not self.weather.weatherData:
            sysinfo += ['* Data not ready. Please wait...']
        else:
            wdata = self.weather.weatherData
            if 'info' in wdata:
                sysinfo += [
                    '   LAT: %.2f/%.2f LON: %.2f/%.2f FL: %02.f MAGNETIC DEV: %.2f' % (
                        self.data.latdr.value, wdata['info']['lat'], self.data.londr.value, wdata['info']['lon'],
                        c.m2ft(self.data.altdr.value) / 100, self.data.mag_deviation.value)
                ]
                if self.data.xpWeather.value != 1:
                    sysinfo += [f"   XP12 Real Weather is not active (value = {self.data.xpWeather.value})"]
                elif 'None' in wdata['info']['gfs_cycle']:
                    sysinfo += ['   XP12 is still downloading weather info ...']
                elif self.conf.real_weather_enabled:
                    sysinfo += [f"   GFS Cycle: {wdata['info']['rw_gfs_cycle']}"]
                else:
                    sysinfo += [f"   GFS Cycle: {wdata['info']['gfs_cycle']}"]

            if 'metar' in wdata and 'icao' in wdata['metar']:
                sysinfo += ['']
                # Split metar if needed
                icao = wdata['metar']['icao']
                metar = f"{self.conf.metar_source} METAR: {icao} {wdata['metar']['metar']}"
                sysinfo += util.split_text(metar)


                if self.conf.metar_decode:
                    # METAR Decoding Section
                    sysinfo += [
                        f"   Apt altitude: {int(c.m2ft(wdata['metar']['elevation']))}ft, "
                        f"Apt distance: {round(wdata['metar']['distance'] / 1000, 1)}km",
                        f"   Temp: {round(wdata['metar']['temperature'][0])}, "
                        f"Dewpoint: {round(wdata['metar']['temperature'][1])}, "
                        f"Visibility: {round(wdata['metar']['visibility'])}m, "
                        f"Press: {wdata['metar']['pressure']:.2f} inhg ({c.inHg2mb(wdata['metar']['pressure']):.1f} mb)"
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

                if 'rwmetar' in wdata and self.conf.real_weather_enabled:
                    if not wdata['rwmetar']['file_time']:
                        sysinfo += ['XP12 REAL WEATHER METAR:', '   no METAR file, still downloading...']
                    else:
                        sysinfo += [f"XP12 REAL WEATHER METAR ({wdata['rwmetar']['file_time']}):"]
                        for line in wdata['rwmetar']['reports'][:2]:
                            sysinfo += util.split_text(line, 3)
                    # check actual pressure and adjusted friction
                    pressure = self.data.pressure.value / 100  # mb
                    pressure_inHg = c.mb2inHg(pressure)
                    line = f"   Pressure: {pressure:.1f}mb ({pressure_inHg:.2f}inHg)"
                    vis_m, vis_sm = round(c.sm2m(self.data.visibility.value)), round(self.data.visibility.value, 1)
                    line += f" | Visibility: {vis_m}m ({vis_sm}sm)"
                    friction = self.data.runwayFriction.value
                    # metar_friction = self.weather.friction
                    line += f" | Runway Friction: {friction:02}"
                    # if friction != metar_friction:
                    #     line += f" (original {metar_friction:02})"
                    sysinfo += [line, '']

            if not self.conf.meets_wgrib2_requirements:
                '''not a compatible OS with wgrib2'''
                sysinfo += ['',
                            '*** *** WGRIB2 decoder not available for your OS version *** ***',
                            'Windows 7 or above, MacOS 10.14 or above, Linux kernel 4.0 or above.',
                            ''
                            ]
            elif 'gfs' not in wdata:
                sysinfo += ['',
                            '*** An error has occurred ***',
                            'No GFS data is available, check log',
                            ''
                            ]
            else:
                if not wdata['gfs']:
                    sysinfo += [
                        'The plugin populates METAR.rwx file, monitors XP12 Real Weather data.',
                        ''
                    ]
                else:
                    # GFS data download for testing is enabled
                    sysinfo += [
                        '*** *** Experimental GFS weather data download *** ***',
                        'The plugin populates METAR.rwx file, monitors XP12 Real Weather, adds GFS data in options.',
                        ''
                    ]
                    gfs = wdata['gfs']
                    if 'surface' in gfs and len(gfs['surface']):
                        s = gfs['surface']
                        snow_depth = 'na' if s.get('snow') is None else round(s.get('snow'), 2)
                        acc_precip = 'na' if s.get('acc_precip') is None else round(s.get('acc_precip'), 2)
                        sysinfo += [
                            f"Snow depth (m): {snow_depth}  |  Accumulated precip. (kg/sqm): {acc_precip}",
                            ''
                        ]

                if 'rw' in wdata and self.conf.real_weather_enabled:
                    # XP12 Real Weather is enabled
                    rw = wdata['rw']
                    if 'winds' in rw:
                        sysinfo += ['XP12 REAL WEATHER WIND LAYERS: FL | HDG | KT | TEMP | DEV']
                        wlayers = ''
                        out = []
                        for i, layer in enumerate(rw['winds'], 1):
                            alt, hdg, speed, extra = layer
                            wlayers += f"    F{c.m2fl(alt):03.0F} | {hdg:03.0F} | {int(speed):03}kt" \
                                       f" | {round(c.kel2cel(extra['temp'])):02} | {round(c.kel2cel(extra['dev'])):02}"
                            if i % 3 == 0 or i == len(rw['winds']):
                                out.append(wlayers)
                                wlayers = ''
                        sysinfo += out

                    if 'tropo' in rw and rw['tropo'].values():
                        alt, temp, dev = rw['tropo'].values()
                        if alt and temp and dev:
                            sysinfo += [f"TROPO LIMIT: {round(alt)}m (F{c.m2fl(alt)}) "
                                        f"temp {round(c.kel2cel(temp)):02}C ISA Dev {round(c.kel2cel(dev)):02}C"]

                    if 'clouds' in rw:
                        sysinfo += ['XP12 REAL WEATHER CLOUD LAYERS  FLBASE | FLTOP | COVER']
                        clayers = ''
                        clouds = [el for el in rw['clouds'] if el[0] > 0]
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

                    if 'turbulence' in rw:
                        wafs = rw['turbulence']
                        tblayers = ''
                        out = []
                        cycle = 'not ready yet' if 'None' in wdata['info']['rw_wafs_cycle'] else wdata['info']['rw_wafs_cycle']
                        sysinfo += [f"XP12 REAL WEATHER TURBULENCE ({wdata['info']['rw_wafs_cycle']}):  "
                                    f"FL | SEV (val*10, max {self.conf.max_turbulence * 10}) "]
                        for i, layer in enumerate(wafs, 1):
                            fl = c.m2fl(layer[0])
                            value = round(layer[1] * 10, 2) if layer[1] < self.conf.max_turbulence else '*'
                            tblayers += f"    {fl:03}|{value}"
                            if i % 9 == 0 or i == len(wafs):
                                out.append(tblayers)
                                tblayers = ''
                        sysinfo += out
                    if self.conf.download_WAFS and 'wafs' in wdata and 'turbulence' in wdata['wafs']:
                        wafs = wdata['wafs']['turbulence']
                        tblayers = ''
                        out = []
                        sysinfo += [f"NOAA Downloaded WAFS data ({wdata['info']['wafs_cycle']}):  "
                                    f"FL | SEV (val*10, max {self.conf.max_turbulence * 10}) "]
                        for i, layer in enumerate(wafs, 1):
                            fl = c.m2fl(layer[0])
                            value = round(layer[1] * 10, 2) if layer[1] < self.conf.max_turbulence else '*'
                            tblayers += f"    {fl:03}|{value}"
                            if i % 9 == 0 or i == len(wafs):
                                out.append(tblayers)
                                tblayers = ''
                        sysinfo += out
                    sysinfo += ['']

                else:
                    '''Normal GFS mode'''
                    pass
                    # if 'winds' in gfs:
                    #     sysinfo += ['', f"GFS WIND LAYERS: {len(gfs['winds'])} FL|HDG|KT|TEMP|DEV"]
                    #     wlayers = ''
                    #     i = 0
                    #     for layer in gfs['winds']:
                    #         i += 1
                    #         alt, hdg, speed, extra = layer
                    #         wlayers += f"   F{c.m2fl(alt):03}|{hdg:03}|{speed:02}kt|" \
                    #                    f"{round(c.kel2cel(extra['temp'])):02}|{round(c.kel2cel(extra['dev'])):02}"
                    #         if i > 3:
                    #             i = 0
                    #             sysinfo += [wlayers]
                    #             wlayers = ''

                    # if 'clouds' in gfs:
                    #     clouds = 'GFS CLOUDS  FLBASE|FLTOP|COVER'
                    #     for layer in gfs['clouds']:
                    #         base, top, cover = layer
                    #         if base > 0:
                    #             clouds += f"    {c.m2fl(base):03} | {c.m2fl(top):03} | {cover}%"
                    #     sysinfo += [clouds]

                    # if 'tropo' in gfs:
                    #     alt, temp, dev = gfs['tropo'].values()
                    #     if alt and temp and dev:
                    #         sysinfo += [f"TROPO LIMIT: {round(alt)}m "
                    #                     f"temp {round(c.kel2cel(temp)):02}C ISA Dev {round(c.kel2cel(dev)):02}C"]

                    # if 'wafs' in wdata:
                    #     tblayers = ''
                    #     for layer in wdata['wafs']:
                    #         tblayers += f"   {c.m2fl(layer[0]):03}|{round(layer[1], 2)}" \
                    #                     f"{'*' if layer[1]>=self.conf.max_turbulence else ''}"

                    #     sysinfo += [f"WAFS TURBULENCE ({len(wdata['wafs'])}): FL|SEV (max {self.conf.max_turbulence}) ",
                    #                 tblayers]

                    # sysinfo += ['']
                    # if 'thermals' in wdata:
                    #     t = wdata['thermals']

                    #     if not t['grad']:
                    #         s = "THERMALS: N/A"
                    #     else:
                    #         if t['grad'] == "TS":
                    #             s = "THERMALS (TS mode): "
                    #         else:
                    #             s = f"THERMALS: grad. {round(t['grad'], 2)} ºC/100m, "
                    #         s += f"h {round(t['alt'])}m, p {round(t['prob']*100)}%, r {round(t['rate']*0.00508)}m/s"
                    #     sysinfo += [s]

                    # if 'cloud_info' in wdata and self.conf.opt_clouds_update:
                    #     ci = wdata['cloud_info']
                    #     s = 'OPTIMISED FOR BEST PERFORMANCE' if ci['OVC'] and ci['above_clouds'] else 'MERGED'
                    #     sysinfo += [f"CLOUD LAYERS MODE: {s}"]
                    #     if verbose:
                    #         sysinfo += [f"{ci['layers']}"]

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

        # Register our query widget handler
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
            # adding source and internal XP12 METARs
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

            lat, lon = round(self.data.latdr.value, 1), round(self.data.londr.value, 1)

            # Request data on postion change, every 0.1 degree or 60 seconds
            if (lat, lon) != (self.weather.last_lat, self.weather.last_lon) or (self.fltime - self.lastParse) > 60:
                self.weather.last_lat, self.weather.last_lon = lat, lon

                self.weather.weatherClientSend(f"?{round(lat, 2)}|{round(lon, 2)}\n")

                self.flcounter = 0
                self.lastParse = self.fltime

        # Store altitude
        self.weather.alt = self.data.altdr.value

        wdata = self.weather.weatherData

        ''' Return if there's no weather data'''
        if wdata is False:
            return -1

        if self.conf.real_weather_enabled and self.weather.newData:
            # Real Weather active
            # check Dref values, RW overwrites them. Probably needed for any change to Real Weather data
            # looking at actual weather, does not seem to have any impact tho.
            # if not self.data.metar_runwayFriction.value or self.weather.runwayFriction.value != self.data.metar_runwayFriction.value:
            #     self.data.metar_runwayFriction.value = self.weather.runwayFriction.value
            # if self.weather.runwayFriction.value > 6:
            #     # set runway friction to Puddly, to avoid extreme and unrealistic slippery conditions.
            #     self.weather.friction = self.weather.runwayFriction.value
            #     self.weather.runwayFriction.value = 6 if self.weather.friction < 10 else 9
            pass

        ''' Data set on new weather Data '''
        if not self.conf.real_weather_enabled and self.weather.newData:
            pass
            # Update Dataref data
            # self.data.updateData(wdata)

        self.weather.newData = False
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
        self.data.cleanup()

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
