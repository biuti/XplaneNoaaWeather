"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2021-2026 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

from pathlib import Path


def load_version() -> str:
    """Load the version string from version.txt."""
    version_file = Path(__file__).resolve().parents[1] / 'version.txt'

    try:
        return version_file.read_text(encoding='utf-8').strip()

    except (OSError, UnicodeDecodeError) as e:
        print(f"Unable to load plugin version from {version_file}: {e}")
        return "unknown"


__version__ = load_version()

try:
    import xp
    from XPPython3.utils.commands import create_command
    from XPPython3.utils.datarefs import DataRef, find_dataref
except ImportError:
    xp = None  # weatherserver.py runs as a subprocess, outside X-Plane

# logging needs xp and __version__ from above: keep this after them, before the other imports
from .logger import setup

log = setup()
if xp:
    log.info("XPython3 module correctly imported: %s", xp.VERSION)

from .c import c
from .utils import utils
from .conf import Conf
