"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2021-2023 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import time

from . import xp, Conf, c, util, dref, weather
from .easydref import EasyCommand


class Widget:

    # Constants
    font_width, font_height, _ = xp.getFontDimensions(xp.Font_Basic)
    line_height = font_height + 8
    window_margin = 10
    # xp.log(f"font: {font_width} x {font_height} | line = {line_height}")

    # Info Window Definition
    info_title = "X-Plane 12 NOAA GFS Weather"
    info_width = 560
    info_height = 640
    info_lines = int((info_height - 2 * window_margin) / line_height) - 1
    info_line_chars = int((info_width - 2 * window_margin) / font_width) - 4

    # METAR Window Definition
    metar_title = "METAR Request"
    metar_width = 480
    metar_height = 240
    metar_widget_width = int(metar_width - 2 * window_margin)
    metar_line_chars = int(metar_widget_width / font_width)

    # Config Window Definition
    config_title = "NOAA Weather Configuration"
    config_width = 640
    config_height = 480
    config_line_chars = int((config_width - 4 * window_margin) / font_width)

    def __init__(self):
        self.conf = Conf()
        self.weather = weather.Weather(self.conf)
        self.data = self.weather.data

        # Menu / About
        self.Mmenu = self.main_menu_callback

        # create main menu
        self.create_main_menu()

        self.info_window = False
        self.metar_window = False
        self.config_window = False

        # Register commands
        self.metarWindowCMD = EasyCommand(
            self, 'metar_query_window_toggle', 
            self.metarQueryWindowToggle,
            description="Toggle METAR query window."
        )

        # Flightloop counters
        self.flcounter = 0
        self.fltime = 1
        self.lastParse = 0

        self.newAptLoaded = False

    def create_main_menu(self):

        # create Menu
        self.main_menu = xp.createMenu('XP NOAA Weather', handler=self.main_menu_callback)

        # add Menu Items
        xp.appendMenuItem(self.main_menu, 'Weather Info', 1)
        xp.appendMenuItem(self.main_menu, 'Metar Query', 2)
        xp.appendMenuItem(self.main_menu, 'Configuration', 3)

    def main_menu_callback(self, menuRef, menuItem):
        """Main menu Callback"""

        if menuItem == 1:
            # Weather Info
            if not self.info_window:
                self.create_info_window(221, 640)
                self.info_window = True
            elif not xp.isWidgetVisible(self.info_window_widget):
                xp.showWidget(self.info_window_widget)
        elif menuItem == 2:
            # METAR query
            if not self.metar_window:
                self.create_metar_window()
            elif not xp.isWidgetVisible(self.metar_window_widget):
                xp.showWidget(self.metar_window_widget)
                xp.setKeyboardFocus(self.metarQueryInput)
        elif menuItem == 3:
            # configuration
            if not self.config_window:
                self.create_config_window()
            elif not xp.isWidgetVisible(self.config_window_widget):
                xp.showWidget(self.config_window_widget)

    def create_info_window(self, x: int = 200, y: int = 900):
        x2 = x + self.info_width
        y2 = y - self.info_height
        top = y - self.line_height - self.window_margin

        # Create the Main Widget window
        self.info_window_widget = xp.createWidget(x, y, x2, y2, 1, self.info_title, 1, 0, xp.WidgetClass_MainWindow)
        window = self.info_window_widget
        xp.setWidgetProperty(window, xp.Property_MainWindowType, xp.MainWindowStyle_Translucent)

        x += self.window_margin
        y = top

        # Add Close Box decorations to Info Widget
        xp.setWidgetProperty(window, xp.Property_MainWindowHasCloseBoxes, 1)

        # Create status captions
        self.info_captions = []
        while len(self.info_captions) < self.info_lines:
            cap = xp.createWidget(x, y, x + 40, y - self.line_height, 1, '--', 0, window, xp.WidgetClass_Caption)
            xp.setWidgetProperty(cap, xp.Property_CaptionLit, 1)
            xp.setWidgetProperty(cap, xp.Property_Font, xp.Font_Basic)
            self.info_captions.append(cap)
            y -= self.line_height

        self.updateStatus()

        # Register our widget handler
        self.infoWindowHandlerCB = self.infoWindowHandler
        xp.addWidgetCallback(window, self.infoWindowHandlerCB)

        self.info_window = True

    def create_metar_window(self, x: int = 10, y: int = 900):

        x2 = x + self.metar_width
        y2 = y - self.metar_height

        # Create the Main Widget window
        self.metar_window = True
        self.metar_window_widget = xp.createWidget(x, y, x2, y2, 1, self.metar_title, 1, 0, xp.WidgetClass_MainWindow)
        xp.setWidgetProperty(self.metar_window_widget, xp.Property_MainWindowType, xp.MainWindowStyle_Translucent)

        # Config Sub Window, style
        xp.setWidgetProperty(self.metar_window_widget, xp.Property_MainWindowHasCloseBoxes, 1)
        x += 10
        y -= self.line_height

        cap = xp.createWidget(x, y, x + 40, y - self.line_height, 1, 'Airport ICAO code:', 0, 
                              self.metar_window_widget, xp.WidgetClass_Caption)
        xp.setWidgetProperty(cap, xp.Property_CaptionLit, 1)

        y -= self.line_height
        # Airport input
        self.metarQueryInput = xp.createWidget(x, y, x + 120, y - self.line_height, 1, "", 0, 
                                               self.metar_window_widget, xp.WidgetClass_TextField)
        xp.setWidgetProperty(self.metarQueryInput, xp.Property_TextFieldType, xp.TextTranslucent)

        self.metarQueryButton = xp.createWidget(x + 140, y, x + 210, y - self.line_height, 1, "Request", 0, 
                                               self.metar_window_widget, xp.WidgetClass_Button)

        y -= self.line_height * 2
        # Help caption
        cap = xp.createWidget(x, y, x + 300, y - self.line_height, 1,
                             f"{self.conf.metar_source}:", 0, self.metar_window_widget, xp.WidgetClass_Caption)
        xp.setWidgetProperty(cap, xp.Property_CaptionLit, 1)

        y -= self.line_height
        # Query output
        self.metarQueryOutput = []
        for i in range(2):
            l = xp.createWidget(x , y, x + self.metar_widget_width, y - self.line_height, 0, "", 0, 
                                self.metar_window_widget, xp.WidgetClass_TextField)
            xp.setWidgetProperty(l, xp.Property_TextFieldType, xp.TextTranslucent)
            self.metarQueryOutput.append(l)
            y -= self.line_height

        y -= self.line_height
        cap = xp.createWidget(x, y, x + 300, y - self.line_height, 1, "XP12 Real Weather:", 0, 
                              self.metar_window_widget, xp.WidgetClass_Caption)
        xp.setWidgetProperty(cap, xp.Property_CaptionLit, 1)

        y -= self.line_height
        self.RWQueryOutput = []
        for i in range(2):
            l = xp.createWidget(x, y, x + self.metar_widget_width, y - self.line_height, 0, "", 0, 
                                self.metar_window_widget, xp.WidgetClass_TextField)
            xp.setWidgetProperty(l, xp.Property_TextFieldType, xp.TextTranslucent)
            self.RWQueryOutput.append(l)
            y -= self.line_height

        # Register our query widget handler
        self.metarQueryInputHandlerCB = self.metarQueryInputHandler
        xp.addWidgetCallback(self.metarQueryInput, self.metarQueryInputHandlerCB)

        # Register our widget handler
        self.metarWindowHandlerCB = self.metarWindowHandler
        xp.addWidgetCallback(self.metar_window_widget, self.metarWindowHandlerCB)

        xp.setKeyboardFocus(self.metarQueryInput)

    def create_config_window(self, x: int = 200, y: int = 640):

        x2 = x + self.config_width
        y2 = y - self.config_height

        # Create the Main Widget window
        self.config_window_widget = xp.createWidget(x, y, x2, y2, 1, self.config_title, 1, 0, xp.WidgetClass_MainWindow)
        window = self.config_window_widget

        # Add Close Box decorations to Config Widget
        xp.setWidgetProperty(window, xp.Property_MainWindowHasCloseBoxes, 1)

        ## MAIN CONFIGURATION ##

        # Config Sub Window, style
        r = x + self.window_margin
        l = x2 - self.window_margin
        t = y - self.line_height - self.window_margin
        b = y2 + 80

        subw = xp.createWidget(r, t, l, b, 1, "", 0, window, xp.WidgetClass_SubWindow)
        xp.setWidgetProperty(subw, xp.Property_SubWindowType, xp.SubWindowStyle_SubWindow)

        m = int(self.window_margin / 2)
        top = t - m
        x = r + m
        y = top
        cw = int((l - r - m) / 2)  # column width
        xc = x + cw - self.line_height  # radio button column

        # Main enable
        xp.createWidget(x, y, x + 20, y - self.line_height, 1, 'Enable Plugin:', 0, window, xp.WidgetClass_Caption)
        self.enable_check = xp.createWidget(
            xc, y, xc + self.line_height, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button
        )
        xp.setWidgetProperty(self.enable_check, xp.Property_ButtonState, xp.RadioButton)
        xp.setWidgetProperty(self.enable_check, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
        xp.setWidgetProperty(self.enable_check, xp.Property_ButtonState, self.conf.enabled)
        y -= self.line_height * 2

        # METAR decoding
        xp.createWidget(x, y, x + 20, y - self.line_height, 1, 'METAR Decoding:', 0, window, xp.WidgetClass_Caption)
        self.decode_check = xp.createWidget(
            xc, y, xc + self.line_height, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button
        )
        xp.setWidgetProperty(self.decode_check, xp.Property_ButtonState, xp.RadioButton)
        xp.setWidgetProperty(self.decode_check, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
        xp.setWidgetProperty(self.decode_check, xp.Property_ButtonState, self.conf.metar_decode)
        y -= self.line_height

        # Metar source radios
        xp.createWidget(x, y, x + 100, y - self.line_height, 1, 'METAR SOURCE:', 0, window, xp.WidgetClass_Caption)

        n = self.font_width * (len("NOAA ") + 1)
        v = self.font_width * (len("VATSIM ") + 1)
        x1 = xc - n * 2 - v - self.line_height * 4

        xp.createWidget(x1, y, x1 + n, y - self.line_height, 1, 'NOAA', 0, window, xp.WidgetClass_Caption)
        x1 += n
        noaa_check = xp.createWidget(
            x1, y, x1 + self.line_height, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button
        )
        x1 += self.line_height * 2
        xp.createWidget(x1, y, x1 + n, y - self.line_height, 1, 'IVAO', 0, window, xp.WidgetClass_Caption)
        x1 += n
        ivao_check = xp.createWidget(
            x1, y, x1 + self.line_height, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button
        )
        x1 += self.line_height * 2
        xp.createWidget(x1, y, x1 + 40, y - self.line_height, 1, 'VATSIM', 0, window, xp.WidgetClass_Caption)
        x1 += v
        vatsim_check = xp.createWidget(
            x1, y, x1 + self.line_height, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button
        )

        self.metar_source_check = {
            noaa_check: 'NOAA',
            ivao_check: 'IVAO',
            vatsim_check: 'VATSIM'
        }

        for k, v in self.metar_source_check.items():
            xp.setWidgetProperty(k, xp.Property_ButtonState, xp.RadioButton)
            xp.setWidgetProperty(k, xp.Property_ButtonBehavior, xp.ButtonBehaviorRadioButton)
            xp.setWidgetProperty(k, xp.Property_ButtonState, int(self.conf.metar_source == v))
        y -= self.line_height

        # Ignore automatically generated METAR sources
        xp.createWidget(x, y, x + 100, y - self.line_height, 1, 'Ignore Metars with AUTO:', 0, window, xp.WidgetClass_Caption)
        self.auto_check = xp.createWidget(
            xc, y, xc + self.line_height, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button
        )
        xp.setWidgetProperty(self.auto_check, xp.Property_ButtonState, xp.RadioButton)
        xp.setWidgetProperty(self.auto_check, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
        xp.setWidgetProperty(self.auto_check, xp.Property_ButtonState, self.conf.metar_ignore_auto)
        y -= self.line_height * 2

        # List of METAR stations that will not be considered 
        xp.createWidget(x, y, x + 100, y - self.line_height, 1, 'METAR Stations to be ignored:', 0, window, xp.WidgetClass_Caption)
        y -= self.line_height
        self.ignore_list_input = xp.createWidget(
            x, y, xc, y - self.line_height, 1, ' '.join(self.conf.ignore_metar_stations), 0, window,
            xp.WidgetClass_TextField
        )
        y-= self.line_height * 2

        # Create METAR.rwx file
        xp.createWidget(x, y, x + 200, y - self.line_height, 1, 'Create RWX file (READ the README file!):', 0, window, xp.WidgetClass_Caption)
        self.rwxCheck = xp.createWidget(
            xc, y, xc + self.line_height, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button
        )
        xp.setWidgetProperty(self.rwxCheck, xp.Property_ButtonState, xp.RadioButton)
        xp.setWidgetProperty(self.rwxCheck, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
        xp.setWidgetProperty(self.rwxCheck, xp.Property_ButtonState, self.conf.update_rwx_file)
        y -= self.line_height

        # Use XP12 Real weather files to populate METAR.rwx file
        xp.createWidget(x, y, x + 100, y - self.line_height, 1, 'Use Real Weather for RWX file:', 0, window, xp.WidgetClass_Caption)
        self.xp12MetarCheck = xp.createWidget(
            xc, y, xc + self.line_height, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button
        )
        xp.setWidgetProperty(self.xp12MetarCheck, xp.Property_ButtonState, xp.RadioButton)
        xp.setWidgetProperty(self.xp12MetarCheck, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
        xp.setWidgetProperty(self.xp12MetarCheck, xp.Property_ButtonState, self.conf.metar_use_xp12)
        y -= self.line_height * 2

        # WAFS download enable
        # xp.createWidget(x, y, x + 100, y - self.line_height, 1, 'WAFS download', 0, window, xp.WidgetClass_Caption)
        # self.WAFSCheck = xp.createWidget(x + 120, y, x + 140, y - self.line_height, 1, '', 0, window, xpWidgetClass_Button)
        # XPSetWidgetProperty(self.WAFSCheck, xp.Property_ButtonState, xp.RadioButton)
        # XPSetWidgetProperty(self.WAFSCheck, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
        # XPSetWidgetProperty(self.WAFSCheck, xp.Property_ButtonState, self.conf.download_WAFS)
        # y -= self.line_height * 2

        # # Accumulated snow
        # xp.createWidget(x, y, x + 100, y - self.line_height, 1, 'Snow Depth', 0, window, xp.WidgetClass_Caption)
        # self.snowCheck = xp.createWidget(x + 120, y, x + 140, y - self.line_height, 1, '', 0, window, xpWidgetClass_Button)
        # XPSetWidgetProperty(self.snowCheck, xp.Property_ButtonState, xp.RadioButton)
        # XPSetWidgetProperty(self.snowCheck, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
        # XPSetWidgetProperty(self.snowCheck, xp.Property_ButtonState, self.conf.set_snow)
        # y -= self.line_height * 2

        if not self.conf.real_weather_enabled:
            # Winds enable
            xp.createWidget(x + 5, y, x + 20, y - self.line_height, 1, 'Wind levels', 0, window, xp.WidgetClass_Caption)
            self.windsCheck = xp.createWidget(
                x + 110, y, x + 120, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button
            )
            xp.setWidgetProperty(self.windsCheck, xp.Property_ButtonState, xp.RadioButton)
            xp.setWidgetProperty(self.windsCheck, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
            xp.setWidgetProperty(self.windsCheck, xp.Property_ButtonState, self.conf.set_wind)
            y -= self.line_height

            # Clouds enable
            xp.createWidget(x + 5, y, x + 20, y - self.line_height, 1, 'Cloud levels', 0, window, xp.WidgetClass_Caption)
            self.cloudsCheck = xp.createWidget(
                x + 110, y, x + 120, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button
            )
            xp.setWidgetProperty(self.cloudsCheck, xp.Property_ButtonState, xp.RadioButton)
            xp.setWidgetProperty(self.cloudsCheck, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
            xp.setWidgetProperty(self.cloudsCheck, xp.Property_ButtonState, self.conf.set_clouds)
            y -= self.line_height

            # Optimised clouds layers update for liners
            xp.createWidget(x + 5, y, x + 20, y - self.line_height, 1, 'Opt. redraw', 0, window, xp.WidgetClass_Caption)
            self.optUpdCheck = xp.createWidget(
                x + 110, y, x + 120, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button
            )
            xp.setWidgetProperty(self.optUpdCheck, xp.Property_ButtonState, xp.RadioButton)
            xp.setWidgetProperty(self.optUpdCheck, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
            xp.setWidgetProperty(self.optUpdCheck, xp.Property_ButtonState, self.conf.opt_clouds_update)
            y -= self.line_height

            # Temperature enable
            xp.createWidget(x + 5, y, x + 20, y - self.line_height, 1, 'Temperature', 0, window, xp.WidgetClass_Caption)
            self.tempCheck = xp.createWidget(x + 110, y, x + 120, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button)
            xp.setWidgetProperty(self.tempCheck, xp.Property_ButtonState, xp.RadioButton)
            xp.setWidgetProperty(self.tempCheck, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
            xp.setWidgetProperty(self.tempCheck, xp.Property_ButtonState, self.conf.set_temp)
            y -= self.line_height

            # Pressure enable
            xp.createWidget(x + 5, y, x + 20, y - self.line_height, 1, 'Pressure', 0, window, xp.WidgetClass_Caption)
            self.pressureCheck = xp.createWidget(x + 110, y, x + 120, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button)
            xp.setWidgetProperty(self.pressureCheck, xp.Property_ButtonState, xp.RadioButton)
            xp.setWidgetProperty(self.pressureCheck, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
            xp.setWidgetProperty(self.pressureCheck, xp.Property_ButtonState, self.conf.set_pressure)
            y -= self.line_height

            # Turbulence enable
            xp.createWidget(x + 5, y, x + 20, y - self.line_height, 1, 'Turbulence', 0, window, xp.WidgetClass_Caption)
            self.turbCheck = xp.createWidget(x + 110, y, x + 120, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button)
            xp.setWidgetProperty(self.turbCheck, xp.Property_ButtonState, xp.RadioButton)
            xp.setWidgetProperty(self.turbCheck, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
            xp.setWidgetProperty(self.turbCheck, xp.Property_ButtonState, self.conf.set_turb)
            y -= self.line_height

            self.turbulenceCaption = xp.createWidget(
                x + 5, y, x + 80, y - self.line_height, 
                1, f"Turbulence prob.  {self.conf.turbulence_probability * 100}%", 0, window, 
                xp.WidgetClass_Caption
            )
            self.turbulenceSlider = xp.createWidget(
                x + 10, y - self.line_height, x + 160, y - 40, 1, '', 0, window, xp.WidgetClass_ScrollBar
            )
            xp.setWidgetProperty(self.turbulenceSlider, xp.Property_ScrollBarType, xp.ScrollBarTypeSlider)
            xp.setWidgetProperty(self.turbulenceSlider, xp.Property_ScrollBarMin, 10)
            xp.setWidgetProperty(self.turbulenceSlider, xp.Property_ScrollBarMax, 1000)
            xp.setWidgetProperty(self.turbulenceSlider, xp.Property_ScrollBarPageAmount, 1)
            xp.setWidgetProperty(
                self.turbulenceSlider, 
                xp.Property_ScrollBarSliderPosition, int(self.conf.turbulence_probability * 1000)
            )
            y -= self.line_height * 2

            # Tropo enable
            xp.createWidget(x + 5, y, x + 20, y - self.line_height, 1, 'Tropo Temp', 0, window, xp.WidgetClass_Caption)
            self.tropoCheck = xp.createWidget(x + 110, y, x + 120, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button)
            xp.setWidgetProperty(self.tropoCheck, xp.Property_ButtonState, xp.RadioButton)
            xp.setWidgetProperty(self.tropoCheck, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
            xp.setWidgetProperty(self.tropoCheck, xp.Property_ButtonState, self.conf.set_tropo)
            y -= self.line_height

            # Thermals enable
            xp.createWidget(x + 5, y, x + 20, y - self.line_height, 1, 'Thermals', 0, window, xp.WidgetClass_Caption)
            self.thermalsCheck = xp.createWidget(x + 110, y, x + 120, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button)
            xp.setWidgetProperty(self.thermalsCheck, xp.Property_ButtonState, xp.RadioButton)
            xp.setWidgetProperty(self.thermalsCheck, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
            xp.setWidgetProperty(self.thermalsCheck, xp.Property_ButtonState, self.conf.set_thermals)
            y -= self.line_height

            # Surface Wind Layer enable
            xp.createWidget(x + 5, y, x + 20, y - self.line_height, 1, 'Surface Wind', 0, window, xp.WidgetClass_Caption)
            self.surfaceCheck = xp.createWidget(x + 110, y, x + 120, y - self.line_height, 1, '', 0, window, xp.WidgetClass_Button)
            xp.setWidgetProperty(self.surfaceCheck, xp.Property_ButtonState, xp.RadioButton)
            xp.setWidgetProperty(self.surfaceCheck, xp.Property_ButtonBehavior, xp.ButtonBehaviorCheckBox)
            xp.setWidgetProperty(self.surfaceCheck, xp.Property_ButtonState, self.conf.set_surface_layer)
            y -= self.line_height * 2

            # Performance Tweaks
            xp.createWidget(x, y, x + 80, y - self.line_height, 1, 'Performance Tweaks', 0, window, xp.WidgetClass_Caption)
            xp.createWidget(x + 5, y - self.line_height, x + 80, y - 40, 1, 'Max Visibility (sm)', 0, window, xp.WidgetClass_Caption)
            self.maxVisInput = xp.createWidget(x + 119, y - self.line_height, x + 160, y - 40, 1,
                                               c.convertForInput(self.conf.max_visibility, 'm2sm'), 0, window,
                                               xp.WidgetClass_TextField)
            y -= self.line_height * 2
            xp.createWidget(x + 5, y, x + 80, y - self.line_height, 1, 'Max cloud height (ft)', 0, window, xp.WidgetClass_Caption)
            self.maxCloudHeightInput = xp.createWidget(x + 119, y, x + 160, y - self.line_height, 1,
                                                       c.convertForInput(self.conf.max_cloud_height, 'm2ft'), 0, window,
                                                       xp.WidgetClass_TextField)

        # elements to add at the bottom of the subwindow
        y1 = b + self.line_height * 4

        # Save
        self.save_button = xp.createWidget(
            x, y1, x + 130, y1 - self.line_height, 1, "Apply & Save", 0, window, xp.WidgetClass_Button
        )
        xp.setWidgetProperty(self.save_button, xp.Property_ButtonType, xp.PushButton)
        self.save_caption = xp.createWidget(
            x + 150, y1, x + 420, y1 - self.line_height, 1, "", 0, window, xp.WidgetClass_Caption
        )
        y1 -= self.line_height * 2

        # DumpLog Button
        self.dumplog_button = xp.createWidget(
            x, y1, x + 130, y1 - self.line_height, 1, "DumpLog", 0, window, xp.WidgetClass_Button
        )
        xp.setWidgetProperty(self.dumplog_button, xp.Property_ButtonType, xp.PushButton)
        self.dump_caption = xp.createWidget(
            x + 150, y1, x + 420, y1 - self.line_height, 1, '', 0, window, xp.WidgetClass_Caption
        )

        # ABOUT Subwindow
        t = b - 2
        b = y2 + self.window_margin
        subw = xp.createWidget(r, t, l, b, 1, "", 0, window, xp.WidgetClass_SubWindow)

        # Set the style to sub window

        y = t - self.window_margin
        sysinfo = [
            f"X-Plane 12 NOAA Weather: {self.conf.__VERSION__}",
            '(c) antonio golfari 2023',
        ]
        for label in sysinfo:
            xp.createWidget(x, y, x + 120, y - self.line_height, 1, label, 0, window, xp.WidgetClass_Caption)
            y -= self.line_height

        # Visit site Button
        button_width = 120
        x1 = l - self.window_margin - button_width * 2 - 20
        y1 = b + self.window_margin + self.line_height
        self.about_button = xp.createWidget(
            x1, y1, x1 + button_width, y1 - self.line_height, 1, "Official site", 0, window, xp.WidgetClass_Button
        )

        x1 += button_width + 20
        self.forum_button = xp.createWidget(
            x1, y1, x1 + button_width, y1 - self.line_height, 1, "Support", 0, window, xp.WidgetClass_Button
        )

        # Register our widget handler
        self.configWindowHandlerCB = self.configWindowHandler
        xp.addWidgetCallback(window, self.configWindowHandlerCB)

        self.config_window = True

    def infoWindowHandler(self, inMessage, inWidget, inParam1, inParam2):
        if inMessage == xp.Message_CloseButtonPushed:
            if self.info_window:
                xp.hideWidget(self.info_window_widget)
                return 1
        return 0

    def configWindowHandler(self, inMessage, inWidget, inParam1, inParam2):
        # About window events
        if inMessage == xp.Message_CloseButtonPushed:
            if self.config_window:
                xp.destroyWidget(self.config_window_widget, 1)
                self.config_window = False
            return 1

        if inMessage == xp.Msg_ButtonStateChanged and inParam1 in self.metar_source_check:
            if inParam2:
                for i in self.metar_source_check:
                    if i != inParam1:
                        xp.setWidgetProperty(i, xp.Property_ButtonState, 0)
            else:
                xp.setWidgetProperty(inParam1, xp.Property_ButtonState, 1)
            return 1

        if inMessage == xp.Msg_ButtonStateChanged and inParam1 == self.decode_check:
            self.conf.metar_decode = xp.getWidgetProperty(self.decode_check, xp.Property_ButtonState)
            return 1

        if inMessage == xp.Msg_ScrollBarSliderPositionChanged and inParam1 == self.turbulenceSlider:
            val = xp.getWidgetProperty(self.turbulenceSlider, xp.Property_ScrollBarSliderPosition, None)
            xp.setWidgetDescriptor(self.turbulenceCaption, f"Turbulence probability {round(val/10)}%")
            return 1

        # Handle any button pushes
        if inMessage == xp.Msg_PushButtonPressed:

            if inParam1 == self.about_button:
                from webbrowser import open_new
                open_new('https://github.com/biuti/XplaneNoaaWeather')
                return 1
            if inParam1 == self.forum_button:
                from webbrowser import open_new
                open_new(
                    'http://forums.x-plane.org/index.php?/forums/topic/72313-noaa-weather-plugin/&do=getNewComment')
                return 1
            if inParam1 == self.save_button:
                # Save configuration
                self.conf.enabled = xp.getWidgetProperty(self.enable_check, xp.Property_ButtonState, None)
                self.conf.metar_decode = xp.getWidgetProperty(self.decode_check, xp.Property_ButtonState)
                if self.conf.real_weather_enabled:
                    # nothing to do now
                    # self.conf.download_WAFS = xp.getWidgetProperty(self.WAFSCheck, xp.Property_ButtonState, None)
                    pass
                else:
                    self.conf.set_wind = xp.getWidgetProperty(self.windsCheck, xp.Property_ButtonState, None)
                    self.conf.set_clouds = xp.getWidgetProperty(self.cloudsCheck, xp.Property_ButtonState, None)
                    self.conf.opt_clouds_update = xp.getWidgetProperty(self.optUpdCheck, xp.Property_ButtonState, None)
                    self.conf.set_temp = xp.getWidgetProperty(self.tempCheck, xp.Property_ButtonState, None)
                    self.conf.set_pressure = xp.getWidgetProperty(self.pressureCheck, xp.Property_ButtonState, None)
                    self.conf.set_tropo = xp.getWidgetProperty(self.tropoCheck, xp.Property_ButtonState, None)
                    self.conf.set_thermals = xp.getWidgetProperty(self.thermalsCheck, xp.Property_ButtonState, None)
                    self.conf.set_surface_layer = xp.getWidgetProperty(self.surfaceCheck, xp.Property_ButtonState, None)
                    self.conf.turbulence_probability = xp.getWidgetProperty(self.turbulenceSlider,
                                                                           xp.Property_ScrollBarSliderPosition,
                                                                           None) / 1000.0
                    # Zero turbulence data if disabled
                    self.conf.set_turb = xp.getWidgetProperty(self.turbCheck, xp.Property_ButtonState, None)
                    if not self.conf.set_turb:
                        for i in range(3):
                            self.data.winds[i]['turb'].value = 0

                    buff = xp.getWidgetDescriptor(self.maxCloudHeightInput)
                    self.conf.max_cloud_height = c.convertFromInput(buff, 'f2m', min=c.f2m(2000))

                    buff = xp.getWidgetDescriptor(self.maxVisInput)
                    self.conf.max_visibility = c.convertFromInput(buff, 'sm2m')

                # self.conf.set_snow = XPGetWidgetProperty(self.snowCheck, xp.Property_ButtonState, None)

                # Metar station ignore
                buff = xp.getWidgetDescriptor(self.ignore_list_input)
                ignore_stations = []
                for icao in buff.split(' '):
                    if len(icao) == 4:
                        ignore_stations.append(icao.upper())

                self.conf.metar_ignore_auto = xp.getWidgetProperty(self.auto_check, xp.Property_ButtonState, None)
                self.conf.ignore_metar_stations = ignore_stations

                # Check metar source
                prev_metar_source = self.conf.metar_source
                for check in self.metar_source_check:
                    if xp.getWidgetProperty(check, xp.Property_ButtonState, None):
                        self.conf.metar_source = self.metar_source_check[check]

                # Check METAR.rwx file
                prev_rwx = self.conf.update_rwx_file
                self.conf.update_rwx_file = xp.getWidgetProperty(self.rwxCheck, xp.Property_ButtonState, None)

                # Check METAR.rwx source
                prev_file_source = self.conf.metar_use_xp12
                self.conf.metar_use_xp12 = xp.getWidgetProperty(self.xp12MetarCheck, xp.Property_ButtonState, None)

                # Save config and tell server to reload it
                self.conf.pluginSave()
                xp.log(f"Config saved. Weather client reloading ...")
                self.weather.weatherClientSend('!reload')

                # If metar source has changed tell server to reinit metar database
                if self.conf.metar_source != prev_metar_source:
                    self.weather.weatherClientSend('!resetMetar')

                # If metar source for METAR.rwx file has changed tell server to reinit rwmetar database
                if self.conf.update_rwx_file != prev_rwx or self.conf.metar_use_xp12 != prev_file_source:
                    self.weather.weatherClientSend('!resetRWMetar')

                self.weather.startWeatherClient()
                self.configWindowUpdate()

                # Reset things
                self.weather.newData = True
                self.newAptLoaded = True

                return 1

            if inParam1 == self.dumplog_button:
                dumpfile = self.weather.dumpLog()
                xp.setWidgetDescriptor(self.dump_caption, f"created {dumpfile.name} in cache folder")
                return 1
        return 0

    def configWindowUpdate(self):

        xp.setWidgetProperty(self.enable_check, xp.Property_ButtonState, self.conf.enabled)
        xp.setWidgetProperty(self.decode_check, xp.Property_ButtonState, self.conf.metar_decode)
        xp.setWidgetDescriptor(self.ignore_list_input, ' '.join(self.conf.ignore_metar_stations))
        xp.setWidgetProperty(self.auto_check, xp.Property_ButtonState, self.conf.metar_ignore_auto)
        xp.setWidgetProperty(self.rwxCheck, xp.Property_ButtonState, self.conf.update_rwx_file)
        xp.setWidgetProperty(self.xp12MetarCheck, xp.Property_ButtonState, self.conf.metar_use_xp12)

        if self.conf.real_weather_enabled:
            # nothing to do now
            # xp.setWidgetProperty(self.WAFSCheck, xp.Property_ButtonState, self.conf.download_WAFS)
            pass

        else:
            xp.setWidgetProperty(self.windsCheck, xp.Property_ButtonState, self.conf.set_wind)
            xp.setWidgetProperty(self.cloudsCheck, xp.Property_ButtonState, self.conf.set_clouds)
            xp.setWidgetProperty(self.optUpdCheck, xp.Property_ButtonState, self.conf.opt_clouds_update)
            xp.setWidgetProperty(self.tempCheck, xp.Property_ButtonState, self.conf.set_temp)
            xp.setWidgetProperty(self.turbCheck, xp.Property_ButtonState, self.conf.set_turb)
            xp.setWidgetProperty(self.tropoCheck, xp.Property_ButtonState, self.conf.set_tropo)
            xp.setWidgetProperty(self.thermalsCheck, xp.Property_ButtonState, self.conf.set_thermals)
            xp.setWidgetProperty(self.surfaceCheck, xp.Property_ButtonState, self.conf.set_surface_layer)
            xp.setWidgetDescriptor(self.maxVisInput, c.convertForInput(self.conf.max_visibility, 'm2sm'))
            xp.setWidgetDescriptor(self.maxCloudHeightInput, c.convertForInput(self.conf.max_cloud_height, 'm2ft'))

        self.updateStatus()

    def updateStatus(self):
        """Updates status window"""

        sysinfo = self.weather.weatherInfo(self.info_line_chars)

        for line in self.info_captions:
            label = sysinfo.pop(0) if len(sysinfo) else '--'
            xp.setWidgetDescriptor(line, label)

        text = ""
        if self.conf.settingsfile.is_file() and self.config_window:
            d = int(time.time() - self.conf.settingsfile.stat().st_mtime)
            if d < 15:
                text = f"Reloading ({15 - d} sec.) ..."
            xp.setWidgetDescriptor(self.save_caption, text)

    def metarQueryInputHandler(self, inMessage, inWidget, inParam1, inParam2):
        """Override Texfield keyboard input to be more friendly"""
        if inMessage == xp.Msg_KeyPress:

            key, flags, vkey = inParam1

            if flags == 8:
                cursor = xp.getWidgetProperty(self.metarQueryInput, xp.Property_EditFieldSelStart, None)
                text = xp.getWidgetDescriptor(self.metarQueryInput).strip()
                if key in (8, 127):
                    # pass
                    xp.setWidgetDescriptor(self.metarQueryInput, text[:-1])
                    cursor -= 1
                elif key == 13:
                    # Enter
                    self.metarQuery()
                elif key == 27:
                    # ESC
                    xp.loseKeyboardFocus(self.metarQueryInput)
                elif 65 <= key <= 90 or 97 <= key <= 122 and len(text) < 4:
                    text += chr(key).upper()
                    xp.setWidgetDescriptor(self.metarQueryInput, text)
                    cursor += 1

                ltext = len(text)
                if cursor < 0: cursor = 0
                if cursor > ltext: cursor = ltext

                xp.setWidgetProperty(self.metarQueryInput, xp.Property_EditFieldSelStart, cursor)
                xp.setWidgetProperty(self.metarQueryInput, xp.Property_EditFieldSelEnd, cursor)

                return 1
        elif inMessage in (xp.Msg_MouseDrag, xp.Msg_MouseDown, xp.Msg_MouseUp):
            xp.setKeyboardFocus(self.metarQueryInput)
            return 1
        return 0

    def metarWindowHandler(self, inMessage, inWidget, inParam1, inParam2):
        if inMessage == xp.Message_CloseButtonPushed:
            if self.metar_window:
                xp.hideWidget(self.metar_window_widget)
                return 1
        if inMessage == xp.Msg_PushButtonPressed:
            if inParam1 == self.metarQueryButton:
                self.metarQuery()
                return 1
        return 0

    def clean_metar_output(self):
        for out in (self.metarQueryOutput, self.RWQueryOutput):
            for line in out:
                xp.setWidgetDescriptor(line, '')
                xp.hideWidget(line)

    @staticmethod
    def file_metar_line(el, text: str):
        xp.showWidget(el)
        xp.setWidgetDescriptor(el, text)

    def metarQuery(self):
        query = xp.getWidgetDescriptor(self.metarQueryInput).strip().upper()
        self.clean_metar_output()
        if len(query) == 4:
            self.weather.weatherClientSend('?' + query)
            xp.setWidgetDescriptor(self.metarQueryOutput[0], 'Querying, please wait.')
        else:
            xp.setWidgetDescriptor(self.metarQueryOutput[0], 'Please insert a valid ICAO code.')

    def metarQueryCallback(self, msg):
        """Callback for metar queries"""

        if self.metar_window:
            # Filter metar text
            metar = util.format_text(''.join(filter(lambda x: x in self.conf.printableChars, msg['metar']['metar'])), 
                                          self.metar_line_chars)
            rwmetar = util.format_text(''.join(filter(lambda x: x in self.conf.printableChars, msg['rwmetar']['metar'])), 
                                            self.metar_line_chars)
            # adding source and RW METARs
            for i, line in enumerate(self.metarQueryOutput):
                if len(metar) > i:
                    self.file_metar_line(self.metarQueryOutput[i], f"{metar[i]}")

            for i, line in enumerate(self.RWQueryOutput):
                if len(rwmetar) > i:
                    self.file_metar_line(self.RWQueryOutput[i], f"{rwmetar[i]}")

    def metarQueryWindowToggle(self):
        """Metar window toggle command"""
        if self.metar_window:
            if xp.isWidgetVisible(self.metar_window_widget):
                xp.hideWidget(self.metar_window_widget)
            else:
                xp.showWidget(self.metar_window_widget)
        else:
            self.create_metar_window()

    def shutdown_widget(self):

        # Destroy windows
        if self.info_window:
            xp.destroyWidget(self.info_window_widget, 1)
        if self.metar_window:
            xp.destroyWidget(self.metar_window_widget, 1)
        if self.config_window:
            xp.destroyWidget(self.config_window_widget, 1)

        self.metarWindowCMD.destroy()

        # kill Menu
        xp.destroyMenu(self.main_menu)
