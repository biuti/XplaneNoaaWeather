"""Pure calculation helpers: no X-Plane, no files, no network."""
import pytest

from PI_NOAAWeather.scripts import c


@pytest.mark.parametrize("feet, meters", [(0, 0), (1000, 304.8), (36000, 10972.8)])
def test_f2m(feet, meters):
    assert c.f2m(feet) == pytest.approx(meters)


def test_m2ft_roundtrip():
    assert c.m2ft(c.f2m(12345)) == pytest.approx(12345)


def test_ms2knots():
    assert c.ms2knots(10) == pytest.approx(19.4384)


@pytest.mark.parametrize("value, expected", [(9.999e20, True), (1.0, False), (0.0, False)])
def test_is_exponential_detects_gfs_missing_value(value, expected):
    assert c.is_exponential(value) is expected


def test_great_circle_one_degree_of_latitude():
    assert c.greatCircleDistance((45.0, 9.0), (46.0, 9.0)) == pytest.approx(111_319, rel=1e-3)


def test_to_float_falls_back_to_default():
    assert c.toFloat("not a number", 3) == 3
