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
    expected_marker_count,
    expected_usd_countries,
    is_site_console_error,
)

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
    assert PAGES == ["index.html", "compare.html", "trends.html", "station.html", "about.html"]
    assert LIBRARY_HOSTS == ("cdn.jsdelivr.net", "cdn.observableusercontent.com")
