#!/usr/bin/env python3
"""Generate the SkunkCrafts Updater depot files for PI_NOAAWeather.

The depot is the PI_NOAAWeather/ folder itself, served by raw.githubusercontent.com from a branch:
    release channel : .../xp12-master/PI_NOAAWeather
    beta channel    : .../xp12-dev/PI_NOAAWeather
so the only things to generate are the cfg and the three lists, then commit them with the code.

    python tools/make_depot.py           # (re)write the files
    python tools/make_depot.py --check   # exit 1 if they are out of date (CI / pre-push)

Published files = the files tracked by git inside PI_NOAAWeather/ (so .gitignore decides what is
excluded: cache/, *.pkl, settings...). Never touched by the updater: anything not listed.
"""
import subprocess
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PRODUCT = REPO / "PI_NOAAWeather"

NAME = "NOAAWeather"
# ALWAYS the production depot, whatever branch you are on (PUBLISHING.md 1.6): the cfg is copied to users.
MODULE = "https://raw.githubusercontent.com/biuti/XplaneNoaaWeather/xp12-master/PI_NOAAWeather"
# Beta channel: same cfg, different branch (the docstring's "beta channel").
BETA_MODULE = "https://raw.githubusercontent.com/biuti/XplaneNoaaWeather/xp12-dev/PI_NOAAWeather"

CFG = PRODUCT / "skunkcrafts_updater.cfg"
WHITELIST = PRODUCT / "skunkcrafts_updater_whitelist.txt"
SIZES = PRODUCT / "skunkcrafts_updater_sizeslist.txt"
BLACKLIST = PRODUCT / "skunkcrafts_updater_blacklist.txt"
BLACKLIST_SOURCE = REPO / "tools" / "depot_blacklist.txt"      # hand-maintained: paths deleted on users' disks
GENERATED = {CFG, WHITELIST, SIZES, BLACKLIST}


def published_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z", "--", str(PRODUCT)], cwd=REPO, check=True,
                         capture_output=True, text=True).stdout
    files = [REPO / p for p in out.split("\0") if p]
    return sorted(f for f in files
                  if f not in GENERATED
                  and not any(part.startswith(".") for part in f.relative_to(PRODUCT).parts))


def render() -> dict[Path, str]:
    version = (PRODUCT / "version.txt").read_text(encoding="utf-8").strip()
    white, sizes = [], []
    for f in published_files():
        rel = f.relative_to(PRODUCT).as_posix()
        data = f.read_bytes()
        white.append(f"{rel}|{zlib.crc32(data) & 0xFFFFFFFF}")
        sizes.append(f"{rel}|{len(data)}")
    black = BLACKLIST_SOURCE.read_text(encoding="utf-8").splitlines() if BLACKLIST_SOURCE.exists() else []
    cfg = (
        f"name|{NAME}\n"
        f"module|{MODULE}\n"
        f"version|{version}\n"
        f"locked|false\n"
        f"zone|custom\n"
        f"channel.Beta|{BETA_MODULE}\n"
    )
    nl = lambda lines: "".join(f"{line}\n" for line in lines if line.strip())
    return {CFG: cfg, WHITELIST: nl(white), SIZES: nl(sizes), BLACKLIST: nl(black)}


def main() -> int:
    wanted = render()
    stale = [p for p, text in wanted.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    if "--check" in sys.argv:
        for p in stale:
            print(f"out of date: {p.relative_to(REPO)}")
        return 1 if stale else 0
    for p, text in wanted.items():
        p.write_text(text, encoding="utf-8", newline="\n")
    print(f"{len(published_files())} files, version {wanted[CFG].splitlines()[2].split('|')[1]}; "
          f"{len(stale)} of 4 generated files changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
