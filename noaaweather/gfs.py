"""
NOAA weather daemon server

---
X-plane NOAA GFS weather plugin.
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2024 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

from . import c
from .weathersource import GribWeatherSource


class GFS(GribWeatherSource):
    """NOAA GFS weather source"""

    base_url = 'https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.'

    download = False
    download_wait = 0

    def __init__(self, conf):
        self.variable_list = conf.gfs_variable_list
        self.download_enabled = conf.download_GFS
        super().__init__(conf)

    @classmethod
    def get_download_url(cls, datecycle: str, cycle: int, forecast: int) -> str:
        """ Returns the GRIB download url add .idx or .grib to the end
            it will use more precise 0.25 grid GFS data instead of 0.50
            The file is about 10mb, still testing"""
        filename = f"gfs.t{cycle:02}z.pgrb2.0p25.f0{forecast:02}"
        # filename = f"gfs.t{cycle:02}z.pgrb2full.0p50.f0{forecast:02}"
        return f"{cls.base_url}{datecycle}/{cycle:02}/atmos/{filename}"

    @classmethod
    def get_cache_filename(cls, datecycle: str, cycle: int, forecast: int) -> str:
        """Returns the proper filename for the cache"""
        return f"{datecycle}_gfs.t{cycle:02}z.pgrb2.0p25.f0{forecast:02}"
        # return f"{datecycle}_gfs.t{cycle:02}z.pgrb2full.0p50.f0{forecast:02}"

    def parse_grib_data(self, filepath, lat: float, lon: float) -> dict:
        """Executes wgrib2 and parses its output"""

        it = self.read_grib_file(filepath, lat, lon)
        winds = {}
        clouds = {}
        pressure = False
        tropo = {}
        surface = {}

        for line in it:
            r = line.split(':')
            # Level, variable, value
            level, variable, value = [r[4].split(' '), r[3], r[7].split(',')[2].split('=')[1]]

            if len(level) > 1:
                if level[1] == 'cloud':
                    # cloud layer
                    clouds.setdefault(level[0], {})
                    if len(level) > 3 and variable == 'PRES':
                        clouds[level[0]][level[2]] = value
                    else:
                        # level coverage/temperature
                        clouds[level[0]][variable] = value
                elif level[1] == 'mb':
                    # wind levels
                    winds.setdefault(level[0], {})
                    winds[level[0]][variable] = value
                elif level[0] == 'mean':
                    if variable == 'PRMSL':
                        pressure = c.pa2inhg(float(value))
            elif level[0] == 'tropopause':
                tropo[variable] = value
            elif level[0] == 'surface':
                surface[variable] = value

        windlevels = []
        cloudlevels = []
        templevels = []

        # Let data ready to push on datarefs.

        # Convert wind and temperature levels
        for level, wind in iter(winds.items()):
            if 'UGRD' in wind and 'VGRD' in wind:
                hdg, vel = c.c2p(float(wind['UGRD']), float(wind['VGRD']))
                alt = int(c.mb2alt(float(level)))

                # Optional varialbes
                temp, dev, rh, dew = False, False, False, False
                # Temperature
                if 'TMP' in wind:
                    temp = float(wind['TMP'])
                    dev = c.isaDev(alt, temp)
                # Relative Humidity
                if 'RH' in wind:
                    rh = float(wind['RH'])

                if temp and rh:
                    dew = c.dewpoint(temp, rh)

                windlevels.append(
                    [
                        alt, 
                        hdg, 
                        c.ms2knots(vel), 
                        {'temp': temp, 'dev': dev, 'rh': rh, 'dew': dew, 'gust': 0}
                    ]
                )
                if alt and temp:
                    templevels.append([alt, temp, dev, dew])

                # get tropopause info from F386 wind level if not already available
                if not tropo and float(level) == 200 and temp:
                    tropo = {'PRES': level, 'TMP': wind['TMP']}

        # Convert cloud level
        for level in clouds.values():
            if all(k in level.keys() for k in ('top', 'bottom')):
                top, bottom = float(level['top']) * 0.01, float(level['bottom']) * 0.01  # mb
                cover = float(level.get(next((k for k in level.keys() if k in ('LCDC', 'MCDC', 'HCDC')), None)) or 0)

                if cover:
                    cloudlevels.append([round(c.mb2alt(bottom)), round(c.mb2alt(top)), cover])

        windlevels.sort()
        cloudlevels.sort()
        templevels.sort()

        # tropo
        if all(k in tropo.keys() for k in ('PRES', 'TMP')):
            alt = int(c.mb2alt(float(tropo['PRES'])*0.01))
            temp = float(tropo['TMP'])
            dev = c.isaDev(alt, temp)
            tropo = {'alt': float(alt), 'temp': temp, 'dev': dev}
        else:
            tropo = {}

        # surface
        default = {'PRES': 'press', 'TMP': 'temp', 'HGT': 'alt', 'SNOD': 'snow', 'APCP': 'apcp'}
        if all(x in surface.keys() for x in default.keys()):
            for k, v in default.items():
                surface[v] = float(surface.pop(k)) if k != 'PRES' else float(surface.pop(k)) * 0.01
        else:
            [surface.pop(k) for k in default.keys()]

        data = {
            'winds': windlevels,
            'clouds': cloudlevels,
            'temperature': templevels,
            'pressure': pressure,
            'tropo': tropo,
            'surface': surface
        }

        return data

    def check_snow_values(self, filepath, lat, lon, hdg, gfs):
        snow = gfs['surface'].get('snow')
        if not c.is_exponential(snow):
            return

        # we have no info about snow, probably over or near water
        # look for valid values around origin
        # GFS precision is 0.25 degree (27.75 km grid) so we use a distance of 28 Km
        prediction = False
        d = 28000  # 28 Km, 1 GFS grid
        v = 0      # North
        while True:
            new_lon, new_lat = c.great_circle_destination(lon, lat, v, d)
            data = self.parse_grib_data(filepath, new_lat, new_lon)
            val = data['surface'].get('snow')
            if val is not None and not c.is_exponential(val):
                prediction = {
                    'lat': new_lat,
                    'lon': new_lon,
                    'depth': val
                }
                gfs['surface']['prediction'] = prediction
                return
            v += 45
            if v > 330:
                break 

        # look for valid values ahead along present track
        d = 28000  # 28 Km, 1 GFS grid
        while True:
            new_lon, new_lat = c.great_circle_destination(lon, lat, hdg, d)
            data = self.parse_grib_data(filepath, new_lat, new_lon)
            val = data['surface'].get('snow')
            if val is not None and not c.is_exponential(val):
                prediction = {
                    'lat': new_lat,
                    'lon': new_lon,
                    'depth': val
                }
                gfs['surface']['prediction'] = prediction
                print(f"check_snow_values: prediction: {prediction}")
                return
            d += 28000
            if d > 60000:
                break
