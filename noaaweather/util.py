"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2023 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path


class util:

    @staticmethod
    def remove(filepath: Path):
        """Remove a file or try to rename-it if it fails"""
        try:
            filepath.unlink(missing_ok=True)
        except Exception as e:
            print(f"can't remove {filepath.name}: {e}")
            i = 1
            while 1:
                npath = Path(f"{filepath}-{i}")
                if not npath.exists():
                    try:
                        filepath.rename(npath)
                    except Exception as e:
                        print(f"can't rename {filepath.name}: {e}")
                        if sys.platform == 'win32':
                            import ctypes
                            print(f"{filepath.name} marked for deletion on reboot.")
                            ctypes.windll.kernel32.MoveFileExA(str(filepath), None, 4)
                    break
                i += 1

    @staticmethod
    def rename(opath: Path, dpath: Path):
        if dpath.exists():
            util.remove(dpath)
        try:
            opath.rename(dpath)
        except OSError:
            print(f"Can't rename: {opath.name} to {dpath.name}, trying to copy/remove")
            util.copy(opath, dpath)
            util.remove(opath)

    @staticmethod
    def copy(opath: Path, dpath: Path):
        if dpath.exists():
            util.remove(dpath)
        try:
            shutil.copyfile(opath, dpath)
        except OSError as e:
            print(f"Can't copy {opath.name} to {dpath.name}: {e}")

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

    @staticmethod
    def split_text(text: str, indent: int = 0, max_len: int = 80) -> list:
        if len(text) + indent > max_len:
            icut = text.rfind(' ', 0, max_len - indent)
            return [indent * ' ' + text[:icut], indent * ' ' + 3 * ' ' + text[icut + 1:]]
        else:
            return [indent * ' ' + text]

    @staticmethod
    def date_in_filename(file: Path) -> int:
        """ metar-2023-04-30-13.30.txt -> 202304301330"""
        return int(''.join([s for s in file.stem.replace('-', '.').split('.') if s.isdigit()])) or 0
