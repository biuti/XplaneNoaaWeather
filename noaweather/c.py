"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2012-2020 Joan Perez i Cauhe
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

from math import hypot, atan2, degrees, exp, log, radians, sin, cos, sqrt, pi, isclose
from random import random


class c:
    """Unit conversion  and misc tools"""
    # transition references
    transrefs = {}
    randRefs = {}

    @staticmethod
    def ms2knots(val):
        return val * 1.94384

    @staticmethod
    def kel2cel(val):
        return val - 273.15

    @staticmethod
    def c2p(x, y):
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
    def mb2alt(mb):
        altpress = (1 - (mb / 1013.25) ** 0.190284) * 44307
        return altpress

    @staticmethod
    def oat2msltemp(oat, alt, tropo_temp=-56.5, tropo_alt=11000):
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
    def greatCircleDistance(latlong_a, latlong_b):
        """Return the great circle distance of 2 coordinatee pairs"""
        EARTH_RADIUS = 6378137

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
    def interpolate(t1, t2, alt1, alt2, alt):
        if (alt2 - alt1) == 0:
            return t2
        return t1 + (alt - alt1) * (t2 - t1) / (alt2 - alt1)

    @staticmethod
    def expoCosineInterpolate(t1, t2, alt1, alt2, alt, expo=3):
        if alt1 == alt2: return t1
        x = (alt - alt1) / float(alt2 - alt1)
        return t1 + (t2 - t1) * x ** expo

    @staticmethod
    def cosineInterpolate(t1, t2, alt1, alt2, alt):
        if alt1 == alt2: return t1
        x = (alt - alt1) / float(alt2 - alt1)
        return t1 + (t2 - t1) * (0.5 - cos(pi * x) / 2)

    @staticmethod
    def cosineInterpolateHeading(hdg1, hdg2, alt1, alt2, alt):

        if alt1 == alt2: return hdg1

        t2 = c.shortHdg(hdg1, hdg2)
        t2 = c.cosineInterpolate(0, t2, alt1, alt2, alt)
        t2 += hdg1

        if t2 < 0:
            return t2 + 360
        else:
            return t2 % 360

    @staticmethod
    def expoCosineInterpolateHeading(hdg1, hdg2, alt1, alt2, alt, expo=3):

        if alt1 == alt2: return hdg1

        t2 = c.shortHdg(hdg1, hdg2)
        t2 = c.expoCosineInterpolate(0, t2, alt1, alt2, alt, expo)
        t2 += hdg1

        if t2 < 0:
            return t2 + 360
        else:
            return t2 % 360

    @staticmethod
    def interpolateHeading(hdg1, hdg2, alt1, alt2, alt):
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
    def fog2(rh):
        return (80 - rh) / 20 * 24634

    @staticmethod
    def isaDev(alt, temp):
        """Calculates Temperature ISA Deviation"""
        isa = 15 - 0.65*alt/100
        return temp - isa

    @staticmethod
    def toFloat(string, default=0):
        """Convert to float or return default"""
        try:
            val = float(string)
        except ValueError:
            val = default
        return val

    @staticmethod
    def toInt(string, default=0):
        """Convert to float or return default"""
        try:
            val = int(string)
        except ValueError:
            val = default
        return val

    @staticmethod
    def rh2visibility(rh):
        # http://journals.ametsoc.org/doi/pdf/10.1175/2009JAMC1927.1
        return 1000 * (-5.19 * 10 ** -10 * rh ** 5.44 + 40.10)

    @staticmethod
    def dewpoint2rh(temp, dew):
        return 100 * (exp((17.625 * dew) / (243.04 + dew)) / exp((17.625 * temp) / (243.04 + temp)))

    @staticmethod
    def dewpoint(temp, rh):
        return 243.04 * (log(rh / 100) + ((17.625 * temp) / (243.04 + temp))) / (
                    17.625 - log(rh / 100) - ((17.625 * temp) / (243.04 + temp)))

    @staticmethod
    def shortHdg(a, b):
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
    def pa2inhg(pa):
        return pa * 0.0002952998016471232

    @classmethod
    def datarefTransition(cls, dataref, new, elapsed, speed=0.25, id=False):
        """Timed dataref transition"""

        # Save reference to ignore x-plane roundings
        if not id:
            id = str(dataref.DataRef)
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
    def transition(cls, new, id, elapsed, speed=0.25):
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
    def transitionClearReferences(cls, refs=False, exclude=False):
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
    def transitionHdg(cls, new, id, elapsed, speed=0.25):
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
    def datarefTransitionHdg(cls, dataref, new, elapsed, vel=1):
        """Time based wind heading transition"""
        id = str(dataref.DataRef)
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
    def float_or_lower(string: str) -> float or str:
        el = string.rsplit('.')
        try:
            return float('.'.join(el[:2]))
        except ValueError:
            try:
                return float(el[0])
            except ValueError:
                return string.lower()

    @staticmethod
    def limit(value, max=None, min=None):
        if max is not False and max is not None and value > max:
            return max
        elif min is not False and min is not None and value < min:
            return min
        else:
            return value

    @staticmethod
    def cc2xp_old(cover):
        # Cloud cover to X-plane
        xp = int(cover / 100.0 * 4)
        if xp < 1 and cover > 0:
            xp = 1
        elif cover > 89:
            xp = 4
        return xp

    @staticmethod
    def cc2xp(cover, base):
        # GFS Percent cover to XP
        if base > 6500:
            if cover < 30:
                return 1  # 'CIRRUS
            elif cover < 55:
                return 2  # CIRRUSTRATUS
            else:
                return 3
        elif cover == 0:
            return 0
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
    def metar2xpprecipitation(kind, intensity, mod, recent):
        """Return intensity of a metar precipitation"""

        ints = {'-': 0, '': 1, '+': 2}
        intensity = ints[intensity]

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
            precipitation = precip[kind][intensity]
        if recent or intensity == 0:
            patchy = 1
        if kind in wet:
            friction = wet[kind]

        return precipitation, friction, patchy

    @staticmethod
    def strFloat(i, false_label='na'):
        """Print a float or na if False"""
        if i is False:
            return false_label
        else:
            return '%.2f' % (i)

    @staticmethod
    def m2ft(n):
        if n is False: return False
        return n * 3.280839895013123

    @staticmethod
    def f2m(n):
        if n is False: return False
        return n * 0.3048

    @staticmethod
    def sm2m(n):
        if n is False: return False
        return n * 1609.344

    @staticmethod
    def m2sm(n):
        if n is False:
            return False
        return n * 0.0006213711922373339

    @staticmethod
    def m2kn(n):
        return n * 1852

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
    def convertFromInput(cls, string, conversion, default=False, toFloat=False, max=False, min=False):
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
    def randPattern(cls, id, max_val, elapsed, max_time=1, min_val=0, min_time=1, heading=False):
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
    def middleHeading(hd1, hd2):
        if hd2 > hd1:
            return hd1 + (hd2 - hd1) / 2
        else:
            return hd2 + (360 + hd1 - hd2) / 2

    @staticmethod
    def gfs_levels_help_list():
        """Returns a text list of FL levels with corresponding pressure in millibars"""
        return ["FL%03d %i mb" % (int(c.m2ft(c.mb2alt(i)))/100, i) for i in reversed(range(100, 1050, 50))]

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
    def isclose(value, ref, tol) -> bool:
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
