"""
The weather server is a subprocess of X-Plane, where nobody sees its stderr: a broken import, a renamed
script or a missing name and it dies silently. These tests run the real weatherserver.py inside the
fake X-Plane folder, and a failure prints what X-Plane would never show: the server's own stderr and log.
"""
import json
import pickle
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "PI_NOAAWeather"
STARTUP_TIMEOUT = 20   # seconds
EXIT_TIMEOUT = 10

# Runs in every subprocess of the test before its own imports. The server lives outside X-Plane, so
# `import xp` must fail whatever is installed in the venv, and (the autouse no_network fixture can't
# reach into a subprocess) no download may leave the machine.
SITECUSTOMIZE = """
import sys
import urllib.error
import urllib.request

sys.modules['xp'] = None


def offline(*args, **kwargs):
    raise urllib.error.URLError('offline (tests)')


urllib.request.urlopen = offline
"""


@pytest.fixture
def server_conf(xplane_root, tmp_path, monkeypatch):
    """Conf of the fake X-Plane, ready to run the real server: scripts linked in next to bin, a free port
    saved in its settings (never the real plugin's 8950), and the sitecustomize above for every subprocess"""
    from PI_NOAAWeather.scripts import Conf

    plugin = xplane_root / "Resources" / "plugins" / "PythonPlugins" / "PI_NOAAWeather"
    (plugin / "scripts").symlink_to(PLUGIN_DIR / "scripts", target_is_directory=True)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (plugin / "settings.pkl").write_bytes(pickle.dumps({"version": "12.4.4", "server_port": port}))

    offline = tmp_path / "offline"
    offline.mkdir()
    (offline / "sitecustomize.py").write_text(SITECUSTOMIZE)
    monkeypatch.setenv("PYTHONPATH", str(offline))

    return Conf(xplane_root)


def read_log(conf) -> str:
    return conf.server_log_file.read_text(errors="replace") if conf.server_log_file.is_file() else ""


def send(conf, message: str, timeout: float = 1.0):
    """One UDP request like the plugin's client: the decoded JSON reply, None if nobody answers"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        try:
            s.sendto(message.encode(), ("127.0.0.1", conf.server_port))
            return json.loads(s.recvfrom(65535)[0].decode())
        except OSError:   # timeout, or connection refused while the port is not open yet
            return None


def wait_ready(conf, process, report=lambda: "") -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"the weather server died at startup (returncode {process.returncode})\n{report()}")
        if send(conf, "!ping", timeout=0.3) == "!pong":
            return
        time.sleep(0.1)
    pytest.fail(f"the weather server did not answer within {STARTUP_TIMEOUT} s\n{report()}")


def wait_exit(process, report=lambda: "") -> int:
    try:
        return process.wait(EXIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        pytest.fail(f"the weather server is still running {EXIT_TIMEOUT} s later\n{report()}")


@pytest.fixture
def server(server_conf):
    """The real weatherserver.py, started with the arguments Weather.start_weather_server() uses, and answering"""
    with open(server_conf.plugin_path / "server-output.txt", "w+") as output:   # stdout and stderr
        process = subprocess.Popen(
            [sys.executable, Path(server_conf.scripts_path, "weatherserver.py"), server_conf.xplane_path, "--parent-pipe"],
            stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, close_fds=True,
        )

        def report() -> str:
            output.flush()
            output.seek(0)
            return f"--- stdout/stderr ---\n{output.read()}\n--- weather_server_log.txt ---\n{read_log(server_conf)}"

        try:
            wait_ready(server_conf, process, report)
            yield SimpleNamespace(conf=server_conf, process=process, report=report)
        finally:
            if process.poll() is None:   # never leave a server behind holding a port
                process.kill()
                process.wait()
            process.stdin.close()


def test_server_starts_and_answers(server):
    assert send(server.conf, "!ping") == "!pong"


def test_plugin_shutdown_sequence(server):
    """What Weather.shutdown() does: '!shutdown' over UDP, then the pipe is closed"""
    assert send(server.conf, "!shutdown") == "!bye"
    server.process.stdin.close()

    assert wait_exit(server.process, server.report) == 0
    log = read_log(server.conf)
    assert "Weather server started." in log and "Weather server stopped." in log

    server.conf.load_server_settings()
    assert server.conf.weather_server_pid == server.process.pid   # saved for a later takeover of the port


def test_server_exits_when_the_parent_pipe_closes(server):
    """X-Plane exits or crashes: the pipe closes, and the server must not stay behind"""
    server.process.stdin.close()

    assert wait_exit(server.process, server.report) == 0
    assert "Weather server stopped." in read_log(server.conf)


def test_weather_starts_the_server_subprocess(plugin_env, server_conf, monkeypatch):
    """Weather.__init__ launches the server: the script it names must exist and must start"""
    env = plugin_env
    monkeypatch.setattr(env.weather.Weather, "start_weather_server", env.start_weather_server)   # plugin_env turns it off

    p = env.plugin.PythonInterface()
    process = p.weather.server_process
    try:
        assert process is not None, "start_weather_server() did not create the process"
        wait_ready(server_conf, process, lambda: read_log(server_conf))
    finally:
        p.weather.shutdown()   # closes the pipe, as on a normal X-Plane exit
        try:
            process.wait(EXIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            pytest.fail(f"the weather server did not stop within {EXIT_TIMEOUT} s after Weather.shutdown()")
