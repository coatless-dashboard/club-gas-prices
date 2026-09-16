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

PREFIX = "/costco-gas-prices/"
PAGE_IDS = ["map", "compare", "trends", "station", "about"]
GRADES = ["regular", "premium", "diesel"]
CURRENCIES = ["USD", "Local"]
VOLUMES = ["gal", "litre"]
IDLE_TIMEOUT_MS = 60_000

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
            page.goto(base_url, wait_until="load")
            wait_idle(page, failures, "step 1 load")
            for page_id in PAGE_IDS:
                if page.locator(f"#{page_id}.tab-pane").count() != 1:
                    failures.add(f"step 1: no tab pane with id #{page_id}")
            if page.locator("#map.tab-pane.active").count() != 1:
                failures.add("step 1: the Map page is not active on load")
            map_check(page, failures, "step 1", latest, "regular")
            check_clean(page, failures, "step 1", collected)

            # Step 2: visit every page.
            for page_id in PAGE_IDS:
                page.locator(f'a.nav-link[href="#{page_id}"]').first.click()
                page.wait_for_selector(f"#{page_id}.tab-pane.active", timeout=10_000)
                wait_idle(page, failures, f"step 2 {page_id}")
                check_clean(page, failures, f"step 2 {page_id}", collected)

            # Step 3: exercise every control value, then re-check the Map.
            for name, values in (("grade", GRADES), ("currency", CURRENCIES), ("volume", VOLUMES)):
                for value in values:
                    set_control(page, name, value)
                    wait_idle(page, failures, f"step 3 {name}={value}")
                    check_clean(page, failures, f"step 3 {name}={value}", collected)
            page.locator('a.nav-link[href="#map"]').first.click()
            page.wait_for_selector("#map.tab-pane.active", timeout=10_000)
            set_control(page, "grade", "regular")
            set_control(page, "currency", "USD")
            wait_idle(page, failures, "step 3 map")
            map_check(page, failures, "step 3", latest, "regular")
            check_clean(page, failures, "step 3", collected)

            # Step 4: the Station deep link.
            page.goto(f"{base_url}?station={quote(first_key, safe='')}#station", wait_until="load")
            wait_idle(page, failures, "step 4 deep link")
            if page.locator("#station.tab-pane.active").count() != 1:
                failures.add("step 4: #station is not the active tab pane")
            marks = page.locator(
                '.cgp-station-chart svg g[aria-label="line"] path, '
                '.cgp-station-chart svg g[aria-label="dot"] circle'
            ).count()
            if marks < 1:
                failures.add(f"step 4: the station chart drew {marks} marks for {first_key}")
            else:
                print(f"step 4: station chart drew {marks} marks for {first_key}", flush=True)
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
