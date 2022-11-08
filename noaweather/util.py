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
