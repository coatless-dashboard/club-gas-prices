"""The smoke script's pure helpers. Playwright is not needed to import them."""

from __future__ import annotations

import sys
from pathlib import Path

# tests/smoke/ sits beside this file and is not an importable package, so the
# directory itself goes on sys.path.
SMOKE = Path(__file__).resolve().parent / "smoke"
sys.path.insert(0, str(SMOKE))

from smoke_site import (  # noqa: E402
    LIBRARY_HOSTS,
    PAGES,
    area_turns,
    chains_by_country,
    doubles_back,
    expected_latest_stations,
    expected_marker_count,
    expected_usd_countries,
    is_site_console_error,
    path_points,
    usd_series,
)

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

from site_sample import build  # noqa: E402

LATEST = [
    {
        "station_key": "US-1364",
        "country": "US",
        "lat": 27.49,
        "lon": -82.47,
        "grades": {
            "regular": {"price": 3.999, "price_usd_per_litre": 1.0564},
            "premium": {"price": 4.629, "price_usd_per_litre": 1.2229},
        },
    },
    {
        "station_key": "US-140",
        "country": "US",
        "lat": 19.68,
        "lon": -156.01,
        "grades": {
            "regular": {"price": 4.899, "price_usd_per_litre": 1.2941},
            "diesel": {"price": 6.899, "price_usd_per_litre": 1.8225},
        },
    },
    {
        "station_key": "MX-Mexicali",
        "country": "MX",
        "lat": None,
        "lon": None,
        "grades": {"regular": {"price": 20.89, "price_usd_per_litre": 1.2254}},
    },
    {
        "station_key": "GB-Coventry",
        "country": "GB",
        "lat": 52.39,
        "lon": -1.56,
        "grades": {},
    },
    {
        # A currency with no rate that day: a price, but nothing in USD.
        "station_key": "TW-Neihu",
        "country": "TW",
        "lat": 25.08,
        "lon": 121.57,
        "grades": {"regular": {"price": 28.9, "price_usd_per_litre": None}},
    },
]


def test_expected_marker_count_needs_a_position_and_that_grade():
    assert expected_marker_count(LATEST, "regular") == 3  # MX has no position
    assert expected_marker_count(LATEST, "premium") == 1
    assert expected_marker_count(LATEST, "diesel") == 1
    assert expected_marker_count(LATEST, "other") == 0


def test_expected_usd_countries_counts_countries_not_stations():
    # Compare draws one dot and Trends one line per country with a USD median,
    # so the two US stations count once. A position is irrelevant here -- MX
    # has none and still appears on both pages -- but a USD value is not: TW
    # has a price and no rate, so neither page can draw it.
    assert expected_usd_countries(LATEST, "regular") == 2
    assert expected_usd_countries(LATEST, "premium") == 1
    assert expected_usd_countries(LATEST, "other") == 0


def test_only_errors_from_the_site_origin_count():
    origin = "http://127.0.0.1:8123"
    assert is_site_console_error("error", origin + "/club-gas-prices/index.html", origin)
    assert is_site_console_error("error", "", origin)
    # Warnings, CDN scripts and the favicon Chrome asks for are all ignored.
    assert not is_site_console_error("warning", origin + "/x", origin)
    assert not is_site_console_error("error", "https://cdn.jsdelivr.net/npm/leaflet.js", origin)
    assert not is_site_console_error("error", origin + "/favicon.ico", origin)


def test_the_five_pages_and_the_library_hosts_are_the_spec_ones():
    assert PAGES == [
        "index.html",
        "compare.html",
        "trends.html",
        "changes.html",
        "station.html",
        "about.html",
    ]
    assert LIBRARY_HOSTS == ("cdn.jsdelivr.net", "cdn.observableusercontent.com")


# Two chains in one US state, as Sam's Club and Costco both serve Florida.
TWO_CHAINS = [
    {
        "station_key": "US-COSTCO-1364",
        "country": "US",
        "brand": "COSTCO",
        "region": "FL",
        "grades": {"regular": {"price_usd_per_litre": 1.0564}},
    },
    {
        "station_key": "US-SAMS-8119",
        "country": "US",
        "brand": "SAMS",
        "region": "FL",
        "grades": {"regular": {"price_usd_per_litre": 1.0036}},
    },
    {
        "station_key": "US-SAMS-6376",
        "country": "US",
        "brand": "SAMS",
        "region": "TX",
        "grades": {"regular": {"price_usd_per_litre": 0.9772}},
    },
    {
        "station_key": "GB-COSTCO-Coventry",
        "country": "GB",
        "brand": "COSTCO",
        "region": None,
        "grades": {"regular": {"price_usd_per_litre": 2.1745}},
    },
]


def test_chains_are_counted_per_country():
    assert chains_by_country(TWO_CHAINS) == {"US": {"COSTCO", "SAMS"}, "GB": {"COSTCO"}}


def test_a_series_is_a_chain_in_a_place_never_the_place_alone():
    # Three series across countries, where the country alone would say two.
    assert usd_series(TWO_CHAINS, "regular") == {
        ("US", "COSTCO"),
        ("US", "SAMS"),
        ("GB", "COSTCO"),
    }
    # Florida is two series; a station with no region is in no region's.
    assert usd_series(TWO_CHAINS, "regular", country="US") == {
        ("FL", "COSTCO"),
        ("FL", "SAMS"),
        ("TX", "SAMS"),
    }
    assert usd_series(TWO_CHAINS, "regular", country="GB") == set()


def test_path_points_reads_straight_segments_and_nothing_else():
    assert path_points("M40,301.5L134.8,353.5Z") == [(40.0, 301.5), (134.8, 353.5)]
    assert path_points("M0,-2.5L.5,3") == [(0.0, -2.5), (0.5, 3.0)]
    # The coverage strip's step curve repeats x by design, so it is not read.
    assert path_points("M0,10H5V20H10") is None


def test_a_line_that_alternates_between_two_chains_doubles_back():
    # The start of the Florida line the audit found on Trends: two prices a day.
    sawtooth = "M40,301.481L40,353.504L134.769,353.504L134.769,301.481L229.538,301.481"
    assert doubles_back(sawtooth)
    assert not doubles_back("M40,301.481L134.769,301.481L229.538,292.978")
    # A single reading is a point, and a point is one series.
    assert not doubles_back("M40,301.481Z")


def test_a_band_for_two_chains_turns_back_more_than_once():
    # Out along the upper edge and back along the lower one: one series.
    one = "M52,40L64,35L77,35L77,60L64,62L52,70Z"
    assert area_turns(one) == 1
    # The same rows for two chains, run into one polygon, as the separate
    # charts drew them.
    two = "M52,40L64,35L77,35L52,144L64,144L77,140L77,160L64,160L52,162L77,60L64,62L52,70Z"
    assert area_turns(two) > 1


def test_the_latest_day_count_adds_every_series(tmp_path: Path):
    # The sample's newest day: three Costco and two Sam's Club stations in the
    # United States, and one or two in each other country. The largest series
    # alone is three.
    build(tmp_path)
    assert expected_latest_stations(tmp_path / "summary_daily.parquet", "regular") == 12
    assert expected_latest_stations(tmp_path / "summary_daily.parquet", "none") == 0
