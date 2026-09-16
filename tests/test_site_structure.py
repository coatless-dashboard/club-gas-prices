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
        first = chunk.strip().splitlines()[1][:60]
        assert "Plot.plot(" not in chunk, f"hidden chunk builds a chart: {first}"


def test_no_plot_scheme_the_pinned_plot_build_does_not_know():
    # Quarto's OJS stdlib pins @observablehq/plot 0.6.11, whose ordinal schemes
    # do not include "observable10": naming it throws `unknown ordinal scheme`
    # and turns the whole cell into an error box. A scheme is always a string
    # literal, so the page may still say the name in a comment.
    assert '"observable10"' not in read_qmd()
