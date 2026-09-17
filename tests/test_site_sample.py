"""The sample site data used to render and smoke-test the dashboard locally."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(FIXTURES))

from site_sample import build  # noqa: E402

SUMMARY_COLUMNS = [
    "capture_date",
    "country",
    "brand",
    "level",
    "region",
    "grade",
    "n_stations",
    "n_stations_usd",
    "median_local_per_litre",
    "p25_local_per_litre",
    "p75_local_per_litre",
    "median_usd_per_litre",
    "p25_usd_per_litre",
    "p75_usd_per_litre",
]
# The two parquet schemas the pages query, restated here because the code that
# writes the real ones lives in the data repository. This is the seam: if
# `club_gas.sitedata` ever changes a column, this file is what has to change
# with it, and these two tests are what fail if it does not.
HISTORY_COLUMNS = [
    "capture_date",
    "station_key",
    "brand",
    "grade",
    "price_local_per_litre",
    "price_usd_per_litre",
    "currency",
    "n_captures",
    "changed",
    "moved_intraday",
]


def test_build_writes_the_five_files(tmp_path: Path):
    build(tmp_path)
    for name in (
        "latest.json",
        "stations.json",
        "meta.json",
        "summary_daily.parquet",
        "history.parquet",
    ):
        assert (tmp_path / name).is_file(), name


def test_summary_and_history_use_the_columns_the_page_queries(tmp_path: Path):
    build(tmp_path)
    assert pl.read_parquet(tmp_path / "summary_daily.parquet").columns == SUMMARY_COLUMNS
    assert pl.read_parquet(tmp_path / "history.parquet").columns == HISTORY_COLUMNS


def test_latest_keeps_the_published_value_and_both_conversions(tmp_path: Path):
    build(tmp_path)
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    by_key = {row["station_key"]: row for row in latest}

    bradenton = by_key["US-COSTCO-1364"]["grades"]["regular"]
    assert bradenton["price_raw"] == "3.999"
    assert bradenton["price_unit"] == "USD/gal"
    assert bradenton["price_usd_per_gallon"] == 3.999  # USD/gal rows keep the published value
    assert bradenton["price_local_per_litre"] == round(3.999 / 3.785411784, 4)

    coventry = by_key["GB-COSTCO-Coventry"]["grades"]["regular"]
    assert coventry["price_raw"] == "160.9"
    assert coventry["price_unit"] == "GBp/L"
    assert coventry["price_local_per_litre"] == 1.609  # pence to pounds

    tomiya = by_key["JP-COSTCO-Tomiya"]
    assert tomiya["name_local"] == "富谷"
    assert tomiya["grades"]["regular"]["price_raw"] == "¥149"
    assert "Kerosene" in tomiya["other"]  # non-comparable grades stay out of `grades`


def test_meta_carries_the_basemap_and_the_grade_table(tmp_path: Path):
    build(tmp_path)
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert set(meta) >= {
        "built_at_utc",
        "capture_id",
        "countries",
        "grades",
        "releases",
        "notice",
        "stale_after_hours",
        "basemap",
    }
    assert set(meta["basemap"]) == {
        "provider",
        "light_url",
        "dark_url",
        "subdomains",
        "max_zoom",
        "dark_filter",
        "attribution",
    }
    assert meta["basemap"]["provider"] in {"osm", "carto"}
    assert "{z}/{x}/{y}" in meta["basemap"]["light_url"]
    assert "{z}/{x}/{y}" in meta["basemap"]["dark_url"]
    assert meta["basemap"]["dark_filter"] is True  # OSM ships no dark tiles
    assert {"current", "all"} <= set(meta["releases"])
    assert meta["releases"]["current"].endswith("/releases/tag/current")
    assert meta["releases"]["all"].endswith("/releases")
    assert meta["stale_after_hours"] == 12
    assert meta["notice"].startswith("Unofficial.")
    assert {row["country"] for row in meta["grades"]} == {"US", "CA", "MX", "GB", "AU", "JP", "TW"}
