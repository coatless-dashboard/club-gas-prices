"""Browser smoke test for the rendered dashboard.  CI only.

    uv run python tests/smoke/smoke_site.py _site

It serves the staged site with `range_server.py` (DuckDB-WASM reads Parquet by
byte range), drives the page with Playwright, and exits non-zero on any failure.
Playwright is imported inside `main()` so the module, and the pure helpers below,
import without the CI-only `smoke` dependency group.
"""

from __future__ import annotations

import argparse
import base64
import itertools
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from range_server import serve  # noqa: E402

PREFIX = "/club-gas-prices/"
# One document per view now, so a page is a URL rather than a tab pane.
PAGES = ["index.html", "compare.html", "trends.html", "changes.html", "station.html", "about.html"]
# The pages that query history.parquet. It is fetched whole and grows without
# bound, so every other page has to leave it alone.
HISTORY_PAGES = {"changes.html", "station.html"}
# A phone: the width the audit found About scrolling sideways at, and Compare's
# labels cut off.
PHONE = {"width": 390, "height": 844}
# WCAG 2.1 AA for text below 18pt, or 14pt bold: the cluster counts are both.
AA_TEXT = 4.5
GRADES = ["regular", "premium", "diesel"]
CURRENCIES = ["USD", "Local"]
VOLUMES = ["gal", "litre"]
IDLE_TIMEOUT_MS = 60_000

# The marks Observable Plot draws only when the page has rows: one dot per
# country on Compare's median chart, and on Trends one line per country --
# or, until a second day has been captured, one dot, because a single reading
# per country is a point and the page draws it as one. Either answers the
# question this gate asks, which is whether the card drew the data at all.
COMPARE_DOTS = 'svg g[aria-label="dot"] circle'
TREND_MARKS = 'svg g[aria-label="line"] path, svg g[aria-label="dot"] circle'

# Every linear line and band of every Plot chart on the page, for the two-chain
# checks. Plot marks its own SVGs with a `plot-` class, which the chain key's
# small swatches do not carry.
PLOT_PATHS_JS = """() => [...document.querySelectorAll("svg")]
  .filter((svg) => (svg.getAttribute("class") || "").startsWith("plot"))
  .flatMap((svg) => ["line", "area"].flatMap((kind) =>
    [...svg.querySelectorAll(`g[aria-label="${kind}"] path`)].map((path) => ({
      chart: svg.getAttribute("aria-label") || "",
      kind,
      d: path.getAttribute("d") || ""
    }))))"""

# Each Changes chart's row labels against the rows that have any cell at all.
CHANGES_ROWS_JS = """() => [...document.querySelectorAll("svg")]
  .filter((svg) => (svg.getAttribute("aria-label") || "").includes("by station"))
  .map((svg) => ({
    chart: svg.getAttribute("aria-label"),
    ticks: svg.querySelectorAll('g[aria-label="y-axis tick label"] text').length,
    rows: new Set([...svg.querySelectorAll('g[aria-label="cell"] rect')]
      .map((rect) => rect.getAttribute("y"))).size
  }))"""

# Text a chart's own SVG cuts off. Plot's SVG hides whatever falls outside it,
# so a label past an edge is simply not drawn: "nited Kingdom" and "596 stati"
# on Compare, the month under every day on the Trends strip, the bottom of every
# date on Changes. A chain's name is what made a row label long. The tip is left
# out; it is only drawn under the pointer.
CLIPPED_TEXT_JS = """() => [...document.querySelectorAll("svg")]
  .filter((svg) => (svg.getAttribute("class") || "").startsWith("plot"))
  .flatMap((svg) => {
    const box = svg.getBoundingClientRect();
    if (!box.width) return [];
    return [...svg.querySelectorAll("text")]
      .filter((text) => !text.closest('g[aria-label="tip"]'))
      .filter((text) => {
        const r = text.getBoundingClientRect();
        return r.width > 0 && (r.left < box.left - 1 || r.right > box.right + 1
          || r.top < box.top - 1 || r.bottom > box.bottom + 1);
      })
      .map((text) => `${svg.getAttribute("aria-label") || ""}: ${text.textContent}`);
  })"""

# Charts drawn wider than the box that shows them, and so scaled down to fit it,
# type and all. OJS's `width` measured the window rather than the page, and at
# 1440px every chart came out at 78% of its size.
SHRUNK_CHARTS_JS = """() => [...document.querySelectorAll("svg")]
  .filter((svg) => (svg.getAttribute("class") || "").startsWith("plot"))
  .map((svg) => ({
    chart: svg.getAttribute("aria-label") || "",
    drawn: Number(svg.getAttribute("width")),
    shown: svg.getBoundingClientRect().width
  }))
  .filter((c) => c.shown > 0 && c.drawn > 0 && c.shown < 0.97 * c.drawn)
  .map((c) => `${c.chart} (${Math.round(c.shown)} of ${c.drawn}px)`)"""

# How far the page reaches past the viewport, and the outermost elements that
# take it there. Anything inside a box that clips or scrolls its own overflow --
# the map, a wide table, the row of country buttons -- stays where it is and is
# not the page's problem.
OVERFLOW_JS = """() => {
  const doc = document.documentElement;
  const edge = doc.clientWidth;
  const outside = (el) => {
    const r = el.getBoundingClientRect();
    return (r.width > 0 || r.height > 0) && (r.right > edge + 1 || r.left < -1);
  };
  const clipped = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      if (getComputedStyle(p).overflowX !== "visible") return true;
    }
    return false;
  };
  const wide = [...document.body.querySelectorAll("*")]
    .filter((el) => outside(el) && !clipped(el)
      && !(el.parentElement && el.parentElement !== document.body && outside(el.parentElement)))
    .slice(0, 5)
    .map((el) => {
      const r = el.getBoundingClientRect();
      const name = el.getAttribute("class") || "";
      return `${el.tagName.toLowerCase()}.${name} [${Math.round(r.left)}, ${Math.round(r.right)}]`;
    });
  return {scroll: doc.scrollWidth, client: edge, wide};
}"""

# Captions set anywhere but over the start of their own options. Below 30em
# Observable centers a form's items, so on a phone every caption sat centered.
OFF_CENTER_CAPTIONS_JS = """() => [...document.querySelectorAll(
    ".cgp-field:not(.cgp-field--inline) > label")]
  .filter((label) => label.getBoundingClientRect().width > 1)
  .filter((label) => Math.abs(label.getBoundingClientRect().left
    - label.parentElement.getBoundingClientRect().left) > 1)
  .map((label) => label.textContent.trim())"""

# How far the station search's result count reaches past the search card: its
# box had an 18rem floor, and on a phone the count ran off the page.
SEARCH_COUNT_JS = """() => {
  const card = document.querySelector(".cgp-search");
  const count = card && card.querySelector("output");
  if (!count) return null;
  return count.getBoundingClientRect().right - card.getBoundingClientRect().right;
}"""

# What every visible cluster badge draws its count in, and on.
CLUSTER_COLORS_JS = """() => [...document.querySelectorAll(".cgp-cluster span")]
  .filter((span) => span.getBoundingClientRect().width > 0)
  .map((span) => {
    const style = getComputedStyle(span);
    return {count: span.textContent.trim(), ink: style.color, fill: style.backgroundColor};
  })"""

# The Changes key: its words and swatches, and every color a cell is drawn in.
CHANGES_KEY_JS = """() => {
  const key = document.querySelector(".cgp-changes-key");
  return {
    text: key ? key.textContent.replace(/\\s+/g, " ").trim() : "",
    swatches: key
      ? [...key.querySelectorAll(".cgp-swatch")].map((s) => getComputedStyle(s).backgroundColor)
      : [],
    cells: [...new Set([...document.querySelectorAll('svg g[aria-label="cell"] rect')]
      .map((rect) => rect.getAttribute("fill")))]
  };
}"""

# The x-axis tick labels of every Plot chart. Plot writes a two-line label as
# two tspans, which are joined with a space so "12 AM" and "Sep 17" stay apart.
X_TICKS_JS = """() => [...document.querySelectorAll("svg")]
  .filter((svg) => (svg.getAttribute("class") || "").startsWith("plot"))
  .map((svg) => ({
    chart: svg.getAttribute("aria-label") || "",
    labels: [...svg.querySelectorAll('g[aria-label="x-axis tick label"] text')].map((text) => {
      const lines = [...text.querySelectorAll("tspan")].map((span) => span.textContent);
      return lines.length ? lines.join(" ") : text.textContent;
    })
  }))"""

# The About page's grade table: its header and each row's cells.
GRADE_TABLE_JS = """() => {
  const table = document.querySelector("table.cgp-table");
  if (!table) return null;
  return {
    head: [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim()),
    rows: [...table.querySelectorAll("tbody tr")].map((tr) =>
      [...tr.querySelectorAll("td")].map((td) => td.textContent.trim()))
  };
}"""

# The freshness notice at the top of every page, its whitespace folded.
FRESHNESS_JS = """() => {
  const el = document.querySelector(".cgp-controlbar .cgp-alert, .cgp-controlbar .cgp-notice");
  return el ? el.textContent.replace(/\\s+/g, " ").trim() : null;
}"""

# The chains' names as the page gives them, for the freshness cases' labels.
CHAIN_NAMES = {"COSTCO": "Costco", "SAMS": "Sam's Club"}
PRIMARY_CHAIN = "COSTCO"

# A tick label that names a time of day: "12 AM", "6 PM", "3:15".
TIME_OF_DAY = re.compile(r"\b\d{1,2}(?::\d{2})? ?[AP]M\b|\b\d{1,2}:\d{2}\b")

TILE_HOSTS = ("basemaps.cartocdn.com", "tile.openstreetmap.org")
FONT_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")
# The page's libraries come from these two, so they are never intercepted.
LIBRARY_HOSTS = ("cdn.jsdelivr.net", "cdn.observableusercontent.com")

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# Right after `load`, zero elements carry `.observablehq--running` simply because
# no OJS cell has ticked yet — the runtime hasn't started, not finished. Without
# this, `wait_idle`'s selector can resolve on its very first poll, before any
# cell (including the one that builds `.cgp-map`) has run at all. This counts a
# cell actually starting, so "no cells running" only counts once something has
# run. Installed via `add_init_script` so it is in place before the page's own
# scripts execute, on every navigation. It observes `document` itself, not
# `document.documentElement`: at the moment an init script runs, the document
# may not have a root element yet, and `document` (unlike `documentElement`) is
# always a valid Node to observe, with `subtree: true` covering elements added
# under it later.
TRACK_CELL_STARTS_SCRIPT = """
window.__cgpCellStarts = 0;
new MutationObserver((mutations) => {
  for (const mutation of mutations) {
    const el = mutation.target;
    if (el.classList && el.classList.contains("observablehq--running")) {
      window.__cgpCellStarts += 1;
    }
  }
}).observe(document, {
  attributes: true,
  attributeFilter: ["class"],
  subtree: true,
});
"""


def expected_marker_count(latest: list[dict], grade: str) -> int:
    """Stations that should get a marker: a position and an entry for `grade`."""
    total = 0
    for station in latest:
        if station.get("lat") is None or station.get("lon") is None:
            continue
        if (station.get("grades") or {}).get(grade):
            total += 1
    return total


def expected_usd_countries(latest: list[dict], grade: str) -> int:
    """Countries Compare and Trends should draw in USD for `grade`.

    Both pages read `summary_daily`, which the smoke test cannot query, but a
    country has a USD median there exactly when at least one of its stations
    carries a USD value for the grade -- which `latest.json` does record. A
    country with no rate for its currency has no USD value anywhere and is
    absent from both pages, so it must not be counted here either.
    """
    countries = set()
    for station in latest:
        entry = (station.get("grades") or {}).get(grade) or {}
        if entry.get("price_usd_per_litre") is not None:
            countries.add(station.get("country"))
    return len(countries)


def chains_by_country(latest: list[dict]) -> dict[str, set[str]]:
    """The chains each country has in `latest.json`."""
    out: dict[str, set[str]] = {}
    for station in latest:
        if station.get("brand"):
            out.setdefault(station.get("country"), set()).add(station["brand"])
    return out


def usd_series(latest: list[dict], grade: str, *, country: str | None = None) -> set[tuple]:
    """The series Compare draws one dot each for, in USD.

    Across countries a series is a (country, chain) pair; inside one country it
    is a (region, chain) pair. Either way it is never the place alone: a state
    both chains serve is two dots, not one.
    """
    series = set()
    for station in latest:
        entry = (station.get("grades") or {}).get(grade) or {}
        if entry.get("price_usd_per_litre") is None:
            continue
        if country is None:
            series.add((station.get("country"), station.get("brand")))
        elif station.get("country") == country and station.get("region"):
            series.add((station.get("region"), station.get("brand")))
    return series


def expected_latest_stations(summary_path: Path, grade: str) -> int:
    """Stations behind the newest day of the country-level summary, every series added.

    What the note under the all-countries Trends chart should say: the chart
    draws every chain in every country, so it stands on all of their stations,
    not on the largest series' alone. polars is imported here, as Playwright is
    in main(): it comes from the dev group, which both workflows sync alongside
    the smoke one.
    """
    import polars as pl

    rows = pl.read_parquet(summary_path).filter(
        (pl.col("level") == "country") & (pl.col("grade") == grade)
    )
    if rows.is_empty():
        return 0
    newest = rows["capture_date"].max()
    return int(rows.filter(pl.col("capture_date") == newest)["n_stations"].sum())


def fetches_history(urls: list[str]) -> bool:
    """Whether a page asked for history.parquet among `urls`."""
    return any(urlsplit(url).path.endswith("/data/history.parquet") for url in urls)


def capture_id_of(when: datetime) -> str:
    """A capture id as the collector writes one: `2026-09-18T1623Z`."""
    return when.strftime("%Y-%m-%dT%H%MZ")


def freshness_cases(meta: dict, stations: list[dict], now: datetime) -> list[tuple]:
    """meta.json as the collector writes it with `feeds` and without, and what
    the freshness notice has to say about each.

    One United States chain last succeeded 30 hours ago and everything else an
    hour ago. The country block takes the newest success across a country's
    feeds, so it reads fresh; per feed, the notice names the country, and the
    chain too wherever the United States has two. Without `feeds` the page
    reads the country block as it always has. A feed of a chain the data holds
    no station of is never named, however stale: the site names no chain it
    shows nothing of. The cases are built from the stations served, so a
    one-chain release and the two-chain sample each get the labels their own
    page would draw.
    """
    window = meta.get("stale_after_hours") or 12
    fresh = capture_id_of(now - timedelta(hours=1))
    stale = capture_id_of(now - timedelta(hours=30))
    pairs = sorted({(s["country"], s["brand"]) for s in stations if s.get("brand")})
    us_chains = sorted(
        {brand for country, brand in pairs if country == "US"},
        key=lambda brand: (brand != PRIMARY_CHAIN, brand),
    )
    late = us_chains[-1]
    absent = next(b for b in ("SAMS", "ELSEWHERE") if b not in {b for _, b in pairs})
    countries = {
        country: {"status": "ok", "last_success_capture_id": fresh}
        for country in sorted({country for country, _ in pairs})
    }

    def feed(country: str, brand: str, last: str | None, status: str = "ok") -> dict:
        return {
            "country": country,
            "brand": brand,
            "status": status,
            "last_success_capture_id": last,
        }

    feeds = {f"{c}-{b}": feed(c, b, stale if (c, b) == ("US", late) else fresh) for c, b in pairs}
    # A chain switched on in the collector that has yet to post a price.
    feeds[f"US-{absent}"] = feed("US", absent, None, status="failed")
    all_fresh = {**feeds, f"US-{late}": feed("US", late, fresh)}
    built = {**meta, "built_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    by_country = {key: value for key, value in built.items() if key != "feeds"}

    named = len(us_chains) > 1 or late != PRIMARY_CHAIN
    label = "United States" + (f" · {CHAIN_NAMES.get(late, late)}" if named else "")
    over = f"Last successful capture over {window} h ago: {{}} (30 h)."
    return [
        ("per feed", {**built, "countries": countries, "feeds": feeds}, over.format(label)),
        (
            "per country",
            {
                **by_country,
                "countries": {
                    **countries,
                    "US": {**countries["US"], "last_success_capture_id": stale},
                },
            },
            over.format("United States"),
        ),
        (
            "every feed fresh",
            {**built, "countries": countries, "feeds": all_fresh},
            f"Every country was captured in the last {window} hours.",
        ),
    ]


def path_points(d: str) -> list[tuple[float, float]] | None:
    """The vertices of a path drawn in straight segments, or None.

    Plot writes a linear line or area as M, L and Z commands. Anything else --
    the coverage strip's step curve -- returns None, because x repeats there by
    design and says nothing about how the series were keyed.
    """
    points = []
    for command, args in re.findall(r"([A-Za-z])([^A-Za-z]*)", d or ""):
        if command in "Zz":
            continue
        if command not in "ML":
            return None
        numbers = [float(n) for n in re.findall(r"-?\d*\.?\d+", args)]
        points.extend(zip(numbers[::2], numbers[1::2], strict=True))
    return points


def doubles_back(d: str) -> bool:
    """True when a line ever steps back or sideways in x.

    One series has one reading a day, so its line only moves right. Two chains
    keyed as one series alternate between their two prices on the same day,
    which is a vertical step: the sawtooth the audit found in every state that
    both chains serve.
    """
    points = path_points(d)
    if not points:
        return False
    return any(b[0] <= a[0] for a, b in itertools.pairwise(points))


def area_turns(d: str) -> int:
    """How often a band's outline reverses direction in x.

    A band for one series runs out along its upper edge and back along its
    lower one: one turn. Two series drawn as one band run out and back twice.
    """
    points = path_points(d)
    if not points:
        return 0
    turns, heading = 0, 0
    for a, b in itertools.pairwise(points):
        step = (b[0] > a[0]) - (b[0] < a[0])
        if step and heading and step != heading:
            turns += 1
        heading = step or heading
    return turns


def time_of_day_charts(charts: list[dict]) -> list[str]:
    """The charts whose date axis is ticked at times of day.

    Every series on the site holds one value per UTC day, so a tick between two
    days marks a reading nobody took. Given a tick count, Plot put them on any
    history short enough for the count to split a day: "12 AM Sep 17, 12 PM".
    """
    return sorted(
        chart["chart"]
        for chart in charts
        if any(TIME_OF_DAY.search(label) for label in chart["labels"])
    )


def tip_units(text: str) -> list[str]:
    """The unit each price in a tip is quoted in: "3.764 USD/gal" gives "USD"."""
    return re.findall(r"\d ([^\s\d/]+)/(?:gal|L)\b", text)


def currencies(latest: list[dict]) -> set[str]:
    """Every currency a station in `latest.json` posts a price in."""
    return {
        entry["currency"]
        for station in latest
        for entry in (station.get("grades") or {}).values()
        if entry.get("currency")
    }


def grade_table_problems(
    head: list[str], rows: list[list[str]], grades: list[dict], chains: set[str]
) -> list[str]:
    """What is wrong with the About page's grade table, given meta.json's rows.

    One row per grade, and a Chain column exactly when the rows say whose label
    each one is: without `brand` the column was a stack of blank cells that
    named nothing. A chain the table names must also be one the data holds,
    because the site names no chain it shows no prices for.
    """
    problems = []
    if len(rows) != len(grades):
        problems.append(f"{len(rows)} rows for {len(grades)} grades in meta.json")
    named = {row.get("brand") for row in grades if row.get("brand")}
    if "Chain" in head:
        column = head.index("Chain")
        blank = sum(1 for cells in rows if column >= len(cells) or not cells[column])
        if not named:
            problems.append("a Chain column, though no grade row says its chain")
        if blank:
            problems.append(f"{blank} rows with a blank Chain cell")
    elif named:
        problems.append("no Chain column, though the grade rows say their chain")
    if named - chains:
        problems.append(f"it names {sorted(named - chains)}, which have no stations here")
    return problems


def parse_color(text: str) -> tuple[int, int, int] | None:
    """A CSS color as the page gives it -- "#rgb", "#rrggbb" or "rgb(...)" -- as RGB.

    The chart writes its fills as the stylesheet's hex tokens and the browser
    reports computed colors as rgb(), so both have to land on one form before
    they can be compared. Alpha is ignored: nothing checked here is translucent.
    """
    value = (text or "").strip().lower()
    match = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", value)
    if match:
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        return tuple(int(digits[i : i + 2], 16) for i in (0, 2, 4))
    match = re.fullmatch(r"rgba?\(([^)]*)\)", value)
    if match:
        parts = re.findall(r"[\d.]+", match.group(1))
        if len(parts) >= 3:
            return tuple(round(float(p)) for p in parts[:3])
    return None


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """WCAG 2's contrast ratio between two RGB colors, from 1 to 21."""

    def luminance(rgb: tuple[int, int, int]) -> float:
        channels = []
        for value in rgb:
            c = value / 255
            channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
        red, green, blue = channels
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def unexplained_colors(cells: list[str], swatches: list[str]) -> list[str]:
    """The cell colors a key has no swatch for.

    A key that shows a different blue from the cells, or none of the gray,
    leaves the reader where no key at all did.
    """
    keyed = {parse_color(color) for color in swatches}
    return sorted({color for color in cells if parse_color(color) not in keyed})


def is_site_console_error(message_type: str, location_url: str, origin: str) -> bool:
    """True for console errors the page itself produced.

    Tile and font requests are stubbed and CDN scripts are out of our control,
    so only messages attributed to the local origin (or to no file at all) count.
    """
    if message_type != "error":
        return False
    if location_url.endswith("/favicon.ico"):
        # Chrome asks for a favicon the site does not ship; that 404 is noise.
        return False
    if not location_url:
        return True
    return location_url.startswith(origin)


class Failures:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, message: str) -> None:
        print(f"FAIL: {message}", flush=True)
        self.items.append(message)

    def raise_if_any(self) -> None:
        if self.items:
            raise SystemExit(f"{len(self.items)} smoke failure(s)")


def install_routes(page) -> None:
    def handler(route):
        url = route.request.url
        host = urlsplit(url).hostname or ""
        if any(host == h or host.endswith("." + h) for h in LIBRARY_HOSTS):
            route.continue_()
            return
        if any(host == h or host.endswith("." + h) for h in TILE_HOSTS):
            route.fulfill(status=200, content_type="image/png", body=PNG_1X1)
            return
        if any(host == h or host.endswith("." + h) for h in FONT_HOSTS) or url.endswith(
            (".woff", ".woff2", ".ttf")
        ):
            route.fulfill(status=204, body=b"")
            return
        route.continue_()

    page.route("**/*", handler)


def wait_idle(page, failures: Failures, step: str) -> None:
    try:
        page.wait_for_function(
            "() => window.__cgpCellStarts > 0 && "
            "document.querySelectorAll('.observablehq--running').length === 0",
            timeout=IDLE_TIMEOUT_MS,
        )
    except Exception as exc:
        failures.add(f"{step}: OJS cells still running after 60 s ({type(exc).__name__})")


def check_clean(page, failures: Failures, step: str, collected: dict) -> None:
    errors = page.locator(".observablehq--error")
    count = errors.count()
    if count:
        texts = [errors.nth(i).inner_text()[:300] for i in range(min(count, 5))]
        failures.add(f"{step}: {count} OJS error cell(s): {texts}")
    for message in collected["console"]:
        failures.add(f"{step}: console error: {message}")
    for message in collected["pageerror"]:
        failures.add(f"{step}: page error: {message}")
    collected["console"].clear()
    collected["pageerror"].clear()


def map_check(page, failures: Failures, step: str, latest: list[dict], grade: str) -> None:
    try:
        page.wait_for_selector(".cgp-map", timeout=IDLE_TIMEOUT_MS)
    except Exception as exc:
        failures.add(f"{step}: .cgp-map never appeared ({type(exc).__name__})")
        return
    container = page.locator(".cgp-map").first
    box = container.bounding_box()
    if box is None or box["width"] < 300 or box["height"] < 300:
        failures.add(f"{step}: map container is {box}, expected at least 300x300")
    raw = container.get_attribute("data-marker-count")
    drawn = int(raw) if raw and raw.lstrip("-").isdigit() else -1
    expected = expected_marker_count(latest, grade)
    if drawn < 0.9 * expected:
        failures.add(
            f"{step}: data-marker-count={drawn}, expected at least 90% of {expected} "
            f"{grade} stations"
        )
    else:
        print(f"{step}: map ok, {drawn} markers of {expected} {grade} stations", flush=True)


def plot_check(
    page, failures: Failures, step: str, selector: str, expected: int, what: str
) -> None:
    """Assert a page drew its data, not just a card with a heading in it.

    `selector` names a mark Observable Plot only emits for real rows, so an
    empty page -- no data, a cell that threw, or output that never reached the
    card it belongs to -- cannot pass. What is waited on is a mark with a
    non-empty box, not merely one in the DOM: a chart Quarto's dashboard
    autosizing measured against a hidden container has every mark the data
    calls for, inside an SVG zero pixels wide.

    ANY match having a box is enough, which `wait_for_selector(state="visible")`
    cannot express -- it judges the first match alone. On one day of history
    each line is a single point, drawn as a real `path` whose `d` is `M x,y Z`
    and whose box is 0x0, so the first match is permanently invisible while the
    dots right after it are on screen. `wait_idle` has already seen every OJS
    cell finish and this waits up to 60 s more, so the count below cannot race
    the render. Like the Map check it allows 90%, because a country whose
    currency has no rate that day legitimately has no USD value to draw.
    """
    try:
        page.wait_for_function(
            """(sel) => [...document.querySelectorAll(sel)].some((el) => {
                 const box = el.getBoundingClientRect();
                 return box.width > 0 || box.height > 0;
               })""",
            arg=selector,
            timeout=IDLE_TIMEOUT_MS,
        )
    except Exception as exc:
        failures.add(f"{step}: no {what} were drawn ({type(exc).__name__}): {selector}")
        return
    drawn = page.locator(selector).count()
    if drawn < 0.9 * expected:
        failures.add(f"{step}: {drawn} {what}, expected at least 90% of {expected}")
    else:
        print(f"{step}: ok, {drawn} {what} of {expected} countries", flush=True)


def set_control(page, name: str, value: str) -> None:
    page.locator(f'[data-control="{name}"] input[data-value="{value}"]').first.check()


def choose(page, name: str, value: str) -> None:
    """Pick `value` in a select the site built with selectControl."""
    option = page.locator(f'[data-control="{name}"] option[data-value="{value}"]').first
    page.locator(f'[data-control="{name}"] select').first.select_option(
        option.get_attribute("value")
    )


def note_check(page, failures: Failures, step: str, expected: int) -> None:
    """The note under an all-countries chart counts every series' stations."""
    notes = page.locator(".cgp-notice", has_text="stations on the latest day")
    if not notes.count():
        failures.add(f"{step}: no note says how many stations the chart stands on")
        return
    text = notes.first.inner_text()
    match = re.search(r"(\d[\d,]*) stations on the latest day", text)
    said = int(match.group(1).replace(",", "")) if match else -1
    if said != expected:
        failures.add(f"{step}: the note counts {said} stations on the latest day, not {expected}")
    else:
        print(f"{step}: note counts all {expected} stations on the latest day", flush=True)


def series_check(page, failures: Failures, step: str) -> None:
    """Every line and band on the page is one series, never two chains joined."""
    paths = page.evaluate(PLOT_PATHS_JS)
    joined = sorted(
        {
            path["chart"]
            for path in paths
            if (path["kind"] == "line" and doubles_back(path["d"]))
            or (path["kind"] == "area" and area_turns(path["d"]) > 1)
        }
    )
    if not any(path["kind"] == "line" for path in paths):
        failures.add(f"{step}: no lines were drawn")
    elif joined:
        failures.add(f"{step}: a line or band joins two series in {joined}")
    else:
        print(f"{step}: ok, {len(paths)} lines and bands, each one series", flush=True)


def day_ticks_check(page, failures: Failures, step: str) -> None:
    """Every chart on the page with a date axis ticks it in days."""
    charts = page.evaluate(X_TICKS_JS)
    hourly = time_of_day_charts(charts)
    if not charts:
        failures.add(f"{step}: no charts to read the ticks of")
    elif hourly:
        failures.add(f"{step}: ticks at times of day in {hourly}")
    else:
        print(f"{step}: ok, {len(charts)} charts ticked in days", flush=True)


def change_tip_check(page, failures: Failures, step: str, known: set[str]) -> None:
    """In its own currency, the change view quotes each price in that currency.

    The tip looked the currency up by the series key, a country and a chain, in
    a map keyed on the country alone, so every tip said "local".
    """
    spot = page.evaluate(
        """() => {
          const svg = [...document.querySelectorAll("svg")]
            .find((s) => s.getBoundingClientRect().width > 100
                         && s.querySelector("g[aria-label='dot'] circle"));
          if (!svg) return null;
          const dots = [...svg.querySelectorAll("g[aria-label='dot'] circle")];
          const dot = dots[Math.floor(dots.length / 2)];
          dot.scrollIntoView({block: "center"});
          const r = dot.getBoundingClientRect();
          return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        }"""
    )
    if not spot:
        failures.add(f"{step}: no change chart to hover")
        return
    page.mouse.move(spot["x"], spot["y"])
    page.wait_for_timeout(700)
    text = page.evaluate(
        """() => [...document.querySelectorAll("g[aria-label='tip']")]
             .map((g) => g.textContent.trim()).filter(Boolean).join(" ")"""
    )
    units = tip_units(text)
    if not units:
        failures.add(f"{step}: the change tip quotes no price: {text[:120]!r}")
    elif not set(units) <= known:
        failures.add(f"{step}: the change tip quotes prices in {sorted(set(units))}")
    else:
        print(f"{step}: ok, the change tip quotes prices in {units[0]}", flush=True)


def grade_table_check(page, failures: Failures, step: str, meta: dict, latest: list[dict]):
    table = page.evaluate(GRADE_TABLE_JS)
    if table is None:
        failures.add(f"{step}: no grade table")
        return
    chains = {station.get("brand") for station in latest if station.get("brand")}
    problems = grade_table_problems(table["head"], table["rows"], meta.get("grades") or [], chains)
    for problem in problems:
        failures.add(f"{step}: grade table: {problem}")
    if not problems:
        print(f"{step}: ok, grade table has {len(table['rows'])} rows", flush=True)


def chart_text_check(page, failures: Failures, step: str) -> None:
    """Every label on every chart is drawn whole."""
    clipped = page.evaluate(CLIPPED_TEXT_JS)
    if clipped:
        failures.add(f"{step}: chart text cut off at the chart's edge: {clipped[:8]}")
    else:
        print(f"{step}: ok, every chart label drawn whole", flush=True)


def chart_scale_check(page, failures: Failures, step: str) -> None:
    """Every chart is drawn at the width it is shown at, so its type is full size."""
    shrunk = page.evaluate(SHRUNK_CHARTS_JS)
    if shrunk:
        failures.add(f"{step}: charts drawn wider than the page and shrunk to fit it: {shrunk}")


def overflow_check(page, failures: Failures, step: str) -> None:
    """The page does not scroll sideways."""
    info = page.evaluate(OVERFLOW_JS)
    if info["scroll"] > info["client"] + 1:
        failures.add(
            f"{step}: the page scrolls sideways, {info['scroll']}px wide in a "
            f"{info['client']}px viewport: {info['wide']}"
        )
    else:
        print(f"{step}: ok, no sideways scroll at {info['client']}px", flush=True)


def cluster_contrast_check(page, failures: Failures, step: str) -> None:
    """Every cluster count on the map meets AA against its own fill.

    White on every step was 1.7:1 on the amber most groups land on.
    """
    theme = (
        "dark"
        if page.evaluate("() => document.body.classList.contains('quarto-dark')")
        else "light"
    )
    badges = page.evaluate(CLUSTER_COLORS_JS)
    if not badges:
        failures.add(f"{step}: no cluster on the {theme} map to read the count of")
        return
    faint = []
    for badge in badges:
        ink, fill = parse_color(badge["ink"]), parse_color(badge["fill"])
        ratio = contrast_ratio(ink, fill) if ink and fill else 0.0
        if ratio < AA_TEXT:
            faint.append(f"{badge['count']}: {badge['ink']} on {badge['fill']}, {ratio:.2f}:1")
    if faint:
        failures.add(f"{step}: {theme} cluster counts below {AA_TEXT}:1: {faint[:4]}")
    else:
        print(f"{step}: ok, {len(badges)} {theme} cluster counts meet AA", flush=True)


def changes_key_check(page, failures: Failures, step: str) -> None:
    """The heatmap says what its colors and its gray mean, in the colors it uses."""
    key = page.evaluate(CHANGES_KEY_JS)
    missing = unexplained_colors(key["cells"], key["swatches"])
    if not key["cells"]:
        failures.add(f"{step}: no heatmap cells to explain")
    elif not key["swatches"]:
        failures.add(f"{step}: the heatmap has no key")
    elif missing:
        failures.add(f"{step}: the key has no swatch for the cell colors {missing}")
    elif "fewer than 3 readings" not in key["text"]:
        failures.add(f"{step}: the key does not say what the gray means: {key['text']!r}")
    else:
        print(f"{step}: ok, the key explains all {len(key['cells'])} cell colors", flush=True)


def two_chain_steps(page, base_url: str, latest: list[dict], failures: Failures, collected):
    """Two chains in one country are two series on every page.

    Only a release with a second chain can show the difference, so a Costco-only
    one skips this. The Test workflow renders the two-chain sample, where it
    always runs.
    """
    chains = chains_by_country(latest)
    shared = sorted(code for code, brands in chains.items() if len(brands) > 1)

    # Compare: a dot per chain in each country, and per chain in each region.
    page.goto(base_url + "compare.html", wait_until="load")
    wait_idle(page, failures, "step 5 compare")
    expected = len(usd_series(latest, "regular"))
    drawn = page.locator(COMPARE_DOTS).count()
    if drawn < expected:
        failures.add(f"step 5 compare: {drawn} dots for {expected} chains across countries")
    else:
        print(f"step 5 compare: ok, a dot for each of {expected} chains", flush=True)
    chart_text_check(page, failures, "step 5 compare")
    for code in shared:
        page.goto(base_url + "compare.html", wait_until="load")
        wait_idle(page, failures, f"step 5 compare {code}")
        choose(page, "breakdown", code)
        page.wait_for_selector('svg[aria-label*="by region"]', timeout=IDLE_TIMEOUT_MS)
        wait_idle(page, failures, f"step 5 compare {code}")
        expected = len(usd_series(latest, "regular", country=code))
        drawn = page.locator(COMPARE_DOTS).count()
        if drawn < expected:
            failures.add(f"step 5 compare {code}: {drawn} dots for {expected} region chains")
        else:
            print(f"step 5 compare {code}: ok, a dot for each of {expected} chains", flush=True)
        chart_text_check(page, failures, f"step 5 compare {code}")
    check_clean(page, failures, "step 5 compare", collected)

    # Trends: a region both chains serve is two lines, not one sawtooth.
    for code in shared:
        page.goto(base_url + "trends.html", wait_until="load")
        wait_idle(page, failures, f"step 5 trends {code}")
        choose(page, "breakdown", code)
        page.wait_for_selector('svg[aria-label*="by region"]', timeout=IDLE_TIMEOUT_MS)
        wait_idle(page, failures, f"step 5 trends {code}")
        series_check(page, failures, f"step 5 trends {code}")
    # The small multiples band each chain on its own.
    page.goto(base_url + "trends.html?view=separate&cur=Local", wait_until="load")
    wait_idle(page, failures, "step 5 trends separate")
    series_check(page, failures, "step 5 trends separate")
    check_clean(page, failures, "step 5 trends", collected)

    # A region both chains serve: its station's median is its own chain's, and
    # the Changes drill-down gives each chain only its own stations.
    regions: dict[tuple, set] = {}
    for station in latest:
        if station.get("country") in shared and station.get("region"):
            key = (station["country"], station["region"])
            regions.setdefault(key, set()).add(station.get("brand"))
    both = sorted(key for key, brands in regions.items() if len(brands) > 1)
    if not both:
        print("step 5: no region has two chains, so the station and drill-down checks skip")
        return
    country, region = both[0]
    station = next(s for s in latest if s["country"] == country and s["region"] == region)
    page.goto(
        f"{base_url}station.html?station={quote(station['station_key'], safe='')}",
        wait_until="load",
    )
    wait_idle(page, failures, "step 5 station")
    series_check(page, failures, f"step 5 station {station['station_key']}")
    check_clean(page, failures, "step 5 station", collected)
    if country != "US":
        return  # The Changes page covers US states only.
    page.goto(base_url + "changes.html", wait_until="load")
    wait_idle(page, failures, "step 5 changes")
    page.locator("form", has_text="Drill into").locator("select").select_option(label=region)
    page.wait_for_selector('svg[aria-label*="by station"]', timeout=IDLE_TIMEOUT_MS)
    wait_idle(page, failures, f"step 5 changes {region}")
    charts = page.evaluate(CHANGES_ROWS_JS)
    blank = [chart["chart"] for chart in charts if chart["ticks"] != chart["rows"]]
    if len(charts) < 2:
        failures.add(f"step 5 changes {region}: {len(charts)} chart(s) for two chains")
    elif blank:
        failures.add(f"step 5 changes {region}: rows with no readings in {blank}")
    else:
        print(f"step 5 changes {region}: ok, each chain lists only its stations", flush=True)
    chart_text_check(page, failures, f"step 5 changes {region}")
    check_clean(page, failures, "step 5 changes", collected)


def open_page(browser, viewport: dict, collected: dict, origin: str, *, scheme: str = "light"):
    """A page at `viewport` that counts cell starts, stubs tiles and fonts, and
    collects the site's own console errors into `collected`. `scheme` is the
    color scheme the browser asks for, which the site follows."""
    page = browser.new_page(viewport=viewport, color_scheme=scheme)
    page.add_init_script(TRACK_CELL_STARTS_SCRIPT)
    page.on(
        "console",
        lambda m: (
            collected["console"].append(f"{m.text[:400]} @ {m.location.get('url', '')}")
            if is_site_console_error(m.type, m.location.get("url", ""), origin)
            else None
        ),
    )
    page.on("pageerror", lambda e: collected["pageerror"].append(str(e)[:400]))
    install_routes(page)
    return page


def history_fetch_check(page_name: str, urls: list[str], failures: Failures, step: str) -> None:
    """Only the pages that query history.parquet download it."""
    wanted = page_name in HISTORY_PAGES
    if fetches_history(urls) != wanted:
        verb = "never downloaded" if wanted else "downloaded"
        failures.add(f"{step}: {verb} history.parquet")
    else:
        print(f"{step}: ok, history.parquet {'loaded' if wanted else 'left alone'}", flush=True)


def serving(body: str):
    """A route handler that answers with `body` as JSON. Playwright passes a
    handler the request as well when it takes a second argument, so this one
    takes only the route."""
    return lambda route: route.fulfill(status=200, content_type="application/json", body=body)


def freshness_steps(browser, base_url: str, meta: dict, stations: list[dict], failures, collected):
    """The freshness notice, read off meta.json with feeds and without.

    The release's own meta.json says whatever its capture says, so each case
    serves a meta.json of its own in its place and reads the notice back.
    """
    origin = base_url[: base_url.index(PREFIX)]
    for name, served, expected in freshness_cases(meta, stations, datetime.now(UTC)):
        step = f"step 7 freshness, {name}"
        page = open_page(browser, {"width": 1400, "height": 1000}, collected, origin)
        page.route("**/data/meta.json", serving(json.dumps(served)))
        page.goto(base_url + "about.html", wait_until="load")
        wait_idle(page, failures, step)
        said = page.evaluate(FRESHNESS_JS)
        if said != expected:
            failures.add(f"{step}: the notice reads {said!r}, not {expected!r}")
        else:
            print(f"{step}: ok, {said!r}", flush=True)
        check_clean(page, failures, step, collected)
        page.close()


def phone_steps(page, base_url: str, first_key: str, failures: Failures, collected) -> None:
    """Every page at a phone's width: nothing scrolls sideways, every chart label
    is drawn whole and every caption sits over its options. Most of the map is
    on the first screen, and a link to a station opens on that station rather
    than on a list of forty others."""
    views = [
        *PAGES,
        "compare.html?cur=Local",
        "trends.html?view=change&cur=Local",
        "trends.html?view=separate&cur=Local",
        f"station.html?station={quote(first_key, safe='')}",
    ]
    for view in views:
        step = f"step 6 phone {view}"
        page.goto(base_url + view, wait_until="load")
        wait_idle(page, failures, step)
        overflow_check(page, failures, step)
        chart_text_check(page, failures, step)
        off_center = page.evaluate(OFF_CENTER_CAPTIONS_JS)
        if off_center:
            failures.add(f"{step}: captions not over the start of their options: {off_center}")
        past = page.evaluate(SEARCH_COUNT_JS)
        if past is not None and past > 1:
            failures.add(f"{step}: the search count runs {past:.0f}px past the page")
        if view == "compare.html":
            # One country by region has row labels and an axis label of its own.
            choose(page, "breakdown", "US")
            page.wait_for_selector('svg[aria-label*="by region"]', timeout=IDLE_TIMEOUT_MS)
            wait_idle(page, failures, f"{step} US")
            chart_text_check(page, failures, f"{step} US")
        if view == "index.html":
            box = page.locator(".cgp-map").first.bounding_box()
            shown = min(box["y"] + box["height"], PHONE["height"]) - box["y"] if box else 0
            # The controls above it once left 226px of the map on the first screen.
            if shown < 300:
                failures.add(f"{step}: {shown:.0f}px of the map on the first screen, not 300")
            else:
                print(f"{step}: ok, {shown:.0f}px of the map on the first screen", flush=True)
        if view.startswith("station.html?"):
            box = page.locator(".cgp-station-chart").first.bounding_box()
            if not box or box["y"] + 200 > PHONE["height"]:
                failures.add(f"{step}: the station's chart starts below the first screen ({box})")
            else:
                print(f"{step}: ok, the station's chart is on the first screen", flush=True)
        check_clean(page, failures, step, collected)


def working(browser):
    """`browser`, once it has opened a page and run a script in it.

    A browser can start and still be unable to do either, and a gate that
    trusted it would fail every page instead of falling back.
    """
    try:
        page = browser.new_page()
        if page.evaluate("1 + 1") != 2:
            raise RuntimeError("the browser could not run a script")
        page.close()
    except Exception:
        browser.close()
        raise
    return browser


def launch_browser(playwright, *, headed: bool = False):
    """The runner image's Google Chrome, or Playwright's own Chromium without it.

    A runner-image roll is what takes Chrome away or breaks it, and a new image
    is also where `--with-deps` is likeliest to fail: it installs system packages
    by name from a list per Ubuntu release, and on a release newer than this
    Playwright knows, one renamed package fails the whole apt install. The image
    has shipped a browser, and with it most of what Chromium links against, so
    the browser alone is worth a try before the gate gives up.
    """
    try:
        return working(playwright.chromium.launch(channel="chrome", headless=not headed))
    except Exception as exc:
        print(f"::notice::Google Chrome unavailable ({exc}); installing Playwright chromium")
    install = [sys.executable, "-m", "playwright", "install"]
    try:
        subprocess.run([*install, "--with-deps", "chromium"], check=True)
    except subprocess.CalledProcessError as exc:
        print(f"::warning::Chromium's system packages did not install ({exc}); trying without them")
        subprocess.run([*install, "chromium"], check=True)
    return playwright.chromium.launch(headless=not headed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Dashboard smoke test")
    parser.add_argument("site", nargs="?", default="_site", help="staged site directory")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    args = parser.parse_args()

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    site_dir = Path(args.site).resolve()
    latest = json.loads((site_dir / "data" / "latest.json").read_text(encoding="utf-8"))
    if not latest:
        raise SystemExit(f"{site_dir}/data/latest.json has no stations")
    meta = json.loads((site_dir / "data" / "meta.json").read_text(encoding="utf-8"))
    stations = json.loads((site_dir / "data" / "stations.json").read_text(encoding="utf-8"))
    first_key = latest[0]["station_key"]

    # Served the way Pages serves: gzipped, with ranges over the compressed
    # bytes. Plain bytes let a site that could not read its own Parquet in
    # production pass this test every time.
    httpd, base_url = serve(site_dir, PREFIX, emulate_pages=True)
    origin = base_url[: base_url.index(PREFIX)]
    failures = Failures()
    collected = {"console": [], "pageerror": []}

    try:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright, headed=args.headed)
            page = open_page(browser, {"width": 1400, "height": 1000}, collected, origin)

            # Step 1: load, assert the page ids, check the Map with the defaults.
            page.goto(base_url + "index.html", wait_until="load")
            wait_idle(page, failures, "step 1 load")
            linked = page.evaluate(
                """() => [...document.querySelectorAll("nav a[href]")]
                     .map((a) => new URL(a.href, location.href).pathname.split("/").pop())
                     // Quarto writes the current page's own link as "./", which
                     // resolves to the directory rather than to index.html.
                     .map((name) => (name === "" ? "index.html" : name))"""
            )
            for name in PAGES:
                if name not in linked:
                    failures.add(f"step 1: the navbar does not link {name} (has {linked})")
            map_check(page, failures, "step 1", latest, "regular")
            cluster_contrast_check(page, failures, "step 1")
            check_clean(page, failures, "step 1", collected)
            # The dark theme has its own ramp, so its counts are checked on a
            # map that loads dark, the way a reader who prefers it sees it.
            dark = open_page(
                browser, {"width": 1400, "height": 1000}, collected, origin, scheme="dark"
            )
            dark.goto(base_url + "index.html", wait_until="load")
            wait_idle(dark, failures, "step 1 dark")
            if not dark.evaluate("() => document.body.classList.contains('quarto-dark')"):
                failures.add("step 1 dark: the site did not follow the browser into dark mode")
            dark.wait_for_selector(".cgp-cluster span", timeout=IDLE_TIMEOUT_MS)
            cluster_contrast_check(dark, failures, "step 1 dark")
            check_clean(dark, failures, "step 1 dark", collected)
            dark.close()

            # Step 2: visit every page. Compare and Trends are checked for
            # content as well: navigation and the absence of an error cell say
            # nothing about whether either page drew anything at all, and a
            # completely empty card is exactly what this is the only gate on.
            countries = expected_usd_countries(latest, "regular")
            stations_on_latest_day = expected_latest_stations(
                site_dir / "data" / "summary_daily.parquet", "regular"
            )
            for name in PAGES:
                page_id = name.removesuffix(".html")
                requested: list[str] = []

                def note(request, requested=requested):
                    requested.append(request.url)

                page.on("request", note)
                page.goto(base_url + name, wait_until="load")
                wait_idle(page, failures, f"step 2 {page_id}")
                page.remove_listener("request", note)
                history_fetch_check(name, requested, failures, f"step 2 {page_id}")
                if page_id == "compare":
                    plot_check(page, failures, "step 2 compare", COMPARE_DOTS, countries, "dots")
                elif page_id == "trends":
                    plot_check(page, failures, "step 2 trends", TREND_MARKS, countries, "marks")
                    note_check(page, failures, "step 2 trends", stations_on_latest_day)
                elif page_id == "changes":
                    changes_key_check(page, failures, "step 2 changes")
                elif page_id == "about":
                    grade_table_check(page, failures, "step 2 about", meta, latest)
                chart_text_check(page, failures, f"step 2 {page_id}")
                chart_scale_check(page, failures, f"step 2 {page_id}")
                check_clean(page, failures, f"step 2 {page_id}", collected)

            # Step 2b: a station in the query string of a page that queries the
            # release but never loads history. The station's history query is
            # one of the shared cells, so it runs there too, and would ask for a
            # table the page never registered.
            for name in ("compare.html", "trends.html"):
                step = f"step 2b {name}?station="
                page.goto(
                    f"{base_url}{name}?station={quote(first_key, safe='')}", wait_until="load"
                )
                wait_idle(page, failures, step)
                check_clean(page, failures, step, collected)

            # Step 2c: hovering a chart has to raise a tip. The tip mark is easy
            # to render and hard to notice missing, and a helper defined in the
            # wrong chunk once took the whole Trends chart down to zero width.
            for page_id in ("compare", "trends"):
                page.goto(base_url + f"{page_id}.html", wait_until="load")
                wait_idle(page, failures, f"step 2c {page_id}")
                try:
                    # A chart sizes itself a tick after the page lays out.
                    page.wait_for_function(
                        """() => [...document.querySelectorAll("svg")]
                             .some((s) => s.getBoundingClientRect().width > 100)""",
                        timeout=10_000,
                    )
                except PlaywrightTimeoutError:
                    failures.add(f"step 2c {page_id}: no chart reached a usable width")
                    continue
                spot = page.evaluate(
                    """() => {
                      const svg = [...document.querySelectorAll("svg")]
                        .find((s) => s.getBoundingClientRect().width > 100);
                      if (!svg) return null;
                      const dots = [...svg.querySelectorAll("g[aria-label='dot'] circle")];
                      if (!dots.length) return null;
                      const dot = dots[Math.floor(dots.length / 2)];
                      dot.scrollIntoView({block: "center"});
                      const r = dot.getBoundingClientRect();
                      return {x: r.x + r.width / 2, y: r.y + r.height / 2};
                    }""",
                )
                if not spot:
                    failures.add(f"step 2c {page_id}: no chart wide enough to hover")
                    continue
                page.mouse.move(spot["x"], spot["y"])
                page.wait_for_timeout(700)
                text = page.evaluate(
                    """() => {
                      const svg = [...document.querySelectorAll("svg")]
                        .find((s) => s.getBoundingClientRect().width > 100);
                      const tip = svg && svg.querySelector("g[aria-label='tip']");
                      return tip ? tip.textContent.trim() : "";
                    }""",
                )
                if not text:
                    failures.add(f"step 2c {page_id}: hovering a point raised no tip")
                else:
                    print(f"step 2c {page_id}: tip reads {text.splitlines()[0][:40]!r}", flush=True)
                check_clean(page, failures, f"step 2c {page_id}", collected)

            # Step 2d: every Trends view ticks its dates in days, the coverage
            # strip under it included. A short history is where this breaks,
            # so the release's own data catches what the long sample cannot.
            # The change view in local currency also has to name each currency.
            for view in ("", "?view=change&cur=Local", "?view=separate&cur=Local"):
                step = f"step 2d trends{view}"
                page.goto(base_url + "trends.html" + view, wait_until="load")
                wait_idle(page, failures, step)
                day_ticks_check(page, failures, step)
                chart_text_check(page, failures, step)
                if "change" in view:
                    change_tip_check(page, failures, step, currencies(latest))
                check_clean(page, failures, step, collected)

            # Step 3: exercise every control value, then re-check the Map.
            for name, values in (("grade", GRADES), ("currency", CURRENCIES), ("volume", VOLUMES)):
                for value in values:
                    set_control(page, name, value)
                    wait_idle(page, failures, f"step 3 {name}={value}")
                    check_clean(page, failures, f"step 3 {name}={value}", collected)
            page.goto(base_url + "index.html", wait_until="load")
            set_control(page, "grade", "regular")
            set_control(page, "currency", "USD")
            wait_idle(page, failures, "step 3 map")
            map_check(page, failures, "step 3", latest, "regular")
            check_clean(page, failures, "step 3", collected)

            # Step 3b: a marker click has to show that station's prices and keep
            # showing them. The popup opened and was destroyed ~15ms later by a
            # marker-layer rebuild, so anything that only checked it opened would
            # have passed while the map was, in use, dead.
            marker = page.evaluate(
                """async () => {
                  const el = document.querySelector('.cgp-map');
                  const group = el && el._markers;
                  const layers = group ? group.getLayers() : [];
                  if (!layers.length) return null;
                  const target = layers[0];
                  // Past disableClusteringAtZoom every marker stands alone, so
                  // the point below is the marker itself and not a cluster.
                  el._map.setView(target.getLatLng(), 12, {animate: false});
                  await new Promise((r) => setTimeout(r, 600));
                  const visible = group.getVisibleParent
                    ? group.getVisibleParent(target)
                    : target;
                  if (visible && visible !== target) return null;
                  const pt = el._map.latLngToContainerPoint(target.getLatLng());
                  const r = el.getBoundingClientRect();
                  return {x: r.left + pt.x, y: r.top + pt.y};
                }"""
            )
            if not marker:
                failures.add("step 3b: no marker stood alone to click, even zoomed in")
            else:
                page.mouse.click(marker["x"], marker["y"])
                try:
                    page.wait_for_selector(".leaflet-popup", timeout=5_000)
                    page.wait_for_timeout(1_200)
                    if page.locator(".leaflet-popup").count() != 1:
                        failures.add("step 3b: the station popup did not stay open")
                    elif not page.locator(".leaflet-popup .cgp-popup table tr").count():
                        failures.add("step 3b: the station popup carried no prices")
                    else:
                        print("step 3b: marker click shows prices and they stay", flush=True)
                except PlaywrightTimeoutError:
                    failures.add("step 3b: clicking a marker opened no popup")
                check_clean(page, failures, "step 3b", collected)

            # Step 4: the Station deep link.
            page.goto(
                f"{base_url}station.html?station={quote(first_key, safe='')}", wait_until="load"
            )
            wait_idle(page, failures, "step 4 deep link")
            if not page.url.split("/")[-1].startswith("station.html"):
                failures.add(f"step 4: the deep link did not land on station.html ({page.url})")
            # The link names its station, so the search list stands down until
            # something is typed: it put forty other stations above this one.
            listed = page.locator(".cgp-results button").count()
            if listed:
                failures.add(f"step 4: the deep link lands under {listed} unrelated search results")
            else:
                page.locator(".cgp-search input[type=search]").fill(latest[0]["name"])
                try:
                    page.wait_for_selector(".cgp-results button", timeout=10_000)
                    print("step 4: search list hidden until something is typed", flush=True)
                except PlaywrightTimeoutError:
                    failures.add("step 4: typing in the search box brought no results back")
            marks = page.locator(
                '.cgp-station-chart svg g[aria-label="line"] path, '
                '.cgp-station-chart svg g[aria-label="dot"] circle'
            ).count()
            if marks < 1:
                failures.add(f"step 4: the station chart drew {marks} marks for {first_key}")
            else:
                print(f"step 4: station chart drew {marks} marks for {first_key}", flush=True)
            # The history chart answers on hover too, and the panel says which
            # rate turned the local price into the USD one.
            spot = page.evaluate(
                """() => {
                  const svg = [...document.querySelectorAll("svg")]
                    .find((s) => s.getBoundingClientRect().width > 200
                                 && s.querySelector("g[aria-label='dot'] circle"));
                  if (!svg) return null;
                  const dots = [...svg.querySelectorAll("g[aria-label='dot'] circle")];
                  const dot = dots[Math.floor(dots.length / 2)];
                  // The page scrolls now, so a mark can sit below the fold.
                  dot.scrollIntoView({block: "center"});
                  const r = dot.getBoundingClientRect();
                  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
                }"""
            )
            if not spot:
                failures.add("step 4: no station chart to hover")
            else:
                page.mouse.move(spot["x"], spot["y"])
                page.wait_for_timeout(700)
                tip = page.evaluate(
                    """() => {
                      const svg = [...document.querySelectorAll("svg")]
                        .find((s) => s.getBoundingClientRect().width > 200
                                     && s.querySelector("g[aria-label='dot'] circle"));
                      const g = svg && svg.querySelector("g[aria-label='tip']");
                      return g ? g.textContent.trim() : "";
                    }"""
                )
                if not tip:
                    failures.add("step 4: hovering the station chart raised no tip")
                else:
                    print(f"step 4: chart tip reads {tip.splitlines()[0][:36]!r}", flush=True)
            day_ticks_check(page, failures, "step 4")
            chart_text_check(page, failures, "step 4")
            panel = page.locator("body").inner_text()
            if "USD" not in panel:
                failures.add("step 4: the station panel never mentions the USD conversion")
            check_clean(page, failures, "step 4", collected)

            # Step 5: two chains stay apart on every page.
            if len({station.get("brand") for station in latest}) > 1:
                two_chain_steps(page, base_url, latest, failures, collected)
            else:
                print("step 5: one chain in latest.json, so the two-chain checks skip", flush=True)

            # Step 6: a phone.
            phone = open_page(browser, PHONE, collected, origin)
            phone_steps(phone, base_url, first_key, failures, collected)

            # Step 7: the freshness notice, per feed and per country.
            freshness_steps(browser, base_url, meta, stations, failures, collected)

            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()

    failures.raise_if_any()
    print("smoke test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
