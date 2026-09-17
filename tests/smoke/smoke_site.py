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
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from range_server import serve  # noqa: E402

PREFIX = "/club-gas-prices/"
# One document per view now, so a page is a URL rather than a tab pane.
PAGES = ["index.html", "compare.html", "trends.html", "station.html", "about.html"]
GRADES = ["regular", "premium", "diesel"]
CURRENCIES = ["USD", "Local"]
VOLUMES = ["gal", "litre"]
IDLE_TIMEOUT_MS = 60_000

# The marks Observable Plot draws only when the page has rows: one dot per
# country on Compare's median chart, one line per country on the Trends chart.
COMPARE_DOTS = 'svg g[aria-label="dot"] circle'
TREND_LINES = 'svg g[aria-label="line"] path'

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
    card it belongs to -- cannot pass. The wait is `state="visible"`, not
    `attached`: a chart Quarto's dashboard autosizing measured against a hidden
    container is in the DOM, with every mark the data calls for, inside an SVG
    zero pixels wide. `wait_idle` has already seen every OJS cell finish and
    this waits up to 60 s more, so the count below cannot race the render.
    Like the Map check it allows 90%, because a country whose currency has no
    rate that day legitimately has no USD value to draw.
    """
    try:
        page.wait_for_selector(selector, timeout=IDLE_TIMEOUT_MS, state="visible")
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


def launch_browser(playwright, *, headed: bool = False):
    try:
        return playwright.chromium.launch(channel="chrome", headless=not headed)
    except Exception as exc:
        print(f"::notice::Google Chrome unavailable ({exc}); installing Playwright chromium")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
            check=True,
        )
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
    first_key = latest[0]["station_key"]

    httpd, base_url = serve(site_dir, PREFIX)
    origin = base_url[: base_url.index(PREFIX)]
    failures = Failures()
    collected = {"console": [], "pageerror": []}

    try:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright, headed=args.headed)
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
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
            check_clean(page, failures, "step 1", collected)

            # Step 2: visit every page. Compare and Trends are checked for
            # content as well: navigation and the absence of an error cell say
            # nothing about whether either page drew anything at all, and a
            # completely empty card is exactly what this is the only gate on.
            countries = expected_usd_countries(latest, "regular")
            for name in PAGES:
                page_id = name.removesuffix(".html")
                page.goto(base_url + name, wait_until="load")
                wait_idle(page, failures, f"step 2 {page_id}")
                if page_id == "compare":
                    plot_check(page, failures, "step 2 compare", COMPARE_DOTS, countries, "dots")
                elif page_id == "trends":
                    plot_check(page, failures, "step 2 trends", TREND_LINES, countries, "lines")
                check_clean(page, failures, f"step 2 {page_id}", collected)

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
                """() => {
                  const el = document.querySelector('.cgp-map');
                  const layers = el && el._markers ? el._markers.getLayers() : [];
                  if (!layers.length) return null;
                  const pt = el._map.latLngToContainerPoint(layers[0].getLatLng());
                  const r = el.getBoundingClientRect();
                  return {x: r.left + pt.x, y: r.top + pt.y};
                }"""
            )
            if not marker:
                failures.add("step 3b: the map drew no markers to click")
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
            panel = page.locator("body").inner_text()
            if "USD" not in panel:
                failures.add("step 4: the station panel never mentions the USD conversion")
            check_clean(page, failures, "step 4", collected)

            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()

    failures.raise_if_any()
    print("smoke test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
