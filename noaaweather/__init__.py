"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2021-2023 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

try:
    import xp
    print(f"XPython3 module correctly imported: {xp.VERSION}")
except ImportError as e:
    print(f"error import xp: {e}")
    pass

# try:
#     import XPPython3.xp as xp
#     xp.log(f"test xp: {xp}")
# except (ImportError, Exception) as e:
#     print(f"** ** error import xp: {e}")
#     pass

# try:
#     xp.log(f"test xp: {xp.VERSION}")
# except Exception as e:
#     print(f"** ** error xp: {e}")
#     pass

from .c import c
from .util import util
from .conf import Conf
# from .gfs import GFS
from .metar import Metar
# from .wafs import WAFS
# from .data import Data
# from .weather import Weather
from .realweather import RealWeather
from .weathersource import WeatherSource

try:
    from .easydref import EasyDref
    from .easydref import EasyCommand
except ImportError:
    pass
