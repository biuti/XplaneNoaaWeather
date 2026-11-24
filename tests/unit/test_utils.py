from pathlib import Path

from PI_NOAAWeather.scripts import utils


def test_date_in_filename():
    assert utils.date_in_filename(Path("metar-2023-04-30-13.30.txt")) == 202304301330
    assert utils.date_in_filename(Path("nodigits.txt")) == 0


def test_rename_overwrites_destination(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("new"); b.write_text("old")
    utils.rename(a, b)
    assert b.read_text() == "new" and not a.exists()


def test_remove_missing_file_is_fine(tmp_path):
    utils.remove(tmp_path / "nope")


def test_rw_metar_lines_are_ordered_by_icao_then_newest_first(tmp_path):
    f = tmp_path / "metar.txt"
    f.write_text("LIML 241320Z 24008KT\nKJFK 241351Z 31012KT\nLIML 241350Z 24009KT\njunk\n")
    lines = utils.get_rw_ordered_lines(f)
    assert [l[:12] for l in lines] == ["KJFK 241351Z", "LIML 241350Z", "LIML 241320Z"]
