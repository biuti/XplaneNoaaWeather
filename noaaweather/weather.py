"""
X-plane NOAA GFS weather plugin.

Development version for X-Plane 12
---
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2026 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import os
import pickle
import socket
import threading
import subprocess

from datetime import datetime
from pathlib import Path
from typing import Optional

from . import xp, c, dref, util, DataRef


class Weather:
    """Sets x-plane weather from GFS parsed data"""

    alt = 0.0
    ref_winds = {}
    lat, lon, last_lat, last_lon = 99, 99, False, False

    # snow default values
    snow_default_values = {
        'frozen_water': 0,
        'tarmac_snow_width': 0.25,
        'tarmac_snow_scale': 500,
        'tarmac_snow_noise': 0.04
    }

    def __init__(self, conf) -> None:

        self.conf = conf
        self.data = dref.Dref()
        self.lastMetarStation = False

        # runway friction values
        self.xp_runway_friction = None
        self.metar_friction = None
        self.adjusted_friction = None

        # Data
        self.weatherData = False
        self.weatherClientThread = False

        self.windAlts = -1
        self.nearest_snow = False

        # cache to avoid recomputation
        self._last_factor_input = None
        self._cached_factor = 0.0
        self._last_logged_val = None

        # Response queue for user queries
        self.queryResponses = []

        # Create client socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.die = threading.Event()
        self.lock = threading.Lock()

        self.newData = False

        self.startWeatherServer()

    def startWeatherClient(self ) -> None:
        if not self.weatherClientThread:
            self.weatherClientThread = threading.Thread(target=self.weatherClient)
            self.weatherClientThread.start()

    def weatherClient(self) -> None:
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

    def weatherClientSend(self, msg: str) -> None:
        if self.weatherClientThread:
            self.sock.sendto(msg.encode('utf-8'), ('127.0.0.1', self.conf.server_port))

    def startWeatherServer(self) -> None:
        DETACHED_PROCESS = 0x00000008
        args = [xp.pythonExecutable, Path(self.conf.respath, 'weatherServer.py'), self.conf.syspath]
        kwargs = {'close_fds': True}
        try:
            if self.conf.spinfo:
                kwargs.update({'startupinfo': self.conf.spinfo, 'creationflags': DETACHED_PROCESS})
            xp.log(f"Starting Weather Server using: {args} {kwargs}")
            subprocess.Popen(args, **kwargs)
        except Exception as e:
            print(f"Exception while executing subprocess: {e}")

    def shutdown(self) -> None:
        # Shutdown client and server
        self.weatherClientSend('!shutdown')
        self.weatherClientThread = False

    def get_XP12_METAR(self, icao: str) -> str:
        return xp.getMETARForAirport(icao)

    def setSnow(self, elapsed: float) -> None:
        """ Set snow cover
            X-Plane version < 12.4:
            Dref value goes from 1.25 to 0.01
            no snow:    1.25
            light:      0.31
            medium:     0.21
            heavy:      0.07
            X-Plane version >= 12.4:
            Dref value goes from 0 to 1
            no snow:    0
            light:      0.2  no snow on tarmac, some on grass
            medium:     0.5  no snow on tarmac, grass almost covered
            heavy:      0.9  tarmac heavily contaminated

            GFS SNOD is in meters
            SNOD    Dref conversion:
            0       1.25
            0.1     ~0.3
            0.25    ~0.2
            0.5     ~0.1
            1+      ~0.05
        """
        if not (self.weatherData['gfs'].get('surface') and self.data.check_snow_dref()):
            # we don't have snow data
            return

        if self.data.snow_override.value == 0:
            # enable snow coverage control (on 12.4-b1 does not seem to have any effect)
            self.data.snow_override.value = 1
        data = self.weatherData['gfs']['surface']
        snow = data['snow']
        lat = self.data.latdr.value if isinstance(self.data.latdr.value, (int, float)) else 0.0
        lon = self.data.londr.value if isinstance(self.data.londr.value, (int, float)) else 0.0
        temp = c.kel2cel(data['temp'])
        transitions_speed = 0.25 if self.data.on_ground else 0.01

        # over water, or where is not received, GFS data have a value of 9.999e+20
        # if there is already a snow value, it will keep that one in a radius of 300nm, 
        # until a new valid value is acquired, otherwise will stop injecting a value in the dref
        if c.is_exponential(snow):
            # we are probably over water or very close to water
            snow = 0
            if data.get('prediction'):
                # a suitable value is found nearby or along the track
                if not self.nearest_snow:
                    # save new nearest snow value
                    self.nearest_snow = data['prediction']
                else:
                    # check if we have a better prediction
                    nlat, nlon, nsnow = self.nearest_snow.values()
                    plat, plon, _ = data['prediction'].values()
                    if (nlat, nlon) != (plat, plon):
                        ndist = c.greatCircleDistance((lat, lon), (nlat, nlon))
                        pdist = c.greatCircleDistance((lat, lon), (plat, plon))
                        if pdist < ndist:
                            self.nearest_snow = data['prediction']

            if self.nearest_snow:
                # we can use latest recorded snow value if near enough
                nlat, nlon, nsnow = self.nearest_snow.values()
                dist = c.m2nm(c.greatCircleDistance((lat, lon), (nlat, nlon)))  # in nm
                if dist < 70:
                    snow = nsnow
                else:
                    # delete old nearest value
                    self.nearest_snow = False
        elif snow == 0:
            self.nearest_snow = False
        elif snow > 0:
            self.nearest_snow = {
                'lat': lat,
                'lon': lon,
                'depth': snow
            }

        # ------------------------------------------------------------------
        # No snow → defaults
        # ------------------------------------------------------------------
        if snow <= 0.0:
            val = 0.0
            frozen_water, noise, scale, width = self.snow_default_values.values()
            ice = puddles = 0.0

        else:
            # --------------------------------------------------------------
            # Factor caching (lat/temp rarely change)
            # --------------------------------------------------------------
            factor_key = (round(lat, 2), round(temp, 1))
            if factor_key != self._last_factor_input:
                self._cached_factor = c.computeWaterHostilityFactor(lat, temp)
                self._last_factor_input = factor_key

            factor = self._cached_factor

            # --------------------------------------------------------------
            # Compute snow coverage
            # --------------------------------------------------------------
            val = c.computeSnowVal(snow, lat, temp)

            # --------------------------------------------------------------
            # Compute surface effects
            # --------------------------------------------------------------
            (
                frozen_water,
                noise,
                scale,
                width,
                ice,
                puddles
            ) = c.computeSurfaceEffects(val, factor, temp)

        # ------------------------------------------------------------------
        # Snow cover transition (only if changed meaningfully)
        # ------------------------------------------------------------------
        rw_val = self.data.snow_cover.value
        if not c.isclose(rw_val, val, tol=0.005):
            try:
                c.snowDatarefTransition(
                    self.data.snow_cover,
                    val,
                    elapsed=elapsed,
                    speed=transitions_speed
                )

                c.datarefTransition(
                    self.data.frozen_water,
                    frozen_water,
                    elapsed,
                    transitions_speed
                )

                self.setDrefIfDiff(self.data.tarmac_snow_noise, noise)
                self.setDrefIfDiff(self.data.tarmac_snow_scale, scale)
                self.setDrefIfDiff(self.data.tarmac_snow_width, width)

                # log only when change is visible
                if self._last_logged_val is None or abs(val - self._last_logged_val) > 0.01:
                    xp.log(
                        f" Snow cover: {val:.3f} | depth {snow:.2f} m | lat {lat:.1f} | temp {temp:.1f}C"
                    )
                    self._last_logged_val = val

            except SystemError as e:
                xp.log(f"ERROR injecting snow_cover: {e}")

        # ------------------------------------------------------------------
        # Inject secondary drefs
        # ------------------------------------------------------------------
        self.setDrefIfDiff(self.data.iced_tarmac, ice)
        self.setDrefIfDiff(self.data.puddles, puddles)

    def setRunwayFriction(self, elapsed: float) -> None:
        """
            As of XP version 12.4, runway friction still needs to be adjusted as it 
            uses high values of friction (icy tarmac) on any airport, so even international airports are affected.
            This function adjusts the friction value considering that runways receive anti-ice treatment.
            In the future, I will take into consideration METAR values and define a friction value internally
            By now:
            - 6 < friction <= 8  : medium snow -> set to puddly (6)
            - 9 < friction <= 11 : icy     -> set to snowy (7)
            - 12 and 13          : snowy/icy -> set to snowy (8)
            - friction > 13      : heavy snow / ice -> set to heavy snow (9)
        """
        # # get current friction values
        # # Dry = 0, wet(1-3), puddly(4-6), snowy(7-9), icy(10-12), snowy/icy(13-15)
        friction = self.data.runwayFriction.value

        if not isinstance(friction, (int, float)):
            return

        if self.adjusted_friction is not None and c.isclose(friction, self.adjusted_friction, tol=0.01):
            # already adjusted value
            xp.log(f"[RF] already adjusted value: {friction}")
            return

        # -----------------------------
        # Decide desired state
        # -----------------------------
        if friction < 6:
            desired = None
        else:
            desired = c.map_friction(friction)

        # -----------------------------
        # Release
        # -----------------------------
        if desired is None:
            if self.adjusted_friction is not None:
                xp.log("[RF] releasing friction override")
                self.adjusted_friction = None
                self.xp_runway_friction = None
            return

        # -----------------------------
        # Apply or change override
        # -----------------------------
        if self.adjusted_friction != desired:
            self.adjusted_friction = desired
        if self.xp_runway_friction != friction:
            self.xp_runway_friction = friction

        if friction != desired:
            try:
                self.data.runwayFriction.value = desired
                xp.log(f"[RF] adjusted {friction:.1f} → {desired}")
            except SystemError as e:
                xp.log(f"[RF] ERROR injecting runway friction: {e}")

    def setDrefIfDiff(self, dref, value: float, max_diff: Optional[float] = False) -> bool:
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

    def reset_weather(self) -> None:
        if self.nearest_snow:
            # reset nearest snow value
            self.nearest_snow = False
        c.transitionClearReferences()

    def weatherInfo(self, chars: int = 80) -> list[str]:
        """Return an array of strings with formatted weather data"""
        verbose = self.conf.verbose
        sysinfo = [f"XPNoaaWeather for XP12 {self.conf.__VERSION__} Status:"]

        if not self.weatherData:
            sysinfo += ['* Data not ready. Please wait...']
        else:
            wdata = self.weatherData
            if 'info' in wdata:
                lat, lon = self.data.latdr.value, self.data.londr.value
                sysinfo += [
                    '   LAT: %.2f/%.2f LON: %.2f/%.2f FL: %02.f MAGNETIC DEV: %.2f' % (
                        lat, wdata['info']['lat'], lon, wdata['info']['lon'],
                        c.m2ft(self.data.altdr.value) / 100, self.data.mag_deviation.value)
                ]
                if not self.data.real_weather_enabled:
                    sysinfo += [f"   XP12 Real Weather is not active (value = {self.data.xp_weather_source.value})"]
                elif 'None' in wdata['info']['gfs_cycle']:
                    sysinfo += ['   XP12 is still downloading weather info ...']
                elif self.conf.use_real_weather_data:
                    sysinfo += [f"   GFS Cycle: {wdata['info']['rw_gfs_cycle']}"]
                else:
                    sysinfo += [f"   GFS Cycle: {wdata['info']['gfs_cycle']}"]

            if 'metar' in wdata and 'icao' in wdata['metar']:
                sysinfo += [
                    '',
                    f"{self.conf.metar_source} METAR:"
                ]
                # Split metar if needed
                metar = f"{wdata['metar']['icao']} {wdata['metar']['metar']}"
                sysinfo += util.format_text(metar, chars, 3)

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

                if 'rwmetar' in wdata and self.conf.use_real_weather_data:
                    if not wdata['rwmetar'].get('file_time'):
                        sysinfo += ['XP12 REAL WEATHER METAR:', '   no METAR file, still downloading...']
                    else:
                        sysinfo += [f"XP12 REAL WEATHER METAR ({wdata['rwmetar']['file_time']}):"]
                        line = f"{wdata['rwmetar']['result'][0]} {wdata['rwmetar']['result'][1]}"
                        sysinfo += util.format_text(line, chars, 3)
                    # check actual pressure and adjusted friction
                    sysinfo += ['', 'XP12 REAL WEATHER LIVE PARAMETERS:']
                    wind_d = round(self.data.wind_dir.value)
                    wind_s = round(c.ms2knots(self.data.wind_spd.value))
                    line = f"   Wind {wind_d} at {wind_s}"
                    vis_m, vis_sm = round(c.sm2m(self.data.visibility.value)), round(self.data.visibility.value, 1)
                    line += f" | Vis: {vis_m}m ({vis_sm}sm)"
                    temp = round(self.data.temp.value, 1)
                    line += f" | Temp {temp}C"
                    pressure = self.data.pressure.value / 100  # mb
                    pressure_inHg = c.mb2inHg(pressure)
                    line += f" | Press. at sea lvl: {pressure:.1f}mb ({pressure_inHg:.2f}inHg)"
                    sysinfo += [line]
                    friction = self.data.runwayFriction.value
                    line = f"   Runway Friction: {friction:02}"
                    if self.adjusted_friction is not None:
                        line += f" (adjusted from {self.xp_runway_friction:02})"
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
                    pass
                else:
                    # GFS data download for testing is enabled
                    sysinfo += [
                        '*** *** GFS 0.25 degrees weather data download *** ***'
                    ]
                    gfs = wdata['gfs']
                    if 'surface' in gfs and len(gfs['surface']):
                        s = gfs['surface']
                        surface_temp = round(c.kel2cel(s.get('temp')), 1)
                        snow = s.get('snow')
                        d = 0
                        if c.is_exponential(snow):
                            if self.nearest_snow:
                                nlat, nlon, snow = self.nearest_snow.values()
                                d = round(c.m2nm(c.greatCircleDistance((lat, lon), (nlat, nlon))), 1)  # in nm
                            else:
                                snow = None
                        snow_depth = f"{'na' if snow is None or snow < 0 else round(snow, 2)}{'' if not d else f' ({d} nm)'}"
                        acc_precip = 'na' if (s.get('acc_precip') is None or s.get('acc_precip') < 0) else round(s.get('acc_precip'), 2)
                        sysinfo += [
                            f"   sfc temp (C): {surface_temp} | snow depth (m): {snow_depth} | accumulated precip. (kg/sqm): {acc_precip}",
                            ''
                        ]
                    else:
                        # probably there was an error downloading data from NOAA server
                        sysinfo += [
                            'No precipitation data available. Check log files'
                        ]

                if 'rw' in wdata and self.conf.use_real_weather_data:
                    # XP12 Real Weather is enabled
                    rw = wdata['rw']
                    if 'winds' in rw:
                        sysinfo += ['XP12 REAL WEATHER WIND LAYERS: FL | HDG KT | TEMP | DEV']
                        wlayers = ''
                        out = []
                        for i, layer in enumerate(rw['winds'], 1):
                            alt, hdg, speed, extra = layer
                            wind = f"{hdg:03.0f} {speed:>3.0f}kt"
                            temp = round(c.kel2cel(extra['temp']))
                            dev = round(c.kel2cel(extra['dev']))
                            wlayers += f"    F{c.m2fl(alt):03} | {wind} | {temp:> 3} | {dev:> 3}"
                            if i % 3 == 0 or i == len(rw['winds']):
                                out.append(wlayers)
                                wlayers = ''
                        sysinfo += out

                    if 'tropo' in rw and rw['tropo'].values():
                        alt, temp, dev = rw['tropo'].values()
                        if alt and temp and dev:
                            sysinfo += [f"TROPO LIMIT: {round(alt)}m (F{c.m2fl(alt):03}) | "
                                        f"temp {round(c.kel2cel(temp))}C ISA Dev {round(c.kel2cel(dev))}C"]

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
                                clayers += f"    {c.m2fl(base):03} | {c.m2fl(top):03} | {cover:.0f}%"
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
                            value = f"{round(layer[1] * 10, 1):.1f}" if layer[1] < self.conf.max_turbulence else '*'
                            tblayers += f"    F{fl:03} | {value:3}"
                            if i % 7 == 0 or i == len(wafs):
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
                            value = f"{round(layer[1] * 10, 1):.1f}" if layer[1] < self.conf.max_turbulence else '*'
                            tblayers += f"    F{fl:03} | {value:3}"
                            if i % 7 == 0 or i == len(wafs):
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

        return sysinfo

    def cleanup(self) -> None:
        """Cleanup datarefs"""
        datarefs = [el.dref for el in self.data.__dict__.values() if isinstance(el, DataRef)]
        for dr in datarefs:
            xp.unregisterDataAccessor(dr)

    def dumpLog(self) -> Path:
        """Dumps all the information to a file to report bugs"""
        import platform
        from pprint import pprint

        dumpath = Path(self.conf.cachepath, 'dumplogs')
        dumpath.mkdir(parents=True, exist_ok=True)

        dumplog = Path(dumpath, datetime.utcnow().strftime('%Y%m%d_%H%M%SZdump.txt'))
        xp.log(f"creating dumplog file: {dumplog}")

        f = open(dumplog, 'w')

        xpver, sdkver, hid = xp.getVersions()
        output = [
            '--- Platform Info ---\n',
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

        pprint(self.weatherData, f, width=160)
        f.write('\n--- Transition data Data --- \n')
        pprint(c.transrefs, f, width=160)

        f.write('\n--- Weather Datarefs --- \n')

        # dump datarefs
        pprint(self.data.dump(), f, width=160)

        f.write('\n--- Configuration ---\n')
        vars = {}
        for var in self.conf.__dict__:
            if type(self.conf.__dict__[var]) in (str, int, float, list, tuple, dict):
                vars[var] = self.conf.__dict__[var]
        pprint(vars, f, width=160)

        # Append tail of PythonInterface log files
        logfiles = [
            'PythonInterfaceLog.txt',
            'PythonInterfaceOutput.txt',
            Path('noaaweather', 'weatherServerLog.txt'),
        ]

        for logfile in logfiles:
            try:
                filepath = Path(xp.PLUGINSPATH, logfile)
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
