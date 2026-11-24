"""Metar.parse_metar is a classmethod: parse a string, get a dict. No instance, no network."""
import pytest

from PI_NOAAWeather.scripts.metar import Metar


def parse(raw: str) -> dict:
    return Metar.parse_metar(raw[:4], raw)


def test_wind_with_gust_clouds_and_temperature():
    m = parse("LIML 241320Z 24008G15KT 9999 FEW040 BKN070 12/05 Q1018")
    assert m["wind"][:2] == [240, 8]
    assert m["temperature"] == [12, 5]
    assert [layer[1] for layer in m["clouds"]] == ["FEW", "BKN"]
    assert m["clouds"][0][0] == pytest.approx(1219.2)       # FEW040 -> 4000 ft in meters


def test_variable_wind_and_negative_temperatures():
    m = parse("LIML 241320Z VRB02KT CAVOK 08/M02 Q0998")
    assert m["variable_wind"] == [0, 360]
    assert m["temperature"] == [8, -2]
    assert m["clouds"] == []


def test_pressure_units_are_normalised_to_inhg():
    q = parse("LIML 241320Z 24008KT 9999 12/05 Q1013")["pressure"]
    a = parse("KJFK 241351Z 31012KT 10SM 05/M03 A2992")["pressure"]
    assert q == pytest.approx(29.92, abs=0.01)
    assert a == pytest.approx(29.92, abs=0.01)


def test_recent_rain_is_decoded():
    m = parse("LIML 241320Z 24008KT 9999 FEW040 12/05 Q1018 RERA")
    assert m["precipitation"]["RA"]["recent"] == "RE"


@pytest.mark.parametrize("garbage", ["", "LIML", "LIML 241320Z", "\x00\x01 not a metar"])
def test_malformed_input_does_not_crash(garbage):
    # characterisation test: pin down what *currently* happens with junk, so a regression is visible
    result = Metar.parse_metar("LIML", garbage)
    assert isinstance(result, dict)
