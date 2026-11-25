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

import json
import socket
import threading
import subprocess

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import utils, xp, c, dref, DataRef, log


class Weather:
    """Sets x-plane weather from GFS parsed data"""

    # snow default values
    SNOW_DEFAULT_VALUES = {
        'frozen_water': 0,
        'tarmac_snow_width': 0.25,
        'tarmac_snow_scale': 500,
        'tarmac_snow_noise': 0.04
    }

    # weather data further than this from the aircraft (nm) is the data of a previous place, i.e. right after an airport change
    STALE_DATA_NM = 30

    def __init__(self, conf) -> None:

        self.alt = 0.0
        self.ref_winds = {}
        self.lat = 99.0
        self.lon = 99.0
        self.last_lat = False
        self.last_lon = False

        self.conf = conf
        self.data = dref.Dref()
        self.last_metar_station = False

        # runway friction values
        self.xp_runway_friction = None
        self.metar_friction = None
        self.adjusted_friction = None

        # Data
        self.weather_data = False
        self.weather_client_thread = False

        self.windAlts = -1
        self.nearest_snow = False

        # cache to avoid recomputation
        self._last_factor_input = None
        self._cached_factor = 0.0
        self._last_logged_val = None

        # Response queue for user queries
        self.query_response = []

        # Create client socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.die = threading.Event()
        self.lock = threading.Lock()

        self.new_data = False

        # keep the handle: the server watches our end of its stdin pipe, closing it would stop the server
        self.server_process = None
        self.start_weather_server()

    def start_weather_client(self ) -> None:
        thread = self.weather_client_thread
        if not (thread and thread.is_alive()):
            self.die.clear()
            self.sock.settimeout(1.0)  # lets the client thread notice self.die
            # daemon: a stuck client can never keep X-Plane from exiting
            self.weather_client_thread = threading.Thread(
                target=self.weather_client,
                name='noaaweather-client',
                daemon=True
            )
            self.weather_client_thread.start()

    def weather_client(self) -> None:
        """Weather client thread fetches weather from Weather Server"""

        # Send something for windows to bind
        self.weather_client_send('!ping')

        while not self.die.is_set():
            try:
                received = self.sock.recv(65535)  # max UDP datagram: nothing gets truncated
            except socket.timeout:
                continue
            except OSError:
                if not self.die.is_set():
                    log.exception("Weather client socket error")
                    self.die.wait(1)
                continue

            log.debug(
                f"Weather client received datagram: "
                f"{len(received)} bytes, "
                f"content={received[:120]!r}"
            )

            try:
                wdata = json.loads(received)
            except Exception:
                log.exception("Weather client JSON error")
                continue

            log.debug(
                f"Weather response type: {type(wdata).__name__}, "
                f"keys: {list(wdata.keys()) if isinstance(wdata, dict) else wdata!r}"
            )

            if wdata == '!bye':
                log.info("Weather client received: server is shutting down")
                break
            elif not isinstance(wdata, dict):
                # corrupted datagram: skip it, the thread must survive
                log.debug(f"Weather client: discarded a bad datagram ({len(received)} bytes): not a dict")
                continue
            elif 'info' not in wdata:
                # A metar query response
                self.query_response.append(wdata)
            else:
                self.weather_data = wdata
                self.new_data = True
                log.debug("Weather data successfully received and stored.")

    def weather_client_send(self, msg: str) -> None:
        if self.weather_client_thread:
            self.sock.sendto(msg.encode('utf-8'), ('127.0.0.1', self.conf.server_port))

    def start_weather_server(self) -> None:
        DETACHED_PROCESS = 0x00000008
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

        server_script = Path(self.conf.scripts_path, 'weatherserver.py')
        args = [
            xp.pythonExecutable,
            server_script,
            self.conf.xplane_path,
            '--parent-pipe'
        ]
        # the server stops by itself when this pipe closes, i.e. when X-Plane exits or crashes
        kwargs = {'close_fds': True, 'stdin': subprocess.PIPE}
        try:
            if self.conf.spinfo:
                kwargs.update({
                    'startupinfo': self.conf.spinfo,
                    'creationflags': DETACHED_PROCESS | BELOW_NORMAL_PRIORITY_CLASS,
                })
            log.info(f"Starting Weather Server using: {args} {kwargs}")
            self.server_process = subprocess.Popen(args, **kwargs)
            log.debug(f"Weather Server process created: PID={self.server_process.pid}")
        except Exception:
            log.exception("Exception while executing subprocess")

    def shutdown(self) -> None:
        """Shutdown client and server"""

        self.weather_client_send('!shutdown')

        self.weather_client_thread = False
        self.die.set()

        # Signal EOF to the weather server's parent-watch thread.
        if self.server_process and self.server_process.stdin:
            try:
                self.server_process.stdin.close()
            except OSError:
                pass

    def get_XP12_METAR(self, icao: str) -> str:
        return xp.getMETARForAirport(icao)

    def set_snow(self, elapsed: float) -> None:
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

        if not self.data.check_snow_dref():
            # something went wrong initializing snow datarefs
            return

        data = ((self.weather_data or {}).get('rw') or {}).get('surface')
        if not data or 'snow' not in data or 'temp' not in data:
            # no Real Weather surface data (yet): files still downloading or no SNOD in them
            return

        if self.data.snow_override.value == 0:
            # enable snow coverage control (on 12.4-b1 does not seem to have any effect)
            self.data.snow_override.value = 1
        snow = data['snow']
        lat = self.data.latdr.value if isinstance(self.data.latdr.value, (int, float)) else 0.0
        lon = self.data.londr.value if isinstance(self.data.londr.value, (int, float)) else 0.0
        temp = c.kel2cel(data['temp'])
        transitions_speed = 0.25 if self.data.on_ground else 0.01

        # The data belongs to the position it was requested for. After an airport change (or any jump) it is still the
        # data of the previous place until the new response arrives: using it would remember the old snow depth at the new position
        info = self.weather_data.get('info') or {}
        dlat, dlon = info.get('lat', lat), info.get('lon', lon)
        if c.m2nm(c.greatCircleDistance((lat, lon), (dlat, dlon))) > self.STALE_DATA_NM:
            return

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
                'lat': dlat,
                'lon': dlon,
                'depth': snow
            }

        # ------------------------------------------------------------------
        # No snow → defaults
        # ------------------------------------------------------------------
        if snow <= 0.0:
            val = 0.0
            frozen_water, noise, scale, width = self.SNOW_DEFAULT_VALUES.values()
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

                self.set_dref_if_different(self.data.tarmac_snow_noise, noise)
                self.set_dref_if_different(self.data.tarmac_snow_scale, scale)
                self.set_dref_if_different(self.data.tarmac_snow_width, width)

                # log only when change is visible
                if self._last_logged_val is None or abs(val - self._last_logged_val) > 0.01:
                    log.debug(
                        f" Snow cover: {val:.3f} | depth {snow:.2f} m | lat {lat:.1f} | temp {temp:.1f}C"
                    )
                    self._last_logged_val = val

            except SystemError:
                log.exception("ERROR injecting snow_cover")

        # ------------------------------------------------------------------
        # Inject secondary drefs
        # ------------------------------------------------------------------
        self.set_dref_if_different(self.data.iced_tarmac, ice)
        self.set_dref_if_different(self.data.puddles, puddles)

    def set_runway_friction(self, elapsed: float) -> None:
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
                log.debug("[RF] releasing friction override")
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
                log.debug(f"[RF] adjusted {friction:.1f} → {desired}")
            except SystemError:
                log.exception("[RF] ERROR injecting runway friction")

    def set_dref_if_different(self, dref, value: float, max_diff: Optional[float] = False) -> bool:
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

    def weather_info(self, chars: int = 80) -> list[str]:
        """Return an array of strings with formatted weather data"""
        rw_enabled = self.data.real_weather_enabled
        sysinfo = [f"XPNoaaWeather for XP12 {self.conf.__VERSION__} Status:"]

        if not self.weather_data:
            sysinfo += ['* Data not ready. Please wait...']
        else:
            wdata = self.weather_data
            if 'info' in wdata:
                lat, lon = self.data.latdr.value, self.data.londr.value
                sysinfo += [
                    '   LAT: %.2f/%.2f LON: %.2f/%.2f FL: %02.f MAGNETIC DEV: %.2f' % (
                        lat, wdata['info']['lat'], lon, wdata['info']['lon'],
                        c.m2ft(self.data.altdr.value) / 100, self.data.mag_deviation.value)
                ]
                if not self.data.real_weather_enabled:
                    sysinfo += ["   XP12 Real Weather IS NOT ACTIVE"]
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
                sysinfo += utils.format_text(metar, chars, 3)

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
                        wind += f", gusting {wdata['metar']['wind'][2]}kt"
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

                if 'rwmetar' in wdata and rw_enabled and self.conf.use_real_weather_data:
                    if not wdata['rwmetar'].get('file_time'):
                        sysinfo += ['XP12 REAL WEATHER METAR:', '   no METAR file, still downloading...']
                    else:
                        sysinfo += [f"XP12 REAL WEATHER METAR ({wdata['rwmetar']['file_time']}):"]
                        line = f"{wdata['rwmetar']['result'][0]} {wdata['rwmetar']['result'][1]}"
                        sysinfo += utils.format_text(line, chars, 3)

            # check actual pressure and adjusted friction
            sysinfo += ['', f'XP12 {"REAL" if rw_enabled else "CUSTOM"} WEATHER LIVE PARAMETERS:']
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
                    # at the moment we are not using GFS data, but we could use it in the future
                    pass

                if rw_enabled and self.conf.use_real_weather_data and'rw' in wdata:
                    # XP12 Real Weather is enabled
                    rw = wdata['rw']

                    # X-Plane 12.4.4 and above should have SNOD in Real Weather Grib files data
                    if 'surface' in rw and len(rw['surface']):
                        sysinfo += ['XP12 REAL WEATHER SURFACE DATA:']
                        s = rw['surface']
                        elev = 'na' if s.get('alt') is None else round(s.get('alt'))
                        surface_temp = 'na' if s.get('temp') is None else round(c.kel2cel(s.get('temp')), 1)
                        snow = s.get('snow')
                        d = 0
                        if c.is_exponential(snow):
                            # we are probably over water or very close to water
                            # let's look for prediction data
                            if self.nearest_snow:
                                nlat, nlon, snow = self.nearest_snow.values()
                                d = round(c.m2nm(c.greatCircleDistance((lat, lon), (nlat, nlon))), 1)  # in nm
                            else:
                                snow = None
                        snow_depth = f"{'na' if snow is None or snow < 0 else round(snow, 2)}{'' if not d else f' ({d} nm from present position)'}"
                        # acc_precip = 'na' if (s.get('acc_precip') is None or s.get('acc_precip') < 0) else round(s.get('acc_precip'), 2)
                        sysinfo += [
                            f"   elev. (ft): {elev} | temp (C): {surface_temp} | snow depth (m): {snow_depth}",
                            ''
                        ]
                    else:
                        # probably there was an error downloading data from NOAA server
                        sysinfo += [
                            'No precipitation data available. Check log files'
                        ]

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
                    sysinfo += ['']

                else:
                    '''Normal GFS mode'''
                    pass

        return sysinfo

    def cleanup(self) -> None:
        """Cleanup datarefs"""
        datarefs = [el.dref for el in self.data.__dict__.values() if isinstance(el, DataRef)]
        for dr in datarefs:
            xp.unregisterDataAccessor(dr)

    def dump_log(self) -> Path:
        """Dumps all the information to a file to report bugs"""
        import platform
        from pprint import pprint

        dumpath = Path(self.conf.cache_path, 'dumplogs')
        dumpath.mkdir(parents=True, exist_ok=True)

        dumplog = Path(dumpath, datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%SZdump.txt'))
        log.info(f"creating dumplog file: {dumplog}")

        f = open(dumplog, 'w', encoding='utf-8')

        xpver, sdkver, hid = xp.getVersions()
        output = [
            '--- Platform Info ---\n',
            f"Plugin version: {self.conf.__VERSION__}\n",
            f"Xplane Version: {round(xpver/1000, 3)}, SDK Version: {round(sdkver/100, 2)}\n",
            f"Platform: {platform.platform()}\n",
            f"Python version: {platform.python_version()}\n",
            '\n--- Weather Status ---\n'
        ]

        for line in self.weather_info():
            output.append(f"{line}\n")

        output += ['\n--- Weather Data ---\n']

        for line in output:
            f.write(line)

        pprint(self.weather_data, f, width=160)
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
        logfile = self.conf.server_log_file

        if logfile.is_file():
            lfsize = logfile.stat().st_size
            # binary: a text mode seek can land inside a multi-byte character (i.e. the arrow in our own log lines)
            with open(logfile, 'rb') as lf:
                lf.seek(lfsize - c.limit(1024 * 6, lfsize))
                tail = lf.read().decode('utf-8', errors='replace')
            f.write(f"\n--- {logfile} ---\n\n")
            f.write(tail.replace('\r\n', '\n'))

        f.close()

        return dumplog
