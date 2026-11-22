"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2026 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

from math import hypot, atan2, degrees, exp, log, radians, sin, cos, asin, sqrt, pi, isclose
from random import random
from typing import Optional

# const
EARTH_RADIUS = 6378137  # meters

class c:
    """Unit conversion  and misc tools"""

    # transition references
    transrefs = {}
    randRefs = {}

    @staticmethod
    def ms2knots(val: float) -> float:
        return val * 1.94384

    @staticmethod
    def kel2cel(val: float) -> float:
        return val - 273.15

    @staticmethod
    def c2p(x: float, y: float) -> tuple[float, float]:
        # Cartesian 2 polar conversion
        r = hypot(x, y)
        a = degrees(atan2(x, y))
        if a < 0:
            a += 360
        if a <= 180:
            a = a + 180
        else:
            a = a - 180
        return a, r

    @staticmethod
    def mb2inHg(mb: float) -> float:
        return mb / 33.8639

    @staticmethod
    def inHg2mb(inches) -> float:
        return inches * 33.8639

    @staticmethod
    def mb2alt(mb: float) -> float:
        return (1 - (mb / 1013.25) ** 0.190284) * 44307  # meters

    @staticmethod
    def mb2ft(mb) -> float:
        return (1 - (mb / 1013.25) ** 0.190284) * 145366.45

    @staticmethod
    def mb2fl(mb: float) -> int:
        return int((1 - (mb / 1013.25) ** 0.190284) * 1453.6645)


    @staticmethod
    def m2ft(n: float|bool) -> float:
        return False if n is False else n * 3.280839895013123

    @staticmethod
    def m2fl(n: float|bool) -> int:
        return False if n is False else int(n * 0.03280839895013123)

    @staticmethod
    def f2m(n: float|bool) -> bool | float:
        return False if n is False else n * 0.3048

    @staticmethod
    def sm2m(n: float|bool) -> bool | float:
        return False if n is False else n * 1609.344

    @staticmethod
    def m2sm(n: float|bool) -> bool | float:
        return False if n is False else n * 0.0006213711922373339

    @staticmethod
    def m2nm(n: float|bool) -> bool | float:
        return False if n is False else n * 0.0005399568

    @staticmethod
    def m2kn(n: float|bool) -> bool | float:
        return False if n is False else n * 1852

    @staticmethod
    def dm2dd(degrees: str, minutes: str, direction: str) -> float:
        dd = float(degrees) + float(minutes)/60
        if direction == 'W' or direction == 'S':
            dd *= -1
        return dd

    @staticmethod
    def parse_dm(string: str) -> tuple[float, float]:
        parts = string.split()
        return c.dm2dd(parts[0], parts[1][:-1], parts[1][-1]), c.dm2dd(parts[2], parts[3][:-1], parts[3][-1])

    @staticmethod
    def oat2msltemp(oat: float, alt: float, tropo_temp: float=-56.5, tropo_alt: float=11000) -> float:
        """Converts oat temperature to mean sea level.
        oat in C, alt in meters
        http://en.wikipedia.org/wiki/International_Standard_Atmosphere#ICAO_Standard_Atmosphere
        from FL360 (11km) to FL655 (20km) the temperature deviation stays constant at -71.5degreeC
        from MSL up to FL360 (11km) the temperature decreases at a rate of 6.5degreeC/km
        The original code was:
        if alt > tropo:
            return oat + 71.5
        return oat + 0.0065 * alt

        In X-Plane Temperature profile is linear, between msl t and tropo limit t.
        So to have a correct temperature at various levels according to GFS, we must use proportions
        """

        if alt > tropo_alt:
            return oat + 71.5
        gradient = (tropo_temp - oat) / (alt - tropo_alt)
        # print(f"tropo temp {tropo_temp} oat {oat} alt {alt} tropo alt {tropo_alt} grad {gradient}")
        return oat + gradient * alt

    @staticmethod
    def clamp01(value: float) -> float:
        return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value

    @staticmethod
    def clamp(value: float, min_val: float, max_val: float) -> float:
        return min_val if value < min_val else max_val if value > max_val else value

    @staticmethod
    def greatCircleDistance(latlong_a: tuple[float, float], latlong_b: tuple[float, float]) -> float:
        """Return the great circle distance of 2 coordinates pairs, in meters"""

        lat1, lon1 = latlong_a
        lat2, lon2 = latlong_b

        dLat = radians(lat2 - lat1)
        dLon = radians(lon2 - lon1)
        a = (sin(dLat / 2) * sin(dLat / 2) +
             cos(radians(lat1)) * cos(radians(lat2)) *
             sin(dLon / 2) * sin(dLon / 2))
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        d = EARTH_RADIUS * c
        return d

    @staticmethod
    def great_circle_destination(lon1: float, lat1: float, bearing: float, dist: float = 50000) -> tuple[float, float]:
        """ Formula:	
            φ2 = asin( sin φ1 ⋅ cos δ + cos φ1 ⋅ sin δ ⋅ cos θ )
            λ2 = λ1 + atan2( sin θ ⋅ sin δ ⋅ cos φ1, cos δ − sin φ1 ⋅ sin φ2 )
            where:	φ is latitude, λ is longitude, θ is the bearing (clockwise from north), δ is the angular distance d/R; d being the distance travelled, R the earth’s radius"""

        d = dist/EARTH_RADIUS
        b = radians(bearing)
        latr, lonr = radians(lat1), radians(lon1)
        lat2 = asin(sin(latr) * cos(d) + cos(latr) * sin(d) * cos(b))
        lon2 = lonr + atan2(sin(b) * sin(d) * cos(latr), cos(d) - sin(latr) * sin(lat2))
        return degrees(lon2), degrees(lat2)

    @staticmethod
    def interpolate(t1: float, t2: float, alt1: float, alt2: float, alt: float) -> float:
        if (alt2 - alt1) == 0:
            return t2
        return t1 + (alt - alt1) * (t2 - t1) / (alt2 - alt1)

    @staticmethod
    def expoCosineInterpolate(t1: float, t2: float, alt1: float, alt2: float, alt: float, expo: int=3) -> float:
        if alt1 == alt2: return t1
        x = (alt - alt1) / float(alt2 - alt1)
        return t1 + (t2 - t1) * x ** expo

    @staticmethod
    def cosineInterpolate(t1: float, t2: float, alt1: float, alt2: float, alt: float) -> float:
        if alt1 == alt2: return t1
        x = (alt - alt1) / float(alt2 - alt1)
        return t1 + (t2 - t1) * (0.5 - cos(pi * x) / 2)

    @staticmethod
    def cosineInterpolateHeading(hdg1: float, hdg2: float, alt1: float, alt2: float, alt: float) -> float:

        if alt1 == alt2: return hdg1

        t2 = c.shortHdg(hdg1, hdg2)
        t2 = c.cosineInterpolate(0, t2, alt1, alt2, alt)
        t2 += hdg1

        if t2 < 0:
            return t2 + 360
        else:
            return t2 % 360

    @staticmethod
    def expoCosineInterpolateHeading(hdg1: float, hdg2: float, alt1: float, alt2: float, alt: float, expo: int=3) -> float:

        if alt1 == alt2: return hdg1

        t2 = c.shortHdg(hdg1, hdg2)
        t2 = c.expoCosineInterpolate(0, t2, alt1, alt2, alt, expo)
        t2 += hdg1

        if t2 < 0:
            return t2 + 360
        else:
            return t2 % 360

    @staticmethod
    def interpolateHeading(hdg1: float, hdg2: float, alt1: float, alt2: float, alt: float) -> float:
        if alt1 == alt2: return hdg1

        t1 = 0
        t2 = c.shortHdg(hdg1, hdg2)

        t2 = t1 + (alt - alt1) * (t2 - t1) / (alt2 - alt1)

        t2 += hdg1

        if t2 < 0:
            return t2 + 360
        else:
            return t2 % 360

    @staticmethod
    def fog2(rh: float) -> float:
        return (80 - rh) / 20 * 24634

    @staticmethod
    def isaDev(alt: float, temp: float) -> float:
        """Calculates Temperature ISA Deviation"""
        isa = 15 - 0.65*alt/100
        return temp - isa

    @staticmethod
    def toFloat(string: str, default: float=0) -> float:
        """Convert to float or return default"""
        try:
            val = float(string)
        except ValueError:
            val = default
        return val

    @staticmethod
    def toInt(string: str, default: int=0) -> int:
        """Convert to float or return default"""
        try:
            val = int(string)
        except ValueError:
            val = default
        return val

    @staticmethod
    def rh2visibility(rh: float) -> float:
        # http://journals.ametsoc.org/doi/pdf/10.1175/2009JAMC1927.1
        return 1000 * (-5.19 * 10 ** -10 * rh ** 5.44 + 40.10)

    @staticmethod
    def dewpoint2rh(temp: float, dew: float) -> float:
        return 100 * (exp((17.625 * dew) / (243.04 + dew)) / exp((17.625 * temp) / (243.04 + temp)))

    @staticmethod
    def dewpoint(temp: float, rh: float) -> float:
        return 243.04 * (log(rh / 100) + ((17.625 * temp) / (243.04 + temp))) / (
                    17.625 - log(rh / 100) - ((17.625 * temp) / (243.04 + temp)))

    @staticmethod
    def shortHdg(a: float, b: float) -> float:
        if a == 360: a = 0
        if b == 360: b = 0
        if a > b:
            cw = (360 - a + b)
            ccw = -(a - b)
        else:
            cw = -(360 - b + a)
            ccw = (b - a)
        if abs(cw) < abs(ccw):
            return cw
        return ccw

    @staticmethod
    def pa2inhg(pa: float) -> float:
        return pa * 0.0002952998016471232

    @classmethod
    def datarefTransition(cls, dataref, new: float, elapsed: float, speed: float=0.25, id: str|bool=False) -> None:
        """Timed dataref transition"""

        # Save reference to ignore x-plane roundings
        if not id:
            id = str(dataref.dref)
        if id not in cls.transrefs:
            cls.transrefs[id] = dataref.value

        # Return if the value is already set
        if cls.transrefs[id] == new:
            return

        current = cls.transrefs[id]

        if current > new:
            dir = -1
        else:
            dir = 1
        if abs(current - new) > speed * elapsed + speed:
            new = current + dir * speed * elapsed

        cls.transrefs[id] = new
        dataref.value = new

    @classmethod
    def snowDatarefTransition(cls, dataref, new: float, elapsed: float, speed: float) -> None:
        """Timed dataref transition"""
        snow_id = str(dataref.dref)
        if snow_id not in cls.transrefs:
            cls.transrefs[snow_id] = dataref.value

        current = cls.transrefs[snow_id]
        s = -1 if current > new else 1
        if abs(current - new) > speed * elapsed + speed:
            new = current + s * speed * elapsed

        cls.transrefs[snow_id] = new
        dataref.value = new

    @classmethod
    def transition(cls, new: float, id: str, elapsed: float, speed: float=0.25) -> float:
        """Time based transition """
        if not id in cls.transrefs:
            cls.transrefs[id] = new
            return new

        current = cls.transrefs[id]

        if current > new:
            dir = -1
        else:
            dir = 1
        if abs(current - new) > speed * elapsed + speed:
            new = current + dir * speed * elapsed

        cls.transrefs[id] = new

        return new

    @classmethod
    def transitionClearReferences(cls, refs: Optional[list|bool]=False, exclude: bool=False) -> None:
        """Clear transition references"""
        if exclude:
            for ref in list(cls.transrefs.keys()):
                if ref.split('-')[0] not in exclude:
                    cls.transrefs.pop(ref)
            return

        elif refs:
            for ref in list(cls.transrefs.keys()):
                if ref.split('-')[0] in refs:
                    cls.transrefs.pop(ref)
        else:
            cls.transrefs = {}

    @classmethod
    def transitionHdg(cls, new: float, id: str, elapsed: float, speed: float=0.25) -> float:
        """Time based wind heading transition """

        if not id in cls.transrefs:
            cls.transrefs[id] = new
            return new

        current = cls.transrefs[id]

        diff = c.shortHdg(current, float(new))

        if abs(diff) < speed * elapsed:
            newval = new
        else:
            if diff > 0:
                diff = 1
            else:
                diff = -1
            newval = current + diff * speed * elapsed
            if newval < 0:
                newval += 360
            else:
                newval %= 360

        cls.transrefs[id] = newval
        return newval

    @classmethod
    def datarefTransitionHdg(cls, dataref, new: float, elapsed: float, vel: float=1) -> None:
        """Time based wind heading transition"""
        id = str(dataref.dref)
        if id not in cls.transrefs:
            cls.transrefs[id] = dataref.value

        if cls.transrefs[id] == new:
            return

        current = cls.transrefs[id]

        diff = c.shortHdg(current, new)
        if abs(diff) < vel * elapsed:
            newval = new
        else:
            if diff > 0:
                diff = +1
            else:
                diff = -1
            newval = current + diff * vel * elapsed
            if newval < 0:
                newval += 360
            else:
                newval %= 360

        cls.transrefs[id] = newval
        dataref.value = newval

    @staticmethod
    def float_or_lower(string: str) -> float | str:
        el = string.rsplit('.')
        try:
            return float('.'.join(el[:2]))
        except ValueError:
            try:
                return float(el[0])
            except ValueError:
                return string.lower()

    @staticmethod
    def is_exponential(f: float) -> bool:
        if isinstance(f,float) and 'e' in str(f).lower():
            return True
        return False

    @staticmethod
    def limit(value: float, max_value: Optional[float]=None, min_value: Optional[float]=None) -> float:
        if isinstance(max_value, (int, float)) and value > max_value:
            return max_value
        elif isinstance(min_value, (int, float)) and value < min_value:
            return min_value
        else:
            return value

    @staticmethod
    def cc2xp_old(cover: float) -> int:
        # Cloud cover to X-plane
        xp = int(cover / 100.0 * 4)
        if xp < 1 and cover > 0:
            xp = 1
        elif cover > 89:
            xp = 4
        return xp

    @staticmethod
    def cc2xp(cover: float, base: float) -> int:
        """GFS Percent cover to XP
        As GFS tends to overestimate, clouds are cut under 10% coverage that seems to happen often with SKC"""
        if cover <= 10:
            return 0
        elif base > 6500:
            if cover < 30:
                return 1  # 'CIRRUS
            else:
                return 2  # CIRRUSTRATUS
        elif cover < 25:
            return 2  # 'FEW'
        elif cover < 50:
            return 3  # 'SCT'
        elif cover < 75:
            return 4  # 'BKN'
        elif cover < 90:
            return 5  # 'OVC'
        else:
            return 6  # 'STRATUS'

    @staticmethod
    def metar2xpprecipitation(kind: str, intensity: str, mod: str, recent: bool) -> tuple[float | bool, float | bool, float | bool]:
        """Return intensity of a metar precipitation"""

        ints = {'-': 0, '': 1, '+': 2}
        intv = ints[intensity]

        precipitation, friction, patchy = False, False, False

        precip = {
            'DZ': [0.1, 0.2, 0.3],
            'RA': [0.3, 0.5, 0.8],
            'SN': [0.25, 0.5, 0.8],  # Snow
            'SH': [0.7, 0.8, 1]
        }

        wet = {
            'DZ': 1,
            'RA': 1,
            'SN': 1,  # Icy conditions should be 2, but is too slippery
            'SH': 1,
        }

        if mod == 'SH':
            kind = 'SH'

        if kind in precip:
            precipitation = precip[kind][intv]
        if recent or intv == 0:
            patchy = 1
        if kind in wet:
            friction = wet[kind]

        return precipitation, friction, patchy

    @staticmethod
    def strFloat(i: float | bool, false_label: str='na') -> str:
        """Print a float or na if False"""
        if i is False:
            return false_label
        else:
            return f"{round(i, 2)}"

    @staticmethod
    def str03d(i: float | bool, false_label: str='na') -> str:
        """Print a 3 digit string with leading zeroes"""
        return false_label if i is False else f"{i:03.0F}"

    @classmethod
    def convertForInput(cls, value, conversion, toFloat=False, false_str='none'):
        # Make conversion and transform to int
        if value is False:
            value = False
        else:
            convert = getattr(cls, conversion)
            value = convert(value)

        if value is False:
            return false_str

        elif not toFloat:
            value = int(value)
        return str(value)

    @classmethod
    def convertFromInput(cls, string: str, conversion: str, default: float=False, toFloat: bool=False, max: float=False, min: float=False) -> float | int | bool:
        # Convert from str and convert
        value = cls.toFloat(string, default)

        if value is False:
            return False

        convert = getattr(cls, conversion)
        value = cls.limit(convert(value), max, min)

        if toFloat:
            return value
        else:
            return int(round(value))

    @classmethod
    def rand(cls, min: float = 0, max: float = 1) -> float:
        return min + random() * (max - min)

    @classmethod
    def randPattern(cls, id: str, max_val: float, elapsed: float, max_time: float=1, min_val: float=0, min_time: float=1, heading: bool=False) -> float:
        """ Creates random cosine interpolated "patterns" """

        if id in cls.randRefs:
            x1, x2, startime, endtime, time = cls.randRefs[id]
        else:
            x1, x2, startime, endtime, time = min_val, 0, 0, 0, 0

        if heading:
            ret = cls.cosineInterpolateHeading(x1, x2, startime, endtime, time)
        else:
            ret = cls.cosineInterpolate(x1, x2, startime, endtime, time)

        time += elapsed

        if time >= endtime:
            # Init randomness
            x2 = min_val + random() * (max_val - min_val)
            t2 = min_time + random() * (max_time - min_time)

            x1 = ret
            startime = time
            endtime = time + t2

        cls.randRefs[id] = x1, x2, startime, endtime, time

        return ret

    @staticmethod
    def middleHeading(hd1: float, hd2: float) -> float:
        if hd2 > hd1:
            return hd1 + (hd2 - hd1) / 2
        else:
            return hd2 + (360 + hd1 - hd2) / 2

    @staticmethod
    def gfs_levels_help_list() -> list:
        """Returns a text list of FL levels with corresponding pressure in millibars"""
        return [f"FL{c.mb2fl(i):03d} {i} mb" for i in reversed(range(100, 1050, 50))]

    @staticmethod
    def optimise_gfs_clouds(gfs_clouds: list) -> list:
        layers = c.copy_gfs_clouds(gfs_clouds)
        idx = 0
        while len(layers) > idx:
            base0, top0, cover0 = layers[idx]
            if cover0 == 0:
                del layers[idx]
            elif len(layers) > idx + 1:
                base1, top1, cover1 = layers[idx + 1]
                if c.isclose(top0, base1, 500) and (
                        (cover0 > 70 and cover1 > 75) or c.isclose(cover0, cover1, 24)):
                    layers[idx] = [base0, top1, (cover0+cover1)/2]
                    del layers[idx + 1]
                    continue
            else:
                break
            idx += 1

        for layer in layers:
            layer[2] = c.cc2xp(layer[2], layer[0])
        return layers

    @staticmethod
    def isclose(value: float, ref: float, tol: float) -> bool:
        return isclose(value, ref, abs_tol=tol)

    @staticmethod
    def manage_clouds_layers(clouds: list, alt: float, ts: float = False) -> list:
        """choose a max of three layers out of available ones based on flight situation"""

        if c.above_cloud_layers(clouds, alt):
            '''choose overcasted layer if any and the higher ones'''
            if c.is_overcasted(clouds):
                idx, layer = c.get_first_OVC_layer(reversed(clouds))
                if idx > 2:
                    clouds = list(layer).extend(clouds[-2:])
                else:
                    clouds = clouds[clouds.index(layer):]
            else:
                clouds = clouds[-3:]
        else:
            clouds = clouds[:3]

        gfs_limit = c.f2m(5600)
        if ts > 0.5 and len([el for el in clouds if el[2] > 2]) > 1:
            '''With TS active, clouds minimum width is bigger and will move layers upward'''
            layer = next(el for el in reversed(clouds) if el[2] > 2)
            clouds.remove(layer)
        elif len(clouds) < 3 and any(el[1] - el[0] > gfs_limit + 500 for el in clouds):
            '''we can split a gfs cloud layer that is thicker than max XP cloud layer limit'''
            idx, layer = next((i, v) for i, v in enumerate(clouds) if v[1] - v[0] > gfs_limit + 500)
            l1 = [layer[0], layer[0] + gfs_limit, layer[2]]
            l2 = [layer[0] + gfs_limit + 1, layer[1], layer[2]]
            if idx == 0:
                if c.above_cloud_layers(clouds, alt) and layer[2] > 4:
                    clouds[0] = l2  # we don't need the lower slice
                else:
                    if len(clouds) > 1:
                        clouds.append(clouds[-1])
                        clouds[1] = l2
                    else:
                        clouds.append(l2)
                    clouds[0] = l1
            else:
                clouds[1] = l1
                clouds.append(l2)

        return clouds

    @staticmethod
    def evaluate_clouds_redrawing(clouds: list, xp_clouds: list, alt: float) -> bool:
        """returns if clouds layers redraw is necessary: True or False"""
        print(f"evaluate redraw")
        for i, layer in enumerate(xp_clouds):
            if len(clouds) > i:
                base, top, cover = clouds[i]
                distance = abs(base - alt)
                print(f"layer {i}: base {base}, cover {cover}, xp: {layer['bottom'].value}, {layer['coverage'].value}")
                if (not c.isclose(layer['bottom'].value, base, distance * 0.1)
                        or cover != layer['coverage'].value):
                    print(f"Too much Difference in base or cover: REDRAW")
                    return True
                print(f"OK")
            elif layer['coverage'].value > 0:
                print(f"Different layers number: {len(clouds)}, {len(xp_clouds)}, REDRAW")
                return True
        return False


    @staticmethod
    def is_overcasted(clouds: list) -> bool:
        return any(el[2] > 4 for el in clouds)

    @staticmethod
    def get_first_OVC_layer(clouds) -> tuple:
        return next((i, v) for i, v in enumerate(clouds) if v[2] > 4)

    @staticmethod
    def above_cloud_layers(clouds: list, alt: float, xp_clouds: list = None) -> bool:
        max_clouds = max((el[1] for el in clouds if el[2] > 1), default=0)  # do not consider CIRRUS
        if xp_clouds:
            max_xp = max((xp_clouds[i]['top'].value for i in range(3) if xp_clouds[i]['coverage'].value > 1), default=0)
            max_clouds = max(max_clouds, max_xp)
        return False if not len(clouds) else alt > max_clouds + 500

    @staticmethod
    def copy_gfs_clouds(layers: list) -> list:
        """needed to avoid to change original list changing the copy"""
        return [] if not len(layers) else [[e[0], e[1], e[2]] for e in layers if e[0] > 0 and e[1] > 0 and e[2] > 0]

    @staticmethod
    def computeWaterHostilityFactor(lat: float, temp: float) -> float:
        """
        Environmental hostility factor for liquid water.

        - High latitude increases factor
        - Cold temperature increases factor
        - Warm temperature reduces factor
        - Clamped to avoid extreme values

        Typical range:
            warm / low lat  → negative values
            cold / high lat → positive values
        """

        # Base latitude effect (55°N/S is the neutral pivot)
        factor = abs(lat) - 55.0

        # Temperature penalty: warmth reduces hostility
        # (only affects above 0°C)
        factor -= max(0.0, 0.2 * temp)

        # Hard floor to prevent insane negatives
        return max(-20.0, factor)

    @staticmethod
    def computeSnowVal(snow: float, lat: float, temp: float) -> float:
        """
        Compute normalized snow coverage (val) in range [0..1].

        Inputs:
        - snow: snow depth in meters (GFS SNOD)
        - lat: latitude in degrees
        - temp: temperature in °C

        Behavior:
        - Snow depth saturates smoothly (no hard thresholds)
        - Warm / low-lat conditions reduce visible coverage
        """

        if snow <= 0.0:
            return 0.0

        # Smooth saturation of snow depth
        # 0 m → 0.0
        # ~0.1 m → ~0.25
        # ~0.25 m → ~0.35
        # ≥1 m → ~1.0
        snow_norm = 1.0 - exp(-snow * 1.6)

        # Base hostility factor
        factor = c.computeWaterHostilityFactor(lat, temp)

        # Warm & low-lat snow melts / sticks less
        lat_penalty  = max(0.0, (50.0 - abs(lat)) / 20.0)
        temp_penalty = max(0.0, (temp - 5.0) / 20.0)
        warm_penalty = lat_penalty * temp_penalty

        # Bias applied to normalized snow
        factor_bias = (
            0.12 * (factor + 20.0) / 55.0
            - 0.35 * warm_penalty
        )
        factor_bias = min(max(factor_bias, -0.35), 0.15)

        # --- Low-snow visual lift ---
        # Acts only below ~15 cm, fades out smoothly
        low_snow_boost = 0.28 * (1.0 - exp(-12.0 * snow))

        # Final snow coverage value
        val = snow_norm * (0.9 + factor_bias) + low_snow_boost

        return c.clamp01(val)

    @staticmethod
    def computeSurfaceEffects(val: float, factor: float, temp: float) -> tuple[float, float, float, float, float, float]:
        """
        Compute surface contamination effects from snow coverage and climate.
        Tarmac is assumed to be treated and kept clean at low snow coverage.
        Contamination ramps in smoothly only after sufficient accumulation.

        Inputs:
        - val: snow coverage [0..1]
        - factor: environmental hostility factor
        - temp: temperature in °C

        Returns:
        - frozen_water
        - noise
        - scale
        - width
        - ice
        - puddles
        """

        # ------------------------------------------------------------------
        # Snow damping
        # Heavy snow absorbs / smooths surface irregularities
        # ------------------------------------------------------------------
        # Snow damping factor:
        # - val is snow coverage in range [0..1]
        # - produces a smooth exponential attenuation in range (0..1]
        # - no snow (val = 0)    → damping = 1.0   (no effect)
        # - light snow (~0.2)    → damping ≈ 0.63  (moderate reduction)
        # - medium snow (~0.5)   → damping ≈ 0.32  (strong reduction)
        # - heavy snow (≥0.9)    → damping ≈ 0.12  (almost fully damped)
        # Used to progressively suppress frozen-water-related effects as snow cover increases
        snow_damping = exp(-2.3 * val)

        frozen_water = min(
            5.0 * max(0.0, factor) ** 1.5 * snow_damping,
            1000.0
        )

        # Visual noise parameters (legacy-compatible behavior)
        noise = 0.15 - 0.005 * factor
        scale = noise * 2000.0
        width = noise * 3.0

        # ------------------------------------------------------------------
        # ICE / PUDDLES MIX (treated tarmac)
        # ------------------------------------------------------------------

        # Phase: 0 = liquid, 1 = solid
        # +4°C → water
        # -10°C → fully solid
        phase = c.clamp01((4.0 - temp) / 14.0)

        # Extra crystallization in deep cold
        # -10°C -> 0.0
        # -20°C -> 0.5
        # -30°C -> 1.0
        deep_cold = c.clamp01((-10.0 - temp) / 20.0)

        # ------------------------------------------------------------------
        # TARMAC TREATMENT EFFECT
        # Effective when snow coverage is low (kept clean)
        # ------------------------------------------------------------------

        # 1.0 at val = 0.0
        # 0.0 at val >= 0.25
        treatment = 1.0 - c.clamp01(val / 0.25)

        # Ice builds from snow coverage and phase
        ice = phase * (0.35 + 0.65 * val)

        # Treated tarmac removes ice efficiently
        ice *= (1.0 - 0.85 * treatment)

        # Deep cold still causes residual ice
        ice *= (1.0 + 0.4 * deep_cold)

        ice = c.clamp01(ice)

        # Base puddles from melt / water presence
        puddles = val * (1.0 - 0.6 * ice)

        # Cold normally suppresses puddles,
        # but treatment allows water to remain
        cold_suppression = phase * (1.0 - treatment)

        puddles *= (1.0 - cold_suppression)

        puddles = c.clamp01(puddles)

        return frozen_water, noise, scale, width, ice, puddles

    @staticmethod
    def map_friction(x: int | float) -> int:
        """Map friction value and adjust to suitable value
            0-8   -> 6
            9-11  -> 7
            12-13 -> 8
            14+   -> 9
        """
        if x <= 8:
            return 6
        elif x <= 11:
            return 7
        elif x <= 13:
            return 8
        else:
            return 9


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    """
        Smoothstep function
        0 if x <= edge0
        1 if x >= edge1
        smooth interpolation in between
    """
    t = c.clamp01((x - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)
