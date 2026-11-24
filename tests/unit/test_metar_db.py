"""METAR download -> parse -> SQLite -> query, served from tests/data. No network, no X-Plane."""
import shutil


def load(metar, tmp_path, data):
    stations = tmp_path / "stations.json"
    reports = tmp_path / "metars.csv"
    shutil.copy(data / "stations.json", stations)
    shutil.copy(data / "metars.cache.csv", reports)
    return metar.update_stations(stations), metar.update_metar(reports)


def test_stations_are_filtered_and_stored(metar, tmp_path, metar_data):
    n_stations, _ = load(metar, tmp_path, metar_data)
    assert n_stations == 4                     # 12AB, TOOLONG and null are not real ICAO ids


def test_reports_update_known_stations_only(metar, tmp_path, metar_data):
    _, (updated, parsed) = load(metar, tmp_path, metar_data)
    assert updated == 3                        # LIML (newest wins), KJFK, EGLL
    assert parsed >= 4                         # header junk is tolerated, not counted as failure


def test_newest_report_wins_over_yesterdays(metar, tmp_path, metar_data):
    load(metar, tmp_path, metar_data)
    text = metar.get_metar("LIML")[-1]            # row: icao, lat, lon, elevation, timestamp, metar
    assert "24008G15KT" in text and "CAVOK" not in text


def test_closest_station_with_a_report(metar, tmp_path, metar_data):
    load(metar, tmp_path, metar_data)
    # near Malpensa (LIMC) which has no report: the closest *with* a report is LIML
    assert metar.get_closest_station(45.63, 8.73)[0] == "LIML"


def test_ignore_auto_and_ignored_stations(metar, tmp_path, metar_data):
    load(metar, tmp_path, metar_data)
    metar.conf.metar_ignore_auto = True
    assert metar.get_closest_station(40.6, -73.7) [0] != "KJFK"      # KJFK report is AUTO
    metar.conf.metar_ignore_auto = False
    metar.conf.ignore_metar_stations = ["LIML"]
    assert metar.get_closest_station(45.4, 9.3)[0] != "LIML"


def test_full_cycle_through_the_async_downloader(metar, tmp_path, metar_data):
    """run() -> download_cycle() -> AsyncTask(GribDownloader.download) -> update_metar(): the real code path,
    with only the HTTP part replaced by a file copy."""
    shutil.copy(metar_data / "stations.json", tmp_path / "s.json")
    metar.update_stations(tmp_path / "s.json")
    metar.last_timestamp = 0

    metar.run(0)                       # starts the download thread
    metar.download.join(5)
    metar.run(0)                       # sees the finished download and imports it

    assert metar.get_metar("KJFK")[-1].startswith("151351Z AUTO 31012KT")
