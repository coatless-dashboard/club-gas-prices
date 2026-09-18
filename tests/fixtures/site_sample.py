"""A small, realistic `site/data/` set, built without running the pipeline.

Every price here is a real value captured from the live sources on 2026-09-15,
and the rates are the Frankfurter reference rates of 2026-09-14. The dashboard
reads only `site/data/`, so this is enough to render and smoke-test the page
locally.

    uv run python tests/fixtures/site_sample.py site/data
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

# Kept in step with club_gas.sitedata.with_change_flags in the data
# repository. It is copied rather than imported because this repository holds
# no pipeline: the sample has to carry the same `changed` and `moved_intraday`
# columns the real history.parquet does, or the station page's change list
# renders against a shape the site never actually receives.
INTRADAY_EPSILON = 5e-5


def with_change_flags(deduped: pl.DataFrame) -> pl.DataFrame:
    """Mark where a price moved, day over day and within a day."""
    previous = (
        pl.col("price_local_per_litre")
        .shift(1)
        .over(["station_key", "grade"], order_by="capture_date")
    )
    moved = (
        (pl.col("price_max") - pl.col("price_min")).abs() > INTRADAY_EPSILON
        if {"price_min", "price_max"} <= set(deduped.columns)
        else pl.lit(None, dtype=pl.Boolean)
    )
    return deduped.with_columns(
        pl.when(previous.is_null() | pl.col("price_local_per_litre").is_null())
        .then(pl.lit(None, dtype=pl.Boolean))
        .otherwise((pl.col("price_local_per_litre") - previous).abs() > INTRADAY_EPSILON)
        .alias("changed"),
        moved.alias("moved_intraday"),
    )


LITRES_PER_GALLON = 3.785411784

# Frankfurter v2, rate date 2026-09-14: quote-currency units per USD.
UNITS_PER_USD = {
    "USD": 1.0,
    "AUD": 1.399,
    "CAD": 1.3871,
    "GBP": 0.73996,
    "JPY": 154.24,
    "MXN": 17.0477,
    "TWD": 31.709,
}
FX_RATE_DATE = "2026-09-14"

# config/site.toml in the data repository, as it stands on 2026-09-18. meta.json's
# `notice` is this prefix and then one clause per chain in the data, as a list.
NOTICE_PREFIX = (
    "Unofficial. Prices are collected from public websites and may differ from the price "
    "at the pump."
)
NOTICES = {
    "COSTCO": "Not affiliated with, endorsed by, or connected to Costco Wholesale Corporation.",
    "SAMS": (
        "Not affiliated with, endorsed by, or connected to Sam's West, Inc., Sam's Club, "
        "or Walmart Inc."
    ),
}

RELEASE_DL = "https://github.com/coatless-data/club-gas-prices/releases/download/current/"

# fmt: off
STATIONS = [
    {
        "station_key": "US-SAMS-6376", "country": "US",
        "brand": "SAMS", "name": "Addison Sam's Club", "name_local": None,
        "address": "4062 Lbj Freeway", "postcode": "75244", "alt_id": "6376",
        "city": "Dallas", "region": "TX", "lat": 32.921881, "lon": -96.843062,
        "price_unit": "USD/gal", "currency": "USD",
        "prices": [("UNLEAD", "regular", "3.699"), ("PREMIUM", "premium", "4.399")],
    },
    {
        "station_key": "US-SAMS-8119", "country": "US",
        "brand": "SAMS", "name": "Orlando Sam's Club", "name_local": None,
        "address": "4600 Millenia Plaza Way", "postcode": "32839", "alt_id": "8119",
        "city": "Orlando", "region": "FL", "lat": 28.4855, "lon": -81.4295,
        "price_unit": "USD/gal", "currency": "USD",
        "prices": [("UNLEAD", "regular", "3.799"), ("PREMIUM", "premium", "4.259")],
    },
    {
        "station_key": "US-COSTCO-1364", "country": "US",
        "brand": "COSTCO", "name": "Bradenton", "name_local": None,
        "address": "5311 CORTEZ RD W", "postcode": "34210", "alt_id": None,
        "city": "BRADENTON", "region": "FL", "lat": 27.49462445, "lon": -82.47014406,
        "price_unit": "USD/gal", "currency": "USD",
        "prices": [("regular", "regular", "3.999"), ("premium", "premium", "4.629")],
    },
    {
        "station_key": "US-COSTCO-140", "country": "US",
        "brand": "COSTCO", "name": "Kona", "name_local": None,
        "city": "KAILUA KONA", "region": "HI", "lat": 19.68545677, "lon": -156.0168782,
        "price_unit": "USD/gal", "currency": "USD",
        "prices": [("regular", "regular", "4.899"), ("premium", "premium", "5.699"),
                   ("diesel", "diesel", "6.899"), ("clear", "other", "5.699")],
    },
    {
        "station_key": "US-COSTCO-335", "country": "US",
        "brand": "COSTCO", "name": "Carolina", "name_local": None,
        "city": "SAN JUAN", "region": "PR", "lat": 18.39974101, "lon": -65.99743872,
        "price_unit": "USD/L", "currency": "USD",
        "prices": [("regular", "regular", "1.097"), ("premium", "premium", "1.267")],
    },
    {
        "station_key": "CA-COSTCO-530", "country": "CA",
        "brand": "COSTCO", "name": "N London", "name_local": None,
        "address": "1685 WONDERLAND RD N", "postcode": "N6G 4W8", "alt_id": None,
        "city": "LONDON", "region": "ON", "lat": 42.987, "lon": -81.293,
        "price_unit": "CAD/L", "currency": "CAD",
        "prices": [("regular", "regular", "1.739"), ("premium", "premium", "1.969"),
                   ("diesel", "diesel", "2.279")],
    },
    {
        "station_key": "CA-COSTCO-1213", "country": "CA",
        "brand": "COSTCO", "name": "Vaudreuil", "name_local": None,
        "city": "VAUDREUIL-DORION", "region": "QC", "lat": 45.415, "lon": -74.038,
        "price_unit": "CAD/L", "currency": "CAD",
        "prices": [("regular", "regular", "1.799"), ("premium", "premium", "1.999"),
                   ("diesel", "diesel", "2.649")],
    },
    {
        "station_key": "MX-COSTCO-750", "country": "MX",
        "brand": "COSTCO", "name": "Mexicali", "name_local": None,
        "address": "Calzada Cetys 2600", "postcode": "21376", "alt_id": "Mexicali",
        "city": "Mexicali", "region": "BCN", "lat": 32.60663671, "lon": -115.4343584,
        "price_unit": "MXN/L", "currency": "MXN",
        "prices": [("Regular", "regular", "$20.89"), ("Premium", "premium", "$25.39")],
    },
    {
        "station_key": "GB-COSTCO-Coventry", "country": "GB",
        "brand": "COSTCO", "name": "Coventry", "name_local": None,
        "address": "Brandon Road", "postcode": "CV3 2AA", "alt_id": "coventry",
        "city": "Coventry", "region": None, "lat": 52.398583, "lon": -1.560788,
        "price_unit": "GBp/L", "currency": "GBP",
        "prices": [("5301", "regular", "160.9"), ("5302", "premium", "169.9"),
                   ("5303", "diesel", "184.9")],
    },
    {
        "station_key": "AU-COSTCO-109", "country": "AU",
        "brand": "COSTCO", "name": "Marsden Park", "name_local": None,
        "address": "10 Marsden Park Rd", "postcode": "2765", "alt_id": "Marsden Park",
        "city": "Marsden Park", "region": "NSW", "lat": -33.72141, "lon": 150.839951,
        "price_unit": "AUD/L", "currency": "AUD",
        "prices": [("E10", "regular", "$2.127"), ("Premium 98", "premium", "$2.327"),
                   ("Diesel", "diesel", "$2.527")],
    },
    {
        "station_key": "JP-COSTCO-Tomiya", "country": "JP",
        "brand": "COSTCO", "name": "Tomiya", "name_local": "富谷",
        "address": "1-1 Narita", "postcode": "981-3341", "alt_id": "costcoJapanTomiyaWarehouse",
        "city": "富谷市", "region": "宮城県", "lat": 38.39, "lon": 140.88,
        "price_unit": "JPY/L", "currency": "JPY",
        "prices": [("Regular", "regular", "¥149"), ("Premium", "premium", "¥159"),
                   ("Diesel", "diesel", "¥135"), ("Kerosene", "other", "¥124")],
    },
    {
        "station_key": "TW-COSTCO-010", "country": "TW",
        "brand": "COSTCO", "name": "Chungli",
        "address": "No. 1 Zhongli Rd", "postcode": "320", "alt_id": "costcoTaiwanWarehouse010",
        "name_local": "桃園中壢店", "city": None, "region": "桃園市",
        "lat": 24.9636189, "lon": 121.1558083,
        "price_unit": "TWD/L", "currency": "TWD",
        "prices": [("95", "regular", "$30.0"), ("98", "premium", "$31.5"),
                   ("Diesel", "diesel", "$28.6")],
    },
]

# fmt: on

# config/grades.csv in the data repository, as it stands on 2026-09-18. The
# About page draws its grade table from these rows, so the sample carries the
# real labels and specifications rather than a shorter invented set.
GRADES_CSV = """\
brand,country,grade_raw,grade,priority,label,spec,spec_source,spec_source_url
COSTCO,US,regular,regular,1,Regular,,,
COSTCO,US,premium,premium,1,Premium,,,
COSTCO,US,diesel,diesel,1,Diesel,,,
COSTCO,US,clear,other,1,Clear,,,
COSTCO,CA,regular,regular,1,Regular,,,
COSTCO,CA,premium,premium,1,Premium,,,
COSTCO,CA,diesel,diesel,1,Diesel,,,
COSTCO,MX,Regular,regular,1,Regular,Octane index ([RON+MON]/2) at least 87,reported,https://api-reportediario.cne.gob.mx/api/EstacionServicio/Petroliferos
COSTCO,MX,Premium,premium,1,Premium,,,
COSTCO,GB,5301,regular,1,Unleaded Petrol,E10,reported,https://www.fuel-finder.uk/costco-fuel-prices
COSTCO,GB,5302,premium,1,Premium Unleaded Petrol,E5 97,reported,https://www.fuel-finder.uk/costco-fuel-prices
COSTCO,GB,5303,diesel,1,Premium Diesel,B7,reported,https://www.fuel-finder.uk/costco-fuel-prices
COSTCO,AU,Unleaded 91,regular,2,Unleaded 91,91 RON,source,
COSTCO,AU,E10,regular,1,E10,10% ethanol blend,source,
COSTCO,AU,Premium 98,premium,1,Premium 98,98 RON,source,
COSTCO,AU,Diesel,diesel,1,Diesel,Premium diesel,reported,https://www.fuelcheck.nsw.gov.au/fuel/api/v1/fuel/refData
COSTCO,JP,Regular,regular,1,Regular,,,
COSTCO,JP,Premium,premium,1,Premium,,,
COSTCO,JP,Diesel,diesel,1,Diesel,,,
COSTCO,JP,Kerosene,other,1,Kerosene,,,
COSTCO,TW,95,regular,1,95,95 RON,source,
COSTCO,TW,98,premium,1,98,98 RON,source,
COSTCO,TW,Diesel,diesel,1,Diesel,,,
SAMS,US,UNLEAD,regular,1,Regular,,,
SAMS,US,PREMIUM,premium,1,Premium,,,
SAMS,US,DIESEL,diesel,1,Diesel,,,
SAMS,US,MIDGRAD,other,1,Mid-Grade,,,
SAMS,US,MID CLR,other,1,Mid-Grade (clear),,,
SAMS,US,PREM CLR,other,1,Premium (clear),,,
"""

# Kept in step with club_gas.sitedata.GRADE_TABLE_FIELDS, in its order: the
# fields of meta.json's `grades` rows. `brand` is what the About page names the
# chain from.
GRADE_TABLE_FIELDS = (
    "country",
    "brand",
    "grade_raw",
    "grade",
    "priority",
    "label",
    "spec",
    "spec_source",
    "spec_source_url",
)


def grade_table(brands: set[str]) -> list[dict]:
    """meta.json's `grades`: the chains in the data only, as sitedata writes them.

    The site names no chain it holds no prices for, so a chain whose feed is off
    has no rows here even though config/grades.csv lists its labels.
    """
    rows = []
    for entry in csv.DictReader(io.StringIO(GRADES_CSV)):
        if entry["brand"] not in brands:
            continue
        row = {field: entry[field] for field in GRADE_TABLE_FIELDS}
        row["priority"] = int(row["priority"])
        rows.append(row)
    return rows


def notice(brands: set[str]) -> list[str]:
    """meta.json's `notice`, as club_gas.config builds it: the chains in the data only."""
    return [NOTICE_PREFIX, *(NOTICES[brand] for brand in sorted(brands) if brand in NOTICES)]


def feeds(stations: list[dict], capture_id: str) -> dict:
    """meta.json's `feeds`: one per chain in each country, keyed and sorted as
    club_gas.sitedata writes them. The sample's feeds all ran in its capture."""
    pairs = sorted({(station["country"], station["brand"]) for station in stations})
    return {
        f"{country}-{brand}": {
            "country": country,
            "brand": brand,
            "status": "ok",
            "last_success_capture_id": capture_id,
        }
        for country, brand in pairs
    }


def parse_price(raw: str) -> float:
    cleaned = raw.strip().replace("NT$", "").replace("$", "").replace("¥", "")
    cleaned = cleaned.replace("£", "").replace(",", "")
    if cleaned.startswith("."):
        cleaned = "0" + cleaned
    return float(cleaned)


def to_local_per_litre(price: float, unit: str) -> float:
    if unit == "USD/gal":
        return price / LITRES_PER_GALLON
    if unit == "GBp/L":
        return price / 100.0
    return price


def build(out_dir: Path, *, days: int = 14, end: date = date(2026, 9, 15)) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dates = [end - timedelta(days=days - 1 - i) for i in range(days)]
    captured = datetime(end.year, end.month, end.day, 18, 17, tzinfo=UTC)
    captured_iso = captured.strftime("%Y-%m-%dT%H:%M:%SZ")

    history_rows: list[dict] = []
    latest: list[dict] = []
    stations: list[dict] = []

    for index, station in enumerate(STATIONS):
        fx_usd_per_unit = 1.0 / UNITS_PER_USD[station["currency"]]
        common = {
            key: station.get(key)
            for key in (
                "station_key",
                "country",
                "brand",
                "name",
                "name_local",
                "address",
                "city",
                "region",
                "postcode",
                "lat",
                "lon",
            )
        }
        entry = dict(
            common,
            status="active",
            first_seen_utc="2026-09-01T06:17:00Z",
            captured_at_utc=captured_iso,
            grades={},
            other={},
        )
        stations.append(
            dict(
                common,
                status="active",
                first_seen_utc="2026-09-01T06:17:00Z",
                last_seen_utc=captured_iso,
                superseded_by=None,
                alt_id=station.get("alt_id"),
                source_station_id=station["station_key"].split("-", 1)[1],
            )
        )

        for offset, (grade_raw, grade, price_raw) in enumerate(station["prices"]):
            price = parse_price(price_raw)
            local = to_local_per_litre(price, station["price_unit"])
            usd = local * fx_usd_per_unit
            payload = {
                "grade_raw": grade_raw,
                "price_raw": price_raw,
                "price": price,
                "price_unit": station["price_unit"],
                "currency": station["currency"],
                "price_local_per_litre": round(local, 4),
                "price_usd_per_litre": round(usd, 4),
                "price_usd_per_gallon": round(
                    price if station["price_unit"] == "USD/gal" else usd * LITRES_PER_GALLON, 4
                ),
                "fx_usd_per_unit": float(f"{fx_usd_per_unit:.10g}"),
                "fx_rate_date": FX_RATE_DATE,
                "fx_source": "identity" if station["currency"] == "USD" else "frankfurter-v2",
            }
            if grade == "other":
                entry["other"][grade_raw] = payload
                continue
            entry["grades"][grade] = payload
            for day_index, day in enumerate(dates):
                # A sine wave made every single day a change, which is exactly
                # the question the change flags exist to answer. Prices hold for
                # a few days and then step, which is how fuel prices move.
                step = (day_index + index + offset) // (4 + (index % 3))
                per_litre = local * (1.0 + 0.008 * ((step % 5) - 2))
                history_rows.append(
                    {
                        "capture_date": day,
                        "station_key": station["station_key"],
                        "country": station["country"],
                        "brand": station["brand"],
                        "region": station["region"],
                        "grade": grade,
                        "price_local_per_litre": round(per_litre, 4),
                        "price_usd_per_litre": round(per_litre * fx_usd_per_unit, 4),
                        "currency": station["currency"],
                        "n_captures": 4,
                    }
                )
        latest.append(entry)

    history = pl.DataFrame(history_rows).sort(["station_key", "grade", "capture_date"])
    # The same flags sitedata derives, from the same helper, so the preview and
    # the real build cannot drift apart.
    history = with_change_flags(
        history.with_columns(
            pl.col("price_local_per_litre").alias("price_min"),
            pl.col("price_local_per_litre").alias("price_max"),
        )
    )
    history.select(
        [
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
    ).write_parquet(out_dir / "history.parquet", row_group_size=20000, statistics=True)

    aggs = [
        pl.len().cast(pl.Int32).alias("n_stations"),
        pl.col("price_usd_per_litre").drop_nulls().len().cast(pl.Int32).alias("n_stations_usd"),
        pl.col("price_local_per_litre").median().alias("median_local_per_litre"),
        pl.col("price_local_per_litre").quantile(0.25, "linear").alias("p25_local_per_litre"),
        pl.col("price_local_per_litre").quantile(0.75, "linear").alias("p75_local_per_litre"),
        pl.col("price_usd_per_litre").median().alias("median_usd_per_litre"),
        pl.col("price_usd_per_litre").quantile(0.25, "linear").alias("p25_usd_per_litre"),
        pl.col("price_usd_per_litre").quantile(0.75, "linear").alias("p75_usd_per_litre"),
    ]
    columns = [
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
    by_country = (
        history.group_by(["capture_date", "country", "brand", "grade"])
        .agg(aggs)
        .with_columns(level=pl.lit("country"), region=pl.lit(None, dtype=pl.String))
    )
    by_region = (
        history.filter(pl.col("region").is_not_null())
        .group_by(["capture_date", "country", "brand", "region", "grade"])
        .agg(aggs)
        .with_columns(level=pl.lit("region"))
    )
    summary = pl.concat([by_country.select(columns), by_region.select(columns)]).sort(
        ["country", "brand", "level", "region", "grade", "capture_date"]
    )
    summary.write_parquet(out_dir / "summary_daily.parquet", statistics=True)

    # The shape club_gas.sitedata._meta writes, key for key and in its order.
    capture_id = "2026-09-15T1817Z"
    brands = {station["brand"] for station in STATIONS}
    meta = {
        "built_at_utc": "2026-09-15T18:30:00Z",
        "capture_id": capture_id,
        "countries": {
            country: {"status": "ok", "last_success_capture_id": capture_id}
            for country in sorted({station["country"] for station in STATIONS})
        },
        "feeds": feeds(STATIONS, capture_id),
        "closed_months": [],
        "closed_years": [],
        "grades": grade_table(brands),
        "notice": notice(brands),
        # The page calls a country, or a chain in it, stale after this many hours
        # without a capture.
        "stale_after_hours": 12,
        "releases": {
            "current": ("https://github.com/coatless-data/club-gas-prices/releases/tag/current"),
            "all": "https://github.com/coatless-data/club-gas-prices/releases",
            # sitedata also emits one download URL per `current` asset; the page links
            # only `current` and `all`, but the fixture carries them so it stays
            # substitutable for real meta.json.
            "all_parquet": RELEASE_DL + "club-gas-all.parquet",
            "all_csv_gz": RELEASE_DL + "club-gas-all.csv.gz",
            "all_captures_parquet": RELEASE_DL + "club-gas-all-captures.parquet",
            "latest_csv": RELEASE_DL + "club-gas-latest.csv",
            "stations_csv": RELEASE_DL + "stations.csv",
            "fx_csv": RELEASE_DL + "fx.csv",
        },
        "basemap": {
            "provider": "osm",
            "light_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "dark_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "subdomains": "",
            "max_zoom": 19,
            "dark_filter": True,
            "attribution": (
                '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
            ),
        },
    }
    for name, payload in (
        ("latest.json", latest),
        ("stations.json", stations),
        ("meta.json", meta),
    ):
        (out_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "site/data")
    build(target)
    print(f"wrote sample site data to {target}")
