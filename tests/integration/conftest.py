"""Fixtures that load the plugin *inside* the fake X-Plane."""
import importlib
import sys
from types import SimpleNamespace

import pytest

from fakes import xp as fake_xp

PKG = "PI_NOAAWeather"


def _is_plugin_module(name: str) -> bool:
    return name == PKG or name.startswith(PKG + ".")


@pytest.fixture
def plugin_env(xplane_root, monkeypatch):
    """Import PI_NOAAWeather with a fake `xp` present, exactly as XPPython3 would.

    scripts/__init__.py decides at import time whether xp exists (xp = None otherwise),
    and widget.Widget calls xp.getFontDimensions in its class body. So the plugin modules
    must be imported *after* the fake is installed, and thrown away afterwards so the
    xp-less modules used by the unit tests are untouched.
    """
    saved = {k: v for k, v in sys.modules.items() if _is_plugin_module(k)}
    for k in saved:
        del sys.modules[k]

    fake_xp.sim.__init__()                       # fresh simulator state
    fake_xp.sim.system_path = str(xplane_root) + "/"
    fake_xp.install()

    plugin = importlib.import_module(f"{PKG}.{PKG}")
    scripts_weather = importlib.import_module(f"{PKG}.scripts.weather")

    # never spawn the real weatherserver.py subprocess from a test (test_weather_server.py puts it back on purpose)
    real_start_weather_server = scripts_weather.Weather.start_weather_server
    monkeypatch.setattr(scripts_weather.Weather, "start_weather_server", lambda self: None)

    yield SimpleNamespace(
        plugin=plugin,
        sim=fake_xp.sim,
        xp=fake_xp,
        root=xplane_root,
        weather=scripts_weather,
        start_weather_server=real_start_weather_server
    )

    fake_xp.uninstall()
    for k in [k for k in sys.modules if _is_plugin_module(k)]:
        del sys.modules[k]
    sys.modules.update(saved)
