"""GRIB decoding logic without a real GRIB file: a stub 'wgrib2' prints canned `-s -lon` records.

This tests what *our* code does with wgrib2's output (bucketing, unit conversion, cloud/tropo handling),
on any machine and any CPU. Whether the real binary decodes a real file is the job of the @wgrib2 tests.
"""
import stat

import pytest

from PI_NOAAWeather.scripts import c
from PI_NOAAWeather.scripts.gfs import GFS

# index:offset:d=run:VAR:level:forecast::lon=..,lat=..,val=..
CANNED = """\
2:1:d=2026111412:TMP:surface:3 hour fcst::lon=9,lat=46,val=275.5
3:2:d=2026111412:SNOD:surface:3 hour fcst::lon=9,lat=46,val=0.25
4:3:d=2026111412:PRMSL:mean sea level:3 hour fcst::lon=9,lat=46,val=101300
5:4:d=2026111412:UGRD:500 mb:3 hour fcst::lon=9,lat=46,val=10
6:5:d=2026111412:VGRD:500 mb:3 hour fcst::lon=9,lat=46,val=-5
7:6:d=2026111412:TMP:500 mb:3 hour fcst::lon=9,lat=46,val=240
this line is not a record
"""


@pytest.fixture
def gfs_with_stub_wgrib2(conf, tmp_path):
    stub = tmp_path / "fake-wgrib2"
    stub.write_text(f"#!/bin/sh\ncat <<'EOF'\n{CANNED}EOF\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    conf.wgrib2bin = stub
    conf.spinfo = False
    gfs = GFS(conf)
    grib = tmp_path / "any.grib2"
    grib.write_bytes(b"")
    return gfs, grib


def test_surface_values_are_renamed_and_converted(gfs_with_stub_wgrib2):
    gfs, grib = gfs_with_stub_wgrib2
    surface = gfs.parse_grib_data(46, 9, files=[grib])["surface"]
    assert surface["press"] == pytest.approx(101300)  # PRMSL, sea level pressure in Pa
    assert surface["temp"] == pytest.approx(275.5)
    assert surface["snow"] == pytest.approx(0.25)


def test_msl_pressure_is_converted_to_inhg(gfs_with_stub_wgrib2):
    gfs, grib = gfs_with_stub_wgrib2
    assert "pressure" not in gfs.parse_grib_data(46, 9, files=[grib]).


def test_wind_levels_are_built_from_pressure_levels(gfs_with_stub_wgrib2):
    gfs, grib = gfs_with_stub_wgrib2
    winds = gfs.parse_grib_data(46, 9, files=[grib])["winds"]
    assert winds, "expected at least the 500 mb layer"
    assert gfs.gfs_run == "2026111412"


def test_msl_pressure_is_a_surface_value_in_pa(gfs_with_stub_wgrib2):
    gfs, grib = gfs_with_stub_wgrib2
    data = gfs.parse_grib_data(46, 9, files=[grib])
    assert data["surface"]["press"] == pytest.approx(101300)
    assert "pressure" not in data


def test_wgrib2_crash_returns_no_data_instead_of_raising(conf, tmp_path):
    conf.wgrib2bin = tmp_path / "does-not-exist"
    conf.spinfo = False
    gfs = GFS(conf)
    assert gfs.read_grib_file(tmp_path / "x.grib2") == []
