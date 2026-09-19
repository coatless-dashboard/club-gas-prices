"""Static guards on the dashboard source — no browser needed."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
QMD = SITE / "index.qmd"
CUSTOM = SITE / "custom.scss"
DARK = SITE / "dark.scss"
CONFIG = SITE / "_quarto.yml"

# One page per view, plus the two includes every page pulls in.
PAGES = ("index.qmd", "compare.qmd", "trends.qmd", "changes.qmd", "station.qmd", "about.qmd")
INCLUDES = ("_shared.qmd", "_controls.qmd")


def read_qmd() -> str:
    """Every page and include, concatenated.

    These guards are about what the site says and does, not about which file
    says it; the split into pages moved a lot of it without changing any of it.
    """
    return "\n".join((SITE / name).read_text(encoding="utf-8") for name in (*INCLUDES, *PAGES))


def test_the_project_declares_a_website_and_both_themes():
    config = CONFIG.read_text(encoding="utf-8")
    assert "type: website" in config
    assert "respect-user-color-scheme: true" in config
    assert "light: [default, custom.scss]" in config
    assert "dark: [default, custom.scss, dark.scss]" in config
    assert "echo: false" in config


def test_the_navbar_links_the_repository_and_the_data():
    config = CONFIG.read_text(encoding="utf-8")
    # This site's own source, and the collector whose releases it reads.
    assert "https://github.com/coatless-dashboard/club-gas-prices" in config
    assert "https://github.com/coatless-data/club-gas-prices/releases" in config
    assert "releases/tag/current" in config


def test_every_page_exists_and_is_in_the_navbar():
    config = CONFIG.read_text(encoding="utf-8")
    for name in PAGES:
        assert (SITE / name).exists(), name
        assert f"href: {name}" in config, name


def test_every_page_pulls_in_the_shared_cells_and_the_controls():
    """Each page includes _shared.qmd and _controls.qmd (separate OJS runtimes)."""
    for name in PAGES:
        text = (SITE / name).read_text(encoding="utf-8")
        assert "{{< include _shared.qmd >}}" in text, name
        assert "{{< include _controls.qmd >}}" in text, name
        assert "pageWantsDb = " in text, name
        # Every page, because the shared db cell reads it on every page.
        assert "pageWantsHistory = " in text, name


def test_exactly_one_duckdb_client_is_created():
    assert read_qmd().count("DuckDBClient.of(") == 1


def test_the_page_adds_no_library_imports_of_its_own():
    text = read_qmd()
    assert "import {" not in text
    assert 'require("' not in text


def test_no_parenthesised_object_literal_cells():
    # `X = ({...})` makes Quarto's OJS cell splitter swallow the rest of the
    # chunk without any error, so every later cell in it goes undefined.
    assert re.search(r"^\s*\w+\s*=\s*\(\{", read_qmd(), re.M) is None


def test_parquet_reaches_duckdb_as_bytes_not_as_a_url():
    """Blob: URLs avoid GitHub Pages' broken gzipped range responses."""
    text = read_qmd()
    assert "URL.createObjectURL" in text
    assert "await file.arrayBuffer()" in text
    # The whole bug was handing these two straight to DuckDBClient.
    for name in ("summary_daily", "history"):
        assert f'buffered(FileAttachment("data/{name}.parquet")' in text


def test_only_the_pages_that_query_load_duckdb():
    """Non-querying pages skip DuckDB-WASM entirely."""
    text = read_qmd()
    assert 'body.classList.contains("quarto-dark")' in text
    assert "if (!pageWantsDb) return null;" in text
    assert "dbWanted" not in text
    wants = {
        name: "pageWantsDb = true" in (SITE / name).read_text(encoding="utf-8") for name in PAGES
    }
    assert wants == {
        "index.qmd": False,
        "compare.qmd": True,
        "trends.qmd": True,
        "changes.qmd": True,
        "station.qmd": True,
        "about.qmd": False,
    }


def test_only_the_pages_that_query_history_download_it():
    """Only pages that query history.parquet download it (it grows without bound)."""
    wants = {
        name: "pageWantsHistory = true" in (SITE / name).read_text(encoding="utf-8")
        for name in PAGES
    }
    assert wants == {
        "index.qmd": False,
        "compare.qmd": False,
        "trends.qmd": False,
        "changes.qmd": True,
        "station.qmd": True,
        "about.qmd": False,
    }
    # A page that queries history itself says so.
    for name in PAGES:
        if "FROM history" in (SITE / name).read_text(encoding="utf-8"):
            assert wants[name], name
    shared = (SITE / "_shared.qmd").read_text(encoding="utf-8")
    db = shared.split("\ndb = {\n", 1)[1].split("\n}\n", 1)[0]
    loads = db.split('buffered(FileAttachment("data/history.parquet")', 1)[0]
    assert loads.rstrip().endswith("pageWantsHistory\n      ?"), loads[-80:]
    assert "...(history ? {history} : {})," in db
    # The one history query in the shared cells runs on every page, so it stands
    # down where the table was never registered.
    query = shared.split("stationHistoryRows = {", 1)[1].split("\n}\n", 1)[0]
    assert "if (!db || !pageWantsHistory || !selectedMeta) return [];" in query


def test_the_freshness_notice_reads_each_feed_and_falls_back_to_countries():
    """Per-feed freshness, falling back to the country block."""
    text = read_qmd()
    sources = text.split("freshnessSources = {", 1)[1].split("\n}\n", 1)[0]
    assert "Object.values(meta.feeds || {})" in sources
    # Named by the rule every chart names a chain by.
    assert "seriesLabel(code, feed.brand, chains)" in sources
    # A chain the data holds no station of is never named.
    assert "shown.has(feed.brand)" in sources
    # Without feeds, the country block exactly as before.
    assert "const entry = meta.countries ? meta.countries[code] : null;" in sources
    # And country by country, not all or nothing: a capture that skipped a
    # country lists none of its feeds, and that country is read by its block.
    assert "if (mine.length) continue;" in sources
    assert "if (!feeds.length) {" not in sources
    notice = text.split("freshnessNotice = {", 1)[1].split("\n}\n", 1)[0]
    assert "for (const {label, captureId} of freshnessSources)" in notice
    assert "meta.countries" not in notice


def test_the_freshness_notice_uses_the_window_from_meta():
    # `stale_after_hours` comes from meta.json, so the window is configuration,
    # not a number baked into the page.
    text = read_qmd()
    assert "meta.stale_after_hours" in text
    assert "builtAge > staleAfterHours" in text
    assert "age > staleAfterHours" in text


def test_the_scss_files_define_the_ramp_in_both_themes():
    for path in (CUSTOM, DARK):
        text = path.read_text(encoding="utf-8")
        assert "/*-- scss:defaults --*/" in text
        assert "/*-- scss:rules --*/" in text
        for token in ("--cgp-ramp-1", "--cgp-ramp-5", "--cgp-missing", "--cgp-marker-stroke"):
            assert token in text, f"{path.name} is missing {token}"


def test_the_map_lifecycle_pieces_are_present():
    text = read_qmd()
    assert text.count("data-marker-count") >= 2  # created with it, then updated
    assert "new ResizeObserver" in text
    assert "invalidateSize()" in text
    assert "L.tileLayer(" in text
    assert "basemap.dark_url" in text and "basemap.light_url" in text
    assert "cgp-tiles-invert" in text


def test_station_selection_uses_the_query_string():
    text = read_qmd()
    assert "selectedStation = Generators.observe(" in text
    # One place knows what a station URL looks like, so the popups, the search
    # results, the neighbour lists and the channel cannot disagree.
    assert "function stationHref(key" in text
    assert '"station.html?station=" + encodeURIComponent(key)' in text
    assert "QuartoDashboardUtils" not in text


def test_the_controls_travel_with_a_link():
    """Each page is its own document. Without this the three shared controls
    silently reset on every navigation, and a shared link shows the recipient
    different units from the sender."""
    text = read_qmd()
    assert "CONTROL_PARAMS = {" in text
    assert "function withControls(href)" in text
    assert "controlLinkCarrier = {" in text
    # The query string rather than storage, so a link carries what it shows.
    assert "localStorage" not in text


def test_every_history_query_is_bounded():
    """history.parquet grows without bound, so nothing may scan all of it.

    Two shapes are allowed. A station-scoped query names its keys, which prunes
    row groups because station_key leads the file's sort order. A place-scoped
    query does not, so it must at least carry a date window. A query with
    neither would read the whole file into the browser.
    """
    for match in re.finditer(r"FROM history\b(.*?)`", read_qmd(), re.S):
        body = match.group(1)
        station_scoped = "WHERE station_key IN (" in body
        windowed = "capture_date > (SELECT max(capture_date) FROM history)" in body
        assert station_scoped or windowed, body[:200]


def ojs_chunks() -> list[str]:
    """The body of every ```{ojs} chunk, in document order."""
    return re.findall(r"^```\{ojs\}\n(.*?)^```$", read_qmd(), re.S | re.M)


def test_no_chart_is_built_in_a_hidden_chunk():
    """Charts in hidden chunks get zero width and empty cards — no error."""
    hidden = [c for c in ojs_chunks() if c.lstrip().startswith("//| output: false")]
    assert len(hidden) >= 4  # the chunk split works, so the loop below is not vacuous
    for chunk in hidden:
        lines = chunk.strip().splitlines()
        first = lines[1][:60] if len(lines) > 1 else lines[0][:60]
        assert "Plot.plot(" not in chunk, f"hidden chunk builds a chart: {first}"


def test_a_hidden_control_is_placed_by_a_visible_chunk():
    """A viewof in a hidden chunk must be interpolated by a visible one."""
    chunks = ojs_chunks()
    hidden = [c for c in chunks if c.lstrip().startswith("//| output: false")]
    shown = "\n".join(c for c in chunks if not c.lstrip().startswith("//| output: false"))
    assert len(hidden) >= 4  # the chunk split works, so this is not vacuous
    placed = 0
    for chunk in hidden:
        for line in chunk.splitlines():
            stripped = line.lstrip()
            if not stripped.startswith("viewof "):
                continue
            name = stripped.split()[1].split("=")[0].strip()
            assert f"viewof {name}" in shown, f"{name} is defined hidden and never placed"
            placed += 1
    assert placed >= 1


def test_no_plot_scheme_the_pinned_plot_build_does_not_know():
    # Quarto's OJS stdlib pins @observablehq/plot 0.6.11, whose ordinal schemes
    # do not include "observable10": naming it throws `unknown ordinal scheme`
    # and turns the whole cell into an error box. A scheme is always a string
    # literal, so the page may still say the name in a comment.
    assert '"observable10"' not in read_qmd()


def test_the_cluster_plugin_is_loaded_without_touching_globals():
    """Shadow define/module/exports locally; blanking globals breaks OJS."""
    text = read_qmd()
    assert 'new Function("define", "module", "exports", source)' in text
    assert "window.define = undefined" not in text
    # The map has to name its own maxZoom: the cluster group is added before any
    # tile layer exists and refuses a map whose maximum zoom is unbounded.
    assert "maxZoom: 19" in text


def test_the_map_groups_stations_and_stops_at_a_stated_zoom():
    text = read_qmd()
    assert "markerClusterGroup" in text
    assert "disableClusteringAtZoom: LABEL_ZOOM" in text
    # Falls back to a plain layer group rather than losing the map entirely.
    assert "L.layerGroup()" in text


def test_the_page_is_written_in_american_english():
    """One spelling across the dashboard, the README and the code comments."""
    british = re.compile(
        r"\b(colour\w*|dearer|dearest|grey|licence|behaviour\w*|centre|organis\w+)\b",
        re.IGNORECASE,
    )
    # Every page and include, not the map's alone: the split into pages left
    # the others unread, and "colour" sat in two of Changes' comments.
    for path in (
        *sorted(SITE.glob("*.qmd")),
        ROOT / "site" / "custom.scss",
        ROOT / "site" / "dark.scss",
        ROOT / "README.md",
    ):
        found = british.findall(path.read_text(encoding="utf-8"))
        assert not found, f"{path.name}: {sorted(set(found))}"


def test_the_legend_says_what_the_ramp_means():
    """ "Cheaper/Dearer" left a reader guessing which end was which."""
    text = read_qmd()
    assert "Lower price" in text and "Higher price" in text
    assert "least to most expensive within each country" in text


def test_a_station_carries_its_country_flag():
    text = read_qmd()
    assert "function countryFlag(code)" in text
    # Flags built from regional indicator symbols, not shipped images.
    assert "0x1f1e6" in text
    assert text.count('class="cgp-flag"') >= 2


def test_the_trend_view_control_is_built_once():
    """The view control must not depend on reactive values that would reset it."""
    text = read_qmd()
    block = text.split("viewof trendMode = radioControl(", 1)[1].split(")\n```", 1)[0]
    for reactive in ("trendCountry", "currency", "grade", "volume"):
        assert reactive not in block, f"trendMode depends on {reactive}"
    assert '"price"' in block and '"change"' in block and '"separate"' in block


def test_a_shared_price_axis_needs_a_single_currency():
    """Mixed currencies across countries force a change view, not a price axis."""
    text = read_qmd()
    assert 'mixedCurrency = currency === "Local" && trendCountry === "All countries"' in text
    assert (
        'effectiveTrendMode = mixedCurrency && trendMode === "price" ? "change" : trendMode' in text
    )
    # Both labels exist, and the local one says the change was measured in each
    # country's own money -- without that clause the axis is ambiguous.
    assert "each country in its own currency (%)" in text
    assert "since ${baseText}, in USD (%)" in text


def test_the_change_view_anchors_every_country_on_one_day():
    """All lines share one base day — the view's whole point."""
    text = read_qmd()
    assert "function commonBaseDay(" in text
    assert "Math.max(...firsts)" in text
    assert "its own first day" in text


def test_compare_draws_a_chart_in_every_scope():
    """Every scope (country/region x USD/local) gets a chart, not a table."""
    text = read_qmd()
    compare = text.split("compareView = {", 1)[1]
    assert 'if (currency === "Local") return compareTableEl();' not in compare
    # Four scopes: country/region across USD/local, all through one mark set.
    assert "function dotPlot(rows, {" in compare
    assert compare.count("dotPlot(") >= 4
    # A country's regions share its currency, so they keep real prices.
    assert "One country \nprices in one currency" in compare or (
        "prices in one currency, so these are real prices" in compare
    )


def test_compare_keeps_the_table_helper_as_a_fallback():
    """compareTableEl is kept as a fallback even though it's no longer reached."""
    assert "function compareTableEl()" in read_qmd()


def test_series_are_capped_to_the_palette():
    """Cap at 10 series — beyond that, Plot silently reuses colors."""
    text = read_qmd()
    assert "PALETTE_SERIES_CAP = 10" in text
    assert "drawn = asked.slice(0, PALETTE_SERIES_CAP)" in text
    # The default checked set respects the cap rather than hardcoding a number.
    assert "options.slice(0, PALETTE_SERIES_CAP)" in text


def test_line_ends_are_labelled_per_series():
    """selectLast per series so stalled feeds keep their label."""
    text = read_qmd()
    assert "Plot.selectLast(" in text
    # `z` must be explicit: with `stroke` a function Plot infers no series and
    # labels exactly one line.
    for block in text.split("Plot.selectLast({")[1:]:
        # Explicit, and the same key the line mark uses: two chains in one
        # country are two lines, so a series keyed on country would label one
        # and leave the other anonymous.
        assert 'z: "series"' in block.split("})")[0]


def test_every_end_label_is_spread_once_its_chart_is_drawn():
    """End labels are marked with END_LABELS and spread post-render."""
    sys.path.insert(0, str(ROOT / "tests" / "smoke"))
    from smoke_site import END_LABELS

    shared = (SITE / "_shared.qmd").read_text(encoding="utf-8")
    assert f'END_LABELS = "{END_LABELS}"' in shared
    assert "function spreadEndLabels(figure)" in shared
    for name, charts in (("trends.qmd", 2), ("station.qmd", 1)):
        page = (SITE / name).read_text(encoding="utf-8")
        marks = page.split("Plot.text(")[1:]
        assert marks, name
        for mark in marks:
            assert "ariaDescription: END_LABELS" in mark.split("})", 1)[0], name
        passes = page.split("spreadEndLabels(figure);")
        assert len(passes) == charts + 1, name
        for before in passes[:-1]:
            # The pass reads the drawn SVG, so the chart it moves is built first.
            assert "const figure = Plot.plot({" in before, name


def test_dots_stand_down_once_they_stop_marking_anything():
    text = read_qmd()
    assert "function showDots(" in text
    assert "showDots(days.length)" in text


def test_the_sample_uses_keys_the_pipeline_would_emit():
    """Station keys must match the <COUNTRY>-<BRAND>-<id> format the pipeline emits."""
    import re as _re
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
    from site_sample import STATIONS

    assert STATIONS, "the sample has no stations"
    for station in STATIONS:
        key = station["station_key"]
        parts = key.split("-")
        assert len(parts) == 3, f"{key} is not <COUNTRY>-<BRAND>-<id>"
        country, brand, sid = parts
        assert _re.fullmatch(r"[A-Z]{2}", country), key
        assert _re.fullmatch(r"[A-Z0-9]+", brand), key
        assert station["country"] == country, key
        assert station["brand"] == brand, key
        # MX, TW and AU are keyed on the warehouse number, not the branch name.
        if country in ("MX", "TW", "AU"):
            assert sid.isdigit(), f"{key} is not a warehouse number"


def test_object_constants_use_the_block_form():
    """`NAME = {…}` is a block in OJS, not an object literal."""
    text = read_qmd()
    for name in ("COUNTRY_NAMES", "COUNTRY_COLORS", "GRADE_LABELS", "WAREHOUSE_PAGE"):
        block = text.split(f"{name} = ", 1)[1][:40]
        assert block.lstrip().startswith("{\n  return"), f"{name} is not the block form"


def test_only_verified_store_pages_are_linked():
    """Checked on 2026-09-16: mx, au and jp answer at the bare /store/<name>
    path; co.uk and com.tw give 404 bare and locale-prefixed, and the US and
    Canadian feeds carry no store URL at all."""
    text = read_qmd()
    spec = text.split("WAREHOUSE_PAGE = ", 1)[1].split("\n}", 1)[0]
    for code in ("MX", "AU", "JP"):
        assert f"{code}: {{base:" in spec
    for code in ("GB", "TW", "US", "CA"):
        assert f"{code}: {{base:" not in spec


def test_directions_prefer_coordinates():
    """Address quality varies a lot across seven countries; a lat/lon does not."""
    text = read_qmd()
    body = text.split("function directionsUrl(", 1)[1].split("\n}", 1)[0]
    assert "maps/dir/?api=1&destination=" in body
    assert body.index("meta.lat") < body.index("meta.address")


def test_the_site_explains_what_a_station_status_means():
    """A station is never removed once seen, so every status describes a station
    that is still here with all its history; only its listing changed."""
    text = read_qmd()
    assert "function statusSentence(meta)" in text
    assert 'meta.status === "closed"' in text
    assert 'meta.status === "missing"' in text
    # Not listed right now is not the same claim as closed.
    assert "often a gap, not a closure" in text
    # The sentence names the chain that stopped listing it, because with two
    # chains in the data "Costco is not listing it" is wrong half the time.
    assert "brandName(meta)" in text
    assert "Costco has not listed" not in text


def test_the_map_styling_follows_quartos_own_theme_switch():
    """Leaflet ships light styling and is fetched at runtime, so its stylesheet
    lands after the theme's and wins on order at equal specificity. The extra
    class is what beats it, and Bootstrap's own variables are what the light and
    dark switch actually changes."""
    css = CUSTOM.read_text(encoding="utf-8")
    assert ".leaflet-tooltip.cgp-hover-tip {" in css
    assert ".leaflet-popup .leaflet-popup-content-wrapper," in css
    for rule in ("--bs-body-bg", "--bs-body-color", "--bs-border-color"):
        assert rule in css
    # The dark theme must not restate what the shared rules already key on.
    dark = DARK.read_text(encoding="utf-8")
    assert ".leaflet-popup-content-wrapper" not in dark


def test_the_notice_and_a_route_for_rights_holders_are_in_the_footer():
    footer = (SITE / "_footer.qmd").read_text(encoding="utf-8")
    # One source of truth: the notice comes from config through meta.json.
    assert "meta.notice" in footer
    # Brand-neutral: the notice above it already names every chain, and this
    # route has to work for whichever one is writing in.
    assert "Costco Wholesale Corporation" not in footer
    assert "one of these chains" in footer
    # A takedown is offered, in whatever words.
    assert "removed" in footer
    # By an issue, or by writing in. The footer once told chains to write to an
    # address in the repository, which published none; the one it offers now
    # is built in the reader's browser from its parts, the scheme included.
    body = footer.split("siteFooter = {", 1)[1]
    assert '"/issues/new"' in body
    assert "on GitHub or email ${mail}." in body
    assert '["support", ["caffeinatedmath", "com"].join(".")].join("@")' in body
    assert '"mail" + "to:" + address' in body
    assert "mailto:" not in footer
    assert "README" not in body
    for name in PAGES:
        assert "{{< include _footer.qmd >}}" in (SITE / name).read_text(encoding="utf-8"), name


def test_the_contact_address_is_never_written_whole():
    """The email address is assembled client-side; no file may contain it whole."""
    sys.path.insert(0, str(ROOT / "tests" / "smoke"))
    from smoke_site import ojs_sources

    domain = ".".join(("caffeinatedmath", "com"))
    address = "@".join(("support", domain))
    needles = (address, "@" + domain, "mailto:" + address)
    sources = [path for path in SITE.rglob("*") if path.is_file()]
    sources.append(ROOT / "README.md")
    assert len(sources) > 10  # the walk finds the site, so this is not vacuous
    for path in sources:
        text = path.read_bytes().decode("utf-8", errors="replace")
        for needle in needles:
            assert needle not in text, path.relative_to(ROOT)
    for page in sorted((ROOT / "_site").glob("*.html")):
        text = page.read_text(encoding="utf-8")
        for part in (text, *ojs_sources(text)):
            for needle in needles:
                assert needle not in part, page.name
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "email support [at] caffeinatedmath [dot] com" in readme


def test_a_daily_series_gets_daily_ticks():
    """Ticks land on whole days, not sub-day intervals."""
    text = read_qmd()
    body = text.split("function dayTicks(rows, room = chartWidth) {", 1)[1].split("\n}\n", 1)[0]
    assert "new Date(times[0] + i * 864e5)" in body
    # Every date axis goes through it. The coverage strip and the small
    # multiples each took a bare `ticks: 4`, and drew six-hour ticks.
    axes = 0
    for name in ("trends.qmd", "station.qmd"):
        page = (SITE / name).read_text(encoding="utf-8")
        for axis in re.findall(r"\bx: \{([^}]*)\}", page):
            assert "ticks: dayTicks(" in axis, f"{name}: {axis}"
            axes += 1
    assert axes >= 7  # the pattern finds the axes, so the loop is not vacuous
    strip = text.split("function coverageStrip(", 1)[1].split("\n}\n", 1)[0]
    assert "ticks: dayTicks(x.domain)" in strip


def test_the_change_tip_looks_the_currency_up_by_country():
    """currencyOf is keyed on country, not on the full series key."""
    trends = (SITE / "trends.qmd").read_text(encoding="utf-8")
    change = trends.split("function allCountriesChange() {", 1)[1].split("\n  function ", 1)[0]
    assert "currencyOf.get(row.country)" in change
    assert "currencyOf.get(code)" not in change


def test_the_about_page_describes_the_schedule_the_collector_runs():
    """The About page must say four times a day, open or not."""
    # The prose wraps, so it is read as one line.
    about = " ".join((SITE / "about.qmd").read_text(encoding="utf-8").split())
    assert "four times a day" in about
    assert "every station four times a day, open or not" in about
    assert "every four hours" not in about
    assert "while a station is open" not in about


def test_the_grade_table_leaves_out_a_chain_column_it_cannot_fill():
    """No Chain column when grade rows lack a brand."""
    about = (SITE / "about.qmd").read_text(encoding="utf-8")
    table = about.split("aboutGrades = {", 1)[1].split("\n}\n", 1)[0]
    assert "const withChain = rows.some((row) => row.brand);" in table
    assert "withChain ? html`<th>Chain</th>`" in table
    assert 'row.brand || ""' not in table


def test_summary_queries_carry_brand_so_two_chains_cannot_pool():
    """Queries must select/group by brand to avoid silently pooling two chains."""
    shared = (ROOT / "site" / "_shared.qmd").read_text(encoding="utf-8")

    country_queries = shared.count("WHERE level = 'country'")
    assert country_queries >= 2, "expected the country-level summary queries"
    assert shared.count("ORDER BY country, brand, capture_date") == country_queries

    # brand is projected, so a consumer can tell the chains apart.
    assert shared.count("             brand,\n") == country_queries

    # And the station count is taken per series before the series are added
    # up, never as a bare sum across rows.
    assert "function seriesOf(row)" in shared
    assert "function latestStationCount(rows, series)" in shared
    assert ".reduce((total, row) => total + (row.n_stations || 0), 0)" not in shared


def test_the_latest_day_note_adds_up_every_series():
    """The station count sums every series, not just the largest one."""
    shared = (SITE / "_shared.qmd").read_text(encoding="utf-8")
    body = shared.split("function latestStationCount(", 1)[1].split("\n}\n", 1)[0]
    assert "Math.max(0, ...bySeries.values())" not in body
    assert "Math.max(bySeries.get(key) || 0, row.n_stations || 0)" in body
    assert "total += count" in body


def region_queries(name: str) -> list[str]:
    """Every region-level summary query on a page, SELECT to closing backtick."""
    text = (SITE / name).read_text(encoding="utf-8")
    return [q for q in re.findall(r"SELECT(.*?)`", text, re.S) if "level = 'region'" in q]


def test_region_queries_carry_brand_so_two_chains_cannot_pool():
    """Region queries must carry brand to keep two chains apart."""
    for name in ("compare.qmd", "trends.qmd"):
        queries = region_queries(name)
        assert queries, name
        for query in queries:
            assert query.lstrip().startswith("region,\n") and "brand,\n" in query, name
            assert "ORDER BY region, brand, capture_date" in query, name


def test_compare_keys_every_row_on_its_chain():
    """Each row is keyed on its chain, not just its country."""
    shared = (SITE / "_shared.qmd").read_text(encoding="utf-8")
    compare = (SITE / "compare.qmd").read_text(encoding="utf-8")
    assert "newestByKey(compareRows, seriesOf)" in shared
    assert "newestByCountry" not in shared
    # A region's key is the region and the chain.
    assert "(row) => `${row.region} :: ${row.brand}`" in compare
    assert 'newestByKey(compareRegionRows, "region")' not in compare
    # Each local-currency row is measured from its own chain's history.
    assert "series.get(seriesOf(item))" in compare
    assert "series.get(item.country)" not in compare
    # And every row is labelled by the one rule that names a chain.
    assert "label: seriesLabel(row.country, row.brand, chains)" in shared
    assert "placeLabel(row.region, compareCountry, row.brand, compareLatest.chains)" in compare
    assert "COUNTRY_NAMES[row.country] || row.country} · ${row.d}" not in compare


def test_every_trends_line_and_band_is_keyed_on_the_series():
    """Every line and band needs `z: "series"` to prevent chain-merging."""
    trends = (SITE / "trends.qmd").read_text(encoding="utf-8")
    marks = re.split(r"Plot\.(?:line|areaY)\(", trends)[1:]
    assert len(marks) >= 10  # the split works, so the loop below is not vacuous
    for mark in marks:
        options = mark.split("})", 1)[0]
        assert 'z: "series"' in options, options[:160]
    # A region's series is the region and the chain, as a country's is.
    assert "series: `${row.region} :: ${row.brand}`" in trends
    assert 'z: "region"' not in trends
    # The Regions control counts each chain's stations and adds them up; the
    # larger chain alone gave a state both serve half of its count.
    options = trends.split("trendRegionOptions = {", 1)[1].split("\n}\n", 1)[0]
    assert "const key = `${row.region} :: ${row.brand}`;" in options
    assert "(counts.get(region) || 0) + n" in options
    # Every line is named by the rule that names a chain where a country has two.
    assert "seriesLabel(row.country, row.brand)" not in trends
    assert trends.count("seriesLabel(row.country, row.brand, chains)") >= 3


def test_the_station_median_is_its_own_chains():
    """A state both chains serve has a median for each, and read together they
    drew one line alternating between them under a single label."""
    shared = (SITE / "_shared.qmd").read_text(encoding="utf-8")
    station = (SITE / "station.qmd").read_text(encoding="utf-8")
    query = shared.split("stationMedianRows = {", 1)[1].split("\n}\n", 1)[0]
    assert "AND brand = ${sqlText(selectedMeta.brand)}" in query
    assert "${brandClause}" in query
    # The page names the station's chain, in the median's label and beside its
    # place, by the rule every chart label follows.
    assert station.count("chainTag(selectedMeta.country, selectedMeta.brand") == 2
    assert '${chain ? ` · ${chain}` : ""} · ${selectedMeta.station_key}' in station


def test_changes_rows_are_per_chain_and_keyed_on_the_station():
    """Keys computed across both chains gave each chain's chart a blank row for
    every station of the other; keyed on the name, two stations sharing one
    would have merged."""
    changes = (SITE / "changes.qmd").read_text(encoding="utf-8")
    chart = changes.split("function changesChart(", 1)[1].split("\n}\n", 1)[0]
    assert "mine.map((c) => c.key)" in chart
    assert "cells.map((c) => c.key)" not in chart
    assert "tickFormat: labelOf" in chart
    assert "station_key: nameOf.get(" not in changes


def test_every_changes_row_gets_its_full_pitch():
    """The heatmap's height was 24px short of its two margins, so its rows
    shared the shortfall: a state with one station drew that row 0px tall, and
    a chain in two states got 4px a row."""
    changes = (SITE / "changes.qmd").read_text(encoding="utf-8")
    chart = changes.split("function changesChart(", 1)[1].split("\n}\n", 1)[0]
    assert "height: top + bottom + keys.length * pitch," in chart
    assert "marginTop: top," in chart
    assert "marginBottom: bottom," in chart


def test_the_coverage_strip_draws_every_series():
    """Keyed on the day and the chain, each country's row overwrote the one
    before it, and the strip under the seven-country chart drew the United
    States alone while the note beside it counted every country."""
    trends = (SITE / "trends.qmd").read_text(encoding="utf-8")
    strip = trends.split("function coverageStrip(", 1)[1].split("\n}\n", 1)[0]
    assert "const series = seriesOf(row);" in strip
    assert "byDay.set(`${row.d} :: ${series}`, {" in strip
    assert '${row.brand || ""}`' not in strip


def test_the_coverage_strip_takes_its_charts_frame():
    """The strip sits under the chart and is read against it a day at a time.
    With gutters of its own it had none of the chart's: Plot's 40px on the left
    of the price view, a right margin sized to the end labels on both, and so
    no tick under the day it named."""
    trends = (SITE / "trends.qmd").read_text(encoding="utf-8")
    strip = trends.split("function coverageStrip(", 1)[1].split("\n}\n", 1)[0]
    assert strip.startswith("rows, {label, chart}) {")
    assert 'const x = chart.scale("x");' in strip
    for option in (
        "width,",
        "marginLeft: x.range[0],",
        "marginRight: width - x.range[1],",
        "domain: x.domain",
    ):
        assert option in strip, option
    for fixed in ("width: chartWidth", "marginLeft: 52", "marginRight: 80"):
        assert fixed not in strip, fixed
    # Both charts with a strip hand it the chart they drew.
    assert trends.count("coverageStrip(trendCountryRows, {") == 2
    assert trends.count("      chart: figure\n    })}") == 2


def css_color(text: str, token: str) -> str:
    match = re.search(rf"{token}:\s*(#[0-9a-fA-F]{{3,6}})\s*;", text)
    assert match, f"no {token} token"
    return match.group(1)


def test_every_cluster_count_meets_aa_on_every_fill_in_both_themes():
    """A cluster's count is 11.7px text on whichever ramp step its median lands
    on, and it was white on all of them: 1.67:1 on the light amber and 1.55:1
    on the dark one, where WCAG AA asks 4.5:1. The ink is now whichever of two
    stands out more from the fill, and every fill has one that clears it."""
    sys.path.insert(0, str(ROOT / "tests" / "smoke"))
    from smoke_site import contrast_ratio, parse_color

    shared = (SITE / "_shared.qmd").read_text(encoding="utf-8")
    inks = re.findall(r"#[0-9a-fA-F]{6}", shared.split("CLUSTER_INKS = [", 1)[1].split("]", 1)[0])
    assert len(inks) == 2
    assert "color:${inkOn(fill)}" in shared.split("function clusterIcon(", 1)[1]
    # Nothing fixes the count's color behind the ink's back.
    badge = CUSTOM.read_text(encoding="utf-8").split(".cgp-cluster span {", 1)[1].split("}", 1)[0]
    assert re.search(r"(?<![-\w])color:", badge) is None
    tokens = [f"--cgp-ramp-{i}" for i in range(1, 6)] + ["--cgp-missing"]
    for path in (CUSTOM, DARK):
        text = path.read_text(encoding="utf-8")
        for token in tokens:
            fill = parse_color(css_color(text, token))
            best = max(contrast_ratio(parse_color(ink), fill) for ink in inks)
            assert best >= 4.5, f"{path.name} {token}: best ink is {best:.2f}:1"
