"""The smoke script's pure helpers. Playwright is not needed to import them."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

# tests/smoke/ sits beside this file and is not an importable package, so the
# directory itself goes on sys.path.
SMOKE = Path(__file__).resolve().parent / "smoke"
sys.path.insert(0, str(SMOKE))

import smoke_site  # noqa: E402
from smoke_site import (  # noqa: E402
    LIBRARY_HOSTS,
    PAGES,
    area_turns,
    chains_by_country,
    contrast_ratio,
    currencies,
    doubles_back,
    expected_country_series,
    expected_latest_stations,
    expected_marker_count,
    expected_usd_countries,
    fetches_history,
    fewest_stations_state,
    freshness_cases,
    grade_table_problems,
    is_site_console_error,
    launch_browser,
    overlapping_labels,
    parse_color,
    path_points,
    stations_per_chain,
    time_of_day_charts,
    tip_units,
    unexplained_colors,
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


def test_the_strip_draws_a_line_for_every_chain_in_every_country(tmp_path: Path):
    # Two chains in the United States and one in each of six other countries.
    build(tmp_path)
    assert expected_country_series(tmp_path / "summary_daily.parquet", "regular") == 8
    assert expected_country_series(tmp_path / "summary_daily.parquet", "none") == 0


def test_the_smallest_state_is_the_one_with_fewest_stations_pricing_the_grade():
    # Florida has two, and Texas one; Great Britain has no states.
    assert fewest_stations_state(TWO_CHAINS, "regular") == "TX"
    # A station that does not price the grade draws no row, so is not counted.
    assert fewest_stations_state(TWO_CHAINS, "diesel") is None
    florida_only = [s for s in TWO_CHAINS if s["region"] == "FL"]
    assert fewest_stations_state(florida_only, "regular") == "FL"


def test_a_date_axis_ticked_between_days_is_caught():
    # The Trends chart on the release's second day, as the audit read it.
    hourly = {"chart": "Median", "labels": ["12 AM Sep 17", "12 PM", "12 AM Sep 18"]}
    strip = {"chart": "Stations", "labels": ["12 AM Sep 17", "6 AM", "12 PM", "6 PM"]}
    # Plot's own labels for day ticks, and for month ticks on a long history.
    days = {"chart": "Change", "labels": ["17 Sep", "18"]}
    months = {"chart": "Price", "labels": ["Oct 2026", "Nov", "Dec"]}
    assert time_of_day_charts([hourly, strip, days, months]) == ["Median", "Stations"]
    assert time_of_day_charts([{"chart": "Minutes", "labels": ["3:15", "3:30"]}]) == ["Minutes"]
    assert time_of_day_charts([days, months]) == []


def label_box(text: str, top: float, height: float = 12, left: float = 1100) -> dict:
    return {"text": text, "left": left, "right": left + 40, "top": top, "bottom": top + height}


def test_end_labels_collide_only_where_they_share_width_and_height():
    # Japan and Taiwan on the release's price chart, 5.7px apart.
    japan, taiwan = label_box("Japan", 914.0), label_box("Taiwan", 919.7)
    assert overlapping_labels([japan, taiwan]) == [("Japan", "Taiwan")]
    # One above the other, as the page spreads them, and merely touching.
    assert overlapping_labels([japan, label_box("Taiwan", 928.0)]) == []
    assert overlapping_labels([japan, label_box("Taiwan", 925.8)]) == []
    # Side by side at one height: a stalled series ends further left.
    assert overlapping_labels([japan, label_box("Taiwan", 914.0, left=1000)]) == []
    # Every colliding pair is named, and a two-line label is as tall as it is.
    sams = label_box("United States Sam's Club", 905.0, height=22)
    assert overlapping_labels([sams, japan, taiwan]) == [
        ("United States Sam's Club", "Japan"),
        ("United States Sam's Club", "Taiwan"),
        ("Japan", "Taiwan"),
    ]


def test_every_chain_in_every_country_gets_its_first_stations_drawn():
    assert stations_per_chain(TWO_CHAINS) == [
        "US-COSTCO-1364",
        "US-SAMS-8119",
        "US-SAMS-6376",
        "GB-COSTCO-Coventry",
    ]
    assert stations_per_chain(TWO_CHAINS, per=1) == [
        "US-COSTCO-1364",
        "US-SAMS-8119",
        "GB-COSTCO-Coventry",
    ]


def test_a_tip_says_which_currency_each_price_is_in():
    # Plot separates a tip's lines with zero-width spaces.
    before = "Taiwan · 2026-09-18\u200b0.0% since 2026-09-17\u200b113.562 local/gal that day"
    after = "Taiwan · 2026-09-18\u200b0.0% since 2026-09-17\u200b30.000 TWD/L that day"
    assert tip_units(before) == ["local"]
    assert tip_units(after) == ["TWD"]
    assert tip_units("Canada · 2026-09-18") == []


def test_currencies_come_from_every_graded_price(tmp_path: Path):
    build(tmp_path)
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert currencies(latest) == {"USD", "CAD", "MXN", "GBP", "AUD", "JPY", "TWD"}


HEAD = ["Country", "Chain", "Source label", "Grade", "Spec", "Stated by"]


def test_the_grade_table_names_each_rows_chain_or_has_no_chain_column():
    with_brand = [{"country": "US", "brand": "COSTCO"}, {"country": "US", "brand": "SAMS"}]
    without = [{"country": "US"}, {"country": "US"}]
    named = [["United States", "Costco"], ["United States", "Sam's Club"]]
    both = {"COSTCO", "SAMS"}
    assert grade_table_problems(HEAD, named, with_brand, both) == []
    # A meta.json from before `brand`: the column the live site drew blank.
    blank = [["United States", ""], ["United States", ""]]
    assert grade_table_problems(HEAD, blank, without, both) == [
        "a Chain column, though no grade row says its chain",
        "2 rows with a blank Chain cell",
    ]
    # The same rows with the column left out are fine.
    no_chain = [h for h in HEAD if h != "Chain"]
    assert grade_table_problems(no_chain, [["United States"]] * 2, without, both) == []
    # Rows that say their chain have to be shown saying it.
    assert grade_table_problems(no_chain, [["United States"]] * 2, with_brand, both) == [
        "no Chain column, though the grade rows say their chain"
    ]
    # And every row is drawn.
    assert grade_table_problems(HEAD, named[:1], with_brand, both) == [
        "1 rows for 2 grades in meta.json"
    ]


def test_the_grade_table_names_no_chain_the_data_does_not_hold():
    rows = [{"country": "US", "brand": "COSTCO"}, {"country": "US", "brand": "SAMS"}]
    cells = [["United States", "Costco"], ["United States", "Sam's Club"]]
    assert grade_table_problems(HEAD, cells, rows, {"COSTCO"}) == [
        "it names ['SAMS'], which have no stations here"
    ]


def test_a_color_reads_the_same_as_hex_or_as_the_browser_reports_it():
    # The chart writes the stylesheet's hex; the browser reports rgb().
    assert parse_color("#e9c46a") == (233, 196, 106)
    assert parse_color("#FFF") == (255, 255, 255)
    assert parse_color("rgb(233, 196, 106)") == (233, 196, 106)
    assert parse_color(" rgba(17, 20, 24, 0.5) ") == (17, 20, 24)
    assert parse_color("amber") is None
    assert parse_color("") is None


def test_contrast_is_the_wcag_ratio():
    white, black = (255, 255, 255), (0, 0, 0)
    assert round(contrast_ratio(white, black), 2) == 21.0
    assert contrast_ratio(black, white) == contrast_ratio(white, black)
    assert contrast_ratio(white, white) == 1.0
    # White on the light ramp's amber, as every cluster count was drawn.
    assert round(contrast_ratio(white, parse_color("#e9c46a")), 2) == 1.67


def test_a_key_must_carry_every_color_a_cell_is_drawn_in():
    swatches = ["rgb(42, 111, 151)", "rgb(233, 196, 106)", "rgb(154, 160, 166)"]
    assert unexplained_colors(["#2a6f97", "#E9C46A", "#9aa0a6"], swatches) == []
    # A cell color the key does not show, and no key at all.
    assert unexplained_colors(["#2a6f97", "#b3261e"], swatches) == ["#b3261e"]
    assert unexplained_colors(["#2a6f97"], []) == ["#2a6f97"]


class FakeBrowser:
    """A launched browser that can, or cannot, open a page and run a script."""

    def __init__(self, name: str, works: bool = True) -> None:
        self.name = name
        self.works = works
        self.closed = False

    def new_page(self):
        if not self.works:
            raise RuntimeError("Target page, context or browser has been closed")
        return SimpleNamespace(evaluate=lambda script: 2, close=lambda: None)

    def close(self) -> None:
        self.closed = True


class FakePlaywright:
    """Playwright on a runner whose Chrome is `chrome`: a working one, a broken
    one, or none at all. The bundled Chromium launches once it is installed."""

    def __init__(self, chrome: str) -> None:
        self.chrome = chrome
        self.launched: list[FakeBrowser] = []
        self.chromium = SimpleNamespace(launch=self.launch)

    def launch(self, channel: str | None = None, headless: bool = True) -> FakeBrowser:
        if channel == "chrome":
            if self.chrome == "missing":
                raise RuntimeError("Chromium distribution 'chrome' is not found")
            browser = FakeBrowser("chrome", works=self.chrome == "working")
        else:
            browser = FakeBrowser("chromium")
        self.launched.append(browser)
        return browser


def fake_installer(monkeypatch, *, packages_install: bool) -> list[list[str]]:
    """Stand in for `playwright install`; `--with-deps` fails unless told not to,
    the way an apt install does on a release with a renamed package."""
    calls: list[list[str]] = []

    def run(command, check=False):
        calls.append(command[3:])
        if "--with-deps" in command and not packages_install:
            raise subprocess.CalledProcessError(100, command)
        return subprocess.CompletedProcess(command, 0)

    fake = SimpleNamespace(run=run, CalledProcessError=subprocess.CalledProcessError)
    monkeypatch.setattr(smoke_site, "subprocess", fake)
    return calls


def test_the_gate_uses_the_images_chrome_when_it_works(monkeypatch):
    calls = fake_installer(monkeypatch, packages_install=True)
    playwright = FakePlaywright("working")
    assert launch_browser(playwright).name == "chrome"
    assert calls == []


def test_without_chrome_the_gate_installs_chromium(monkeypatch):
    calls = fake_installer(monkeypatch, packages_install=True)
    assert launch_browser(FakePlaywright("missing")).name == "chromium"
    assert calls == [["install", "--with-deps", "chromium"]]


def test_without_chrome_the_gate_still_runs_when_the_packages_will_not_install(monkeypatch):
    """ubuntu-latest moves to a release this Playwright does not know. Its
    package list can name a package that release renamed, which fails the whole
    apt install; the browser alone still installs and still runs."""
    calls = fake_installer(monkeypatch, packages_install=False)
    assert launch_browser(FakePlaywright("missing")).name == "chromium"
    assert calls == [["install", "--with-deps", "chromium"], ["install", "chromium"]]


def test_a_chrome_that_starts_but_cannot_open_a_page_is_not_trusted(monkeypatch):
    fake_installer(monkeypatch, packages_install=True)
    playwright = FakePlaywright("broken")
    assert launch_browser(playwright).name == "chromium"
    chrome = playwright.launched[0]
    assert chrome.name == "chrome" and chrome.closed


def test_a_page_fetches_history_only_by_asking_for_the_file():
    base = "http://127.0.0.1:8123/club-gas-prices/"
    assert fetches_history([base + "compare.html", base + "data/history.parquet"])
    assert not fetches_history([base + "data/summary_daily.parquet", base + "history.html"])


NOW = datetime(2026, 9, 18, 16, 30, tzinfo=UTC)


def test_the_freshness_cases_name_the_stale_chain_where_the_country_has_two(tmp_path: Path):
    build(tmp_path)
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    stations = json.loads((tmp_path / "stations.json").read_text(encoding="utf-8"))
    cases = freshness_cases(meta, stations, NOW)
    (_, per_feed, said), (_, per_country, said_by_country), (_, partial, said_partial) = cases[:3]
    (_, fresh, said_fresh) = cases[3]
    assert said == "Last successful capture over 12 h ago: United States · Sam's Club (30 h)."
    assert per_feed["feeds"]["US-SAMS"]["last_success_capture_id"] == "2026-09-17T1030Z"
    assert per_feed["feeds"]["US-COSTCO"]["last_success_capture_id"] == "2026-09-18T1530Z"
    # The country block reads fresh, as the collector's roll-up would.
    assert per_feed["countries"]["US"]["last_success_capture_id"] == "2026-09-18T1530Z"
    # A chain with no station in the data, never captured: not to be named.
    assert per_feed["feeds"]["US-ELSEWHERE"]["last_success_capture_id"] is None
    # Without feeds, the country block is what goes stale.
    assert "feeds" not in per_country
    assert per_country["countries"]["US"]["last_success_capture_id"] == "2026-09-17T1030Z"
    assert said_by_country == "Last successful capture over 12 h ago: United States (30 h)."
    # A capture that skipped the United States lists none of its feeds, so its
    # country block is read, and the other countries still by feed.
    assert not any(feed_id.startswith("US-") for feed_id in partial["feeds"])
    assert "CA-COSTCO" in partial["feeds"]
    assert partial["countries"]["US"]["last_success_capture_id"] == "2026-09-17T1030Z"
    assert said_partial == "Last successful capture over 12 h ago: United States (30 h)."
    assert "US-ELSEWHERE" in fresh["feeds"]
    assert said_fresh == "Every country was captured in the last 12 hours."
    for _, served, _ in freshness_cases(meta, stations, NOW):
        assert served["built_at_utc"] == "2026-09-18T16:30:00Z"


def test_with_one_chain_the_freshness_cases_name_the_country_alone():
    stations = [{"country": "US", "brand": "COSTCO"}, {"country": "CA", "brand": "COSTCO"}]
    meta = {"stale_after_hours": 12, "countries": {}}
    (_, per_feed, said), *_ = freshness_cases(meta, stations, NOW)
    assert said == "Last successful capture over 12 h ago: United States (30 h)."
    # Sam's Club is switched on and failing, and the data holds none of it.
    assert per_feed["feeds"]["US-SAMS"]["status"] == "failed"
    assert per_feed["feeds"]["US-COSTCO"]["last_success_capture_id"] == "2026-09-17T1030Z"
