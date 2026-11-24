"""Fake of the XPPython3 API (`xp`, `XPPython3.utils.datarefs`, `XPPython3.utils.commands`).

Only what the plugin calls is implemented. Anything else raises AttributeError on purpose:
a missing fake function is a signal to add it here, not something to hide behind a MagicMock.
"""
import itertools
import sys
import types

# ---- constants: stable distinct ints, created on first use (Font_Basic, WidgetClass_*, ...) ----
_counter = itertools.count(1000)
_constants: dict[str, int] = {}

VERSION = "fake-4.x"
pythonExecutable = sys.executable
PLUGIN_XPLANE = 0
MSG_AIRPORT_LOADED = 104


def __getattr__(name: str):
    # module-level __getattr__: only called for names not defined below
    if name[:1].isupper():
        return _constants.setdefault(name, next(_counter))
    raise AttributeError(f"fake xp has no '{name}': add it to tests/fakes/xp.py")


class Sim:
    """Everything the plugin can observe or leave behind in the 'simulator'."""

    def __init__(self):
        self.system_path = "/tmp/fake-xplane/"
        self.datarefs: dict = {}
        self.commands: dict = {}
        self.menus: dict = {}
        self.widgets: dict = {}
        self.flight_loops: dict = {}
        self.log: list[str] = []
        self.metars: dict[str, str] = {}      # icao -> raw metar for xp.getMETARForAirport
        self._ids = itertools.count(1)
        self.keyboard_focus = None

    def run_flight_loops(self, n: int = 1, dt: float = 1.0) -> None:
        """Run every scheduled flight loop n times, like X-Plane would."""
        for i in range(n):
            for loop in list(self.flight_loops.values()):
                if loop["interval"] != 0:
                    loop["callback"](dt, dt, i, loop["refcon"])


sim = Sim()


# ---- DataRef (XPPython3.utils.datarefs) ----
class DataRef:
    def __init__(self, name: str, default=0.0):
        self.dref = name
        sim.datarefs.setdefault(name, default)

    @property
    def value(self):
        return sim.datarefs[self.dref]

    @value.setter
    def value(self, v):
        sim.datarefs[self.dref] = v


def find_dataref(name: str) -> DataRef:
    return DataRef(name)


def create_command(name, description, callback, **kw):
    sim.commands[name] = callback
    return name


# ---- plain xp.* functions the plugin uses ----
def log(msg): sim.log.append(str(msg))
def getSystemPath(): return sim.system_path
def getVersions(): return 121400, 400, 1
def getMETARForAirport(icao): return sim.metars.get(icao, "")
def unregisterDataAccessor(ref): pass
def getFontDimensions(font): return 8, 12, 0

# flight loops
def createFlightLoop(callback, phase=1, refCon=None):
    lid = next(sim._ids)
    sim.flight_loops[lid] = {"callback": callback, "interval": 0, "refcon": refCon}
    return lid
def scheduleFlightLoop(lid, interval=-1, relativeToNow=1): sim.flight_loops[lid]["interval"] = interval
def destroyFlightLoop(lid): sim.flight_loops.pop(lid, None)

# menus
def createMenu(name, parentMenu=None, parentItem=0, handler=None, refCon=None):
    mid = next(sim._ids); sim.menus[mid] = {"name": name, "items": [], "handler": handler}; return mid
def appendMenuItem(menu, name, refCon=None):
    sim.menus[menu]["items"].append((name, refCon)); return len(sim.menus[menu]["items"]) - 1
def destroyMenu(menu): sim.menus.pop(menu, None)

# widgets: a dict per widget, enough state for the plugin's show/hide/property logic
def createWidget(left, top, right, bottom, visible, descriptor, isRoot, container, widgetClass):
    wid = next(sim._ids)
    sim.widgets[wid] = {"geo": [left, top, right, bottom], "visible": bool(visible), "desc": descriptor,
                        "props": {}, "callbacks": [], "class": widgetClass}
    return wid
def destroyWidget(w, destroyChildren=1): sim.widgets.pop(w, None)
def showWidget(w): sim.widgets[w]["visible"] = True
def hideWidget(w): sim.widgets[w]["visible"] = False
def isWidgetVisible(w): return sim.widgets[w]["visible"]
def setWidgetDescriptor(w, text): sim.widgets[w]["desc"] = text
def getWidgetDescriptor(w, *a): return sim.widgets[w]["desc"]
def setWidgetProperty(w, prop, value): sim.widgets[w]["props"][prop] = value
def getWidgetProperty(w, prop, exists=None): return sim.widgets[w]["props"].get(prop, 0)
def getWidgetGeometry(w): return tuple(sim.widgets[w]["geo"])
def addWidgetCallback(w, cb): sim.widgets[w]["callbacks"].append(cb)
def setKeyboardFocus(w): sim.keyboard_focus = w
def loseKeyboardFocus(w): sim.keyboard_focus = None


def install() -> types.ModuleType:
    """Register this module as `xp` and the XPPython3.utils.* helpers in sys.modules."""
    me = sys.modules[__name__]
    sys.modules["xp"] = me
    pkg = types.ModuleType("XPPython3"); utils = types.ModuleType("XPPython3.utils")
    drefs = types.ModuleType("XPPython3.utils.datarefs"); drefs.DataRef = DataRef; drefs.find_dataref = find_dataref
    cmds = types.ModuleType("XPPython3.utils.commands"); cmds.create_command = create_command
    pkg.utils, utils.datarefs, utils.commands = utils, drefs, cmds
    sys.modules.update({"XPPython3": pkg, "XPPython3.utils": utils,
                        "XPPython3.utils.datarefs": drefs, "XPPython3.utils.commands": cmds})
    return me


def uninstall() -> None:
    for name in ("xp", "XPPython3", "XPPython3.utils", "XPPython3.utils.datarefs", "XPPython3.utils.commands"):
        sys.modules.pop(name, None)
