"""Static guards on the dashboard source. They need no browser."""

from __future__ import annotations

import re
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
    assert "https://github.com/coatless-datasets/club-gas-prices/releases" in config
    assert "releases/tag/current" in config


def test_every_page_exists_and_is_in_the_navbar():
    config = CONFIG.read_text(encoding="utf-8")
    for name in PAGES:
        assert (SITE / name).exists(), name
        assert f"href: {name}" in config, name


def test_every_page_pulls_in_the_shared_cells_and_the_controls():
    """Each page is its own OJS runtime, so the shared cells are included into
    every one of them rather than shared between them."""
    for name in PAGES:
        text = (SITE / name).read_text(encoding="utf-8")
        assert "{{< include _shared.qmd >}}" in text, name
        assert "{{< include _controls.qmd >}}" in text, name
        assert "pageWantsDb = " in text, name


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


def test_only_the_pages_that_query_load_duckdb():
    """A page that does not query never downloads DuckDB-WASM. In the dashboard
    this needed a MutationObserver on the active tab pane, because every cell of
    every view ran at load."""
    text = read_qmd()
    assert 'body.classList.contains("quarto-dark")' in text
    assert "db = pageWantsDb" in text
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
    """Every `Plot.plot` call belongs to the card that shows its chart.

    Two mechanisms punish a chart built in an `output: false` chunk, and
    neither one reports an error. An OJS cell's node is inserted where the cell
    is defined, so the hidden chunk adopts the chart and the card that returned
    it stays empty. And Quarto's dashboard autosizing rewrites every
    `Plot.plot` call to take the width and height of the cell the call appears
    in, so a chart built in a hidden chunk is measured against a container that
    is never visible and comes out zero pixels wide.
    """
    hidden = [c for c in ojs_chunks() if c.lstrip().startswith("//| output: false")]
    assert len(hidden) >= 4  # the chunk split works, so the loop below is not vacuous
    for chunk in hidden:
        lines = chunk.strip().splitlines()
        first = lines[1][:60] if len(lines) > 1 else lines[0][:60]
        assert "Plot.plot(" not in chunk, f"hidden chunk builds a chart: {first}"


def test_no_plot_scheme_the_pinned_plot_build_does_not_know():
    # Quarto's OJS stdlib pins @observablehq/plot 0.6.11, whose ordinal schemes
    # do not include "observable10": naming it throws `unknown ordinal scheme`
    # and turns the whole cell into an error box. A scheme is always a string
    # literal, so the page may still say the name in a comment.
    assert '"observable10"' not in read_qmd()


def test_the_cluster_plugin_is_loaded_without_touching_globals():
    """Blanking `define` to force a UMD's browser branch breaks the whole page.

    OJS supplies an AMD `define`, so the plugin registers as an anonymous module
    and never patches the global L. Shadowing define/module/exports as function
    parameters does the same job to one script; blanking the real globals took
    every cell still loading down with it.
    """
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
    for path in (
        QMD,
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
    # 0x1F1E6 is regional indicator A; the flag is built from the country code
    # rather than shipped as an image per country.
    assert "0x1f1e6" in text
    assert text.count('class="cgp-flag"') >= 2


def test_the_trend_view_control_is_built_once():
    """A control that depends on the breakdown or the currency resets on every
    change of either, throwing away the reader's choice."""
    text = read_qmd()
    block = text.split("viewof trendMode = radioControl(", 1)[1].split(")\n```", 1)[0]
    for reactive in ("trendCountry", "currency", "grade", "volume"):
        assert reactive not in block, f"trendMode depends on {reactive}"
    assert '"price"' in block and '"change"' in block and '"separate"' in block


def test_a_shared_price_axis_needs_a_single_currency():
    """The incommensurability is between countries, not inside one: a country's
    regions all price in the same currency, so they keep a real price axis."""
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
    """Anchoring each line on its own first day and printing one date on the
    axis is the claim this view exists to avoid."""
    text = read_qmd()
    assert "function commonBaseDay(" in text
    assert "Math.max(...firsts)" in text
    assert "its own first day" in text


def test_compare_draws_a_chart_in_every_scope():
    """Local currency used to fall back to a table, so the chart disappeared
    whenever the reader asked for local prices."""
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
    """compareTableEl is no longer reached, but it is the right answer if the
    local chart is ever cut, and it costs nothing to keep."""
    assert "function compareTableEl()" in read_qmd()


def test_series_are_capped_to_the_palette():
    """tableau10 has ten colors; asked for more, Plot cycles them silently and
    the legend then claims two regions are the same one."""
    text = read_qmd()
    assert "PALETTE_SERIES_CAP = 10" in text
    assert "drawn = asked.slice(0, PALETTE_SERIES_CAP)" in text
    # The default checked set respects the cap rather than hardcoding a number.
    assert "options.slice(0, PALETTE_SERIES_CAP)" in text


def test_line_ends_are_labelled_per_series():
    """Filtering on the global last day drops the label of any series whose feed
    stalled -- the one most worth naming."""
    text = read_qmd()
    assert "Plot.selectLast(" in text
    # `z` must be explicit: with `stroke` a function Plot infers no series and
    # labels exactly one line.
    for block in text.split("Plot.selectLast({")[1:]:
        # Explicit, and the same key the line mark uses: two chains in one
        # country are two lines, so a series keyed on country would label one
        # and leave the other anonymous.
        assert 'z: "series"' in block.split("})")[0]


def test_dots_stand_down_once_they_stop_marking_anything():
    text = read_qmd()
    assert "function showDots(" in text
    assert "showDots(days.length)" in text


def test_the_sample_uses_keys_the_pipeline_would_emit():
    """A sample key the collector could never produce ends up in a URL somebody
    shares -- and the site then renders against a shape it will never receive.

    This reads the fixture's own station list rather than site/data/, which is
    gitignored: the previous version guarded on `if data.exists()` and so never
    executed in CI, which is how the pre-brand key format survived a rename.
    """
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
    """`NAME = {…}` is a block in OJS, not an object literal. Getting this wrong
    does not fail that cell -- it takes down every cell in the chunk."""
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
    for name in PAGES:
        assert "{{< include _footer.qmd >}}" in (SITE / name).read_text(encoding="utf-8"), name


def test_a_daily_series_gets_daily_ticks():
    """Given room, Plot subdivides a daily series into hours -- and the wider the
    page, the more of them."""
    text = read_qmd()
    assert "function dayTicks(rows)" in text
    assert text.count("ticks: dayTicks(") >= 5


def test_summary_queries_carry_brand_so_two_chains_cannot_pool():
    """summary_daily is keyed on brand as well as country.

    Once a second chain publishes in a country there are two rows per day, so a
    query that neither selects nor groups by brand silently draws both as one
    series and sums their station counts -- the pooled figure this site does not
    make. Nothing else on the page reveals it, which is why it is pinned here.
    """
    shared = (ROOT / "site" / "_shared.qmd").read_text(encoding="utf-8")

    country_queries = shared.count("WHERE level = 'country'")
    assert country_queries >= 2, "expected the country-level summary queries"
    assert shared.count("ORDER BY country, brand, capture_date") == country_queries

    # brand is projected, so a consumer can tell the chains apart.
    assert shared.count("             brand,\n") == country_queries

    # And the station count is per chain, not a bare sum across rows.
    assert "function seriesOf(row)" in shared
    assert "function latestStationCount(rows, series)" in shared
    assert ".reduce((total, row) => total + (row.n_stations || 0), 0)" not in shared
