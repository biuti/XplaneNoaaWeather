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
    xp.log(f"XPython3 module correctly imported: {xp.VERSION}")
except ImportError as e:
    print(f"error import xp: {e}")
    pass

from .c import c
from .util import util
from .conf import Conf
