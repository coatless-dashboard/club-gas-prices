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
    PAGE_IDS,
    expected_marker_count,
    is_site_console_error,
)

LATEST = [
    {
        "station_key": "US-1364",
        "lat": 27.49,
        "lon": -82.47,
        "grades": {"regular": {"price": 3.999}, "premium": {"price": 4.629}},
    },
    {
        "station_key": "US-140",
        "lat": 19.68,
        "lon": -156.01,
        "grades": {"regular": {"price": 4.899}, "diesel": {"price": 6.899}},
    },
    {
        "station_key": "MX-Mexicali",
        "lat": None,
        "lon": None,
        "grades": {"regular": {"price": 20.89}},
    },
    {"station_key": "GB-Coventry", "lat": 52.39, "lon": -1.56, "grades": {}},
]


def test_expected_marker_count_needs_a_position_and_that_grade():
    assert expected_marker_count(LATEST, "regular") == 2  # MX has no position
    assert expected_marker_count(LATEST, "premium") == 1
    assert expected_marker_count(LATEST, "diesel") == 1
    assert expected_marker_count(LATEST, "other") == 0


def test_only_errors_from_the_site_origin_count():
    origin = "http://127.0.0.1:8123"
    assert is_site_console_error("error", origin + "/costco-gas-prices/index.html", origin)
    assert is_site_console_error("error", "", origin)
    # Warnings, CDN scripts and the favicon Chrome asks for are all ignored.
    assert not is_site_console_error("warning", origin + "/x", origin)
    assert not is_site_console_error("error", "https://cdn.jsdelivr.net/npm/leaflet.js", origin)
    assert not is_site_console_error("error", origin + "/favicon.ico", origin)


def test_the_five_page_ids_and_the_library_hosts_are_the_spec_ones():
    assert PAGE_IDS == ["map", "compare", "trends", "station", "about"]
    assert LIBRARY_HOSTS == ("cdn.jsdelivr.net", "cdn.observableusercontent.com")
