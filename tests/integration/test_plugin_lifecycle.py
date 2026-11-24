import pytest

pytestmark = pytest.mark.integration


def make_plugin(env):
    return env.plugin.PythonInterface()


def test_start_enable_disable_stop(plugin_env):
    env = plugin_env
    p = make_plugin(env)

    name, sig, desc = p.XPluginStart()
    assert sig == "noaaweather.xppython3"

    assert p.XPluginEnable() == 1
    assert len(env.sim.flight_loops) == 1

    p.XPluginDisable()
    assert env.sim.flight_loops == {}

    p.XPluginEnable()
    p.XPluginStop()
    assert env.sim.flight_loops == {} and env.sim.menus == {}
    p.XPluginStop()                                  # second stop is a no-op (updater message + X-Plane both call it)


def test_stop_saves_settings(plugin_env):
    p = make_plugin(plugin_env)
    p.XPluginStart(); p.XPluginEnable()
    p.XPluginStop()
    assert p.conf.settings_file.is_file()


def test_menu_and_commands_are_registered(plugin_env):
    p = make_plugin(plugin_env)
    assert [i[0] for i in plugin_env.sim.menus[p.main_menu]["items"]] == ["Weather Info", "Metar Query", "Configuration"]
    assert any(n.endswith("info_window_toggle") for n in plugin_env.sim.commands)


def test_flight_loop_asks_server_for_weather_at_the_aircraft_position(plugin_env):
    env = plugin_env
    p = make_plugin(env)
    p.XPluginStart(); p.XPluginEnable()

    sent = []
    p.weather.weather_client_thread = True                      # pretend the client thread runs
    p.weather.weather_client_send = sent.append
    env.sim.datarefs.update({
        "sim/flightmodel/position/latitude": 45.63,
        "sim/flightmodel/position/longitude": 9.28,
        "sim/flightmodel/position/hpath": 270.4,
        "sim/flightmodel/position/groundspeed": 120.2,
    })

    env.sim.run_flight_loops(n=1, dt=2.0)                       # dt > parse_rate
    assert sent == ["?45.6|9.3|270|120\n"]

    env.sim.run_flight_loops(n=1, dt=2.0)                       # same place, <60 s: no new request
    assert len(sent) == 1


def test_flight_loop_does_nothing_when_plugin_disabled_in_config(plugin_env):
    env = plugin_env
    p = make_plugin(env)
    p.XPluginStart(); p.XPluginEnable()
    p.conf.enabled = False
    p.weather.weather_client_thread = True
    p.weather.weather_client_send = lambda msg: pytest.fail("must not talk to the server")
    env.sim.run_flight_loops(n=3, dt=2.0)


def test_airport_loaded_starts_the_weather_client(plugin_env):
    env = plugin_env
    p = make_plugin(env)
    started = []
    p.weather.start_weather_client = lambda: started.append(True)

    p.XPluginReceiveMessage(0, env.xp.MSG_AIRPORT_LOADED, env.xp.PLUGIN_XPLANE)
    assert started and p.newAptLoaded


def test_info_window_opens_from_the_menu(plugin_env):
    env = plugin_env
    p = make_plugin(env)
    p.main_menu_callback(p.main_menu, 1)
    assert p.info_window and env.xp.isWidgetVisible(p.info_window_widget)
