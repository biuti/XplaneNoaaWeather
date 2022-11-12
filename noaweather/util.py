"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2022 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path


class util:

    @staticmethod
    def remove(filepath):
        """Remove a file or try to rename-it if it fails"""
        try:
            os.remove(filepath)
        except:
            print(f"can't remove {filepath}")
            i = 1
            while 1:
                npath = f"{filepath}-{i}"
                if not os.path.exists(npath):
                    try:
                        os.rename(filepath, npath)
                    except:
                        print(f"can't rename {filepath}")
                        if sys.platform == 'win32':
                            import ctypes
                            print(f"{filepath} marked for deletion on reboot.")
                            ctypes.windll.kernel32.MoveFileExA(filepath, None, 4)
                    break
                i += 1

    @staticmethod
    def rename(opath, dpath):
        if os.path.exists(dpath):
            util.remove(dpath)
        try:
            os.rename(opath, dpath)
        except OSError:
            print(f"Can't rename: {opath} to {dpath}, trying to copy/remove")
            util.copy(opath, dpath)
            util.remove(opath)

    @staticmethod
    def copy(opath, dpath):
        if os.path.exists(dpath):
            util.remove(dpath)
        try:
            shutil.copyfile(opath, dpath)
        except:
            print(f"Can't copy {opath} to {dpath}")

    @staticmethod
    def date_info():
        today_prefix = datetime.utcnow().strftime('%Y%m')
        yesterday_prefix = (datetime.utcnow() + timedelta(days=-1)).strftime('%Y%m')

        today = datetime.utcnow().strftime('%d')
        return today, today_prefix, yesterday_prefix

    @staticmethod
    def get_rw_ordered_lines(metar_file: Path) -> list:
        """ Get list of METARs from XP12 real Weather metar file,
            ordered by ICAO, time desc"""

        lines = [x for x in (set(open(metar_file, encoding='utf-8', errors='replace')))
                 if x[0].isalpha() and len(x) > 11 and x[11] == 'Z']
        lines.sort(key=lambda x: (x[0:4], -int(x[5:10])))
        return lines
