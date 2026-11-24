import pickle

import pytest

from PI_NOAAWeather.scripts import Conf


def test_conf_stays_inside_the_fake_xplane_folder(conf, xplane_root):
    assert xplane_root in conf.cache_path.parents
    assert conf.db_file.parent.is_dir()


def test_defaults(conf):
    assert conf.enabled is True
    assert conf.use_real_weather_data is True
    assert conf.server_port == 8950


@pytest.mark.parametrize("raw, expected", [
    ("12.4.4", (12, 4, 4)),
    ("12.4.4-beta.3", (12, 4, 4)),
    ("3.5", (3, 5, 0)),
    ("", (0, 0, 0)),
])
def test_version_tuple(raw, expected):
    assert Conf.version_tuple(raw) == expected
    assert Conf.version_tuple("3.5") < Conf.version_tuple("12.0")      # the reason it is not a string compare


def test_settings_roundtrip(xplane_root):
    first = Conf(xplane_root)
    first.metar_decode = True
    first.metar_source = "VATSIM"
    first.save_plugin_settings()

    second = Conf(xplane_root)
    assert second.metar_decode is True
    assert second.metar_source == "VATSIM"
    assert second.metar_updaterate == 10            # derived from the source on load


def test_corrupted_settings_file_is_deleted_and_defaults_kept(xplane_root):
    first = Conf(xplane_root)
    first.settings_file.write_bytes(b"this is not a pickle")

    second = Conf(xplane_root)
    assert second.enabled is True
    assert not second.settings_file.exists()


def test_settings_from_a_very_old_version_are_ignored(xplane_root):
    first = Conf(xplane_root)
    first.settings_file.write_bytes(pickle.dumps({"version": "11.9.0", "metar_decode": True}))
    assert Conf(xplane_root).metar_decode is False


def test_missing_gfs_levels_file_is_created(conf):
    assert conf.gfs_levels_file.is_file()
    assert conf.gfs_levels == Conf.gfs_levels_defaults()
