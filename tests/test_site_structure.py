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
