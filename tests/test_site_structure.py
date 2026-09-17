"""Static guards on the dashboard source. They need no browser."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QMD = ROOT / "site" / "index.qmd"
CUSTOM = ROOT / "site" / "custom.scss"
DARK = ROOT / "site" / "dark.scss"

PAGE_HEADINGS = ["# {.sidebar}", "# Map", "# Compare", "# Trends", "# Station", "# About"]


def read_qmd() -> str:
    return QMD.read_text(encoding="utf-8")


def test_front_matter_declares_the_dashboard_format_and_both_themes():
    text = read_qmd()
    front = text.split("---")[1]
    assert "dashboard:" in front
    assert "respect-user-color-scheme: true" in front
    assert "light: [default, custom.scss]" in front
    assert "dark: [default, custom.scss, dark.scss]" in front
    assert "echo: false" in front


def test_front_matter_has_nav_buttons_to_the_repository_and_the_data():
    front = read_qmd().split("---")[1]
    assert "https://github.com/coatless-dashboard/costco-gas-prices" in front
    assert "releases/tag/current" in front


def test_the_five_pages_and_the_sidebar_are_present_in_order():
    lines = [line.rstrip() for line in read_qmd().splitlines()]
    found = [line for line in lines if line in PAGE_HEADINGS]
    assert found == PAGE_HEADINGS


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


def test_the_theme_and_db_gates_are_generators():
    text = read_qmd()
    assert 'body.classList.contains("quarto-dark")' in text
    assert "dbWanted = Generators.observe(" in text
    assert "db = dbWanted" in text


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
    assert 'history.pushState({}, "", "?station=" + encodeURIComponent(key) + "#station")' in text
    assert 'QuartoDashboardUtils.showPage("#station")' in text
    assert "selectedStation = Generators.observe(" in text


def test_station_history_is_always_filtered_by_station_key():
    for match in re.finditer(r"FROM history\b(.*?)`", read_qmd(), re.S):
        assert "WHERE station_key IN (" in match.group(1)


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
        assert 'z: "country"' in block.split("})")[0]


def test_dots_stand_down_once_they_stop_marking_anything():
    text = read_qmd()
    assert "function showDots(" in text
    assert "showDots(days.length)" in text


def test_the_sample_uses_keys_the_pipeline_would_emit():
    """A sample key the collector could never produce ends up in a URL somebody
    shares. MX and TW are keyed on the warehouse number, not the branch name."""
    import json as _json

    sample = (ROOT / "tests" / "fixtures" / "site_sample.py").read_text()
    for wrong in ('"MX-Mexicali"', '"TW-Chungli"'):
        assert wrong not in sample, wrong
    data = ROOT / "site" / "data" / "stations.json"
    if data.exists():
        for row in _json.loads(data.read_text()):
            country, _, rest = row["station_key"].partition("-")
            if country in ("MX", "TW", "AU"):
                assert rest.isdigit(), f"{row['station_key']} is not a warehouse number"
