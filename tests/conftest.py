import platform
import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "PI_NOAAWeather"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any test that tries to reach a real host fails loudly instead of hitting NOAA/VATSIM/IVAO."""
    real = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        if host not in ("localhost", "127.0.0.1", "::1", None):
            raise AssertionError(f"test tried to reach the network: {host}")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)


@pytest.fixture
def xplane_root(tmp_path) -> Path:
    """A throw-away 'X-Plane folder'. Conf writes cache/settings/db under it, never in your real install.
    The real wgrib2 binaries are symlinked in, so wgrib2 tests can run."""
    root = tmp_path / "X-Plane 12"
    plugin = root / "Resources" / "plugins" / "PythonPlugins" / "PI_NOAAWeather"
    plugin.mkdir(parents=True)
    (plugin / "bin").symlink_to(PLUGIN_DIR / "bin", target_is_directory=True)
    (root / "Output" / "real weather").mkdir(parents=True)
    (root / "Output" / "preferences").mkdir(parents=True)
    return root


@pytest.fixture
def conf(xplane_root):
    from PI_NOAAWeather.scripts import Conf
    return Conf(xplane_root)


@pytest.fixture
def wgrib2(conf):
    """Skip unless the bundled wgrib2 for THIS machine exists and actually runs."""
    import subprocess
    if not conf.meets_wgrib2_requirements or not conf.wgrib2bin.is_file():
        pytest.skip(f"no wgrib2 binary for {platform.system()}/{platform.machine()}")
    try:
        subprocess.run([str(conf.wgrib2bin), "-version"], capture_output=True, timeout=10)
    except OSError as e:
        pytest.skip(f"wgrib2 does not run here: {e}")
    return conf.wgrib2bin


# ---------- METAR: static data, no network ----------
import shutil
import time

DATA = Path(__file__).resolve().parent / "data"


@pytest.fixture
def metar(conf, monkeypatch):
    """A Metar source whose downloads are served from tests/data, with 'today' pinned to 2026-11-15
    (the day used inside tests/data/metars.cache.csv)."""
    from PI_NOAAWeather.scripts import utils
    from PI_NOAAWeather.scripts.metar import Metar
    from PI_NOAAWeather.scripts.weathersource import GribDownloader

    monkeypatch.setattr(utils, "date_info", staticmethod(lambda: ("15", "202611", "202610")))

    served = {"metars": "metars.cache.csv", "stations": "stations.json"}

    def fake_download(url, file_path, binary=False, **kwargs):
        name = served["stations"] if "stations" in str(url) else served["metars"]
        shutil.copyfile(DATA / name, file_path)
        return file_path

    monkeypatch.setattr(GribDownloader, "download", staticmethod(fake_download))

    conf.ms_update = time.time()            # "stations are fresh": no download in __init__
    conf.keepOldFiles = True
    source = Metar(conf)
    yield source
    source.shutdown()


@pytest.fixture
def metar_data():
    """Copies of the static files in a writable place (update_metar() deletes its input unless keepOldFiles)."""
    return DATA
