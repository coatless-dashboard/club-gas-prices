"""Contract tests for the Render workflow and the script it runs.

These assert on the literal text of the workflow file rather than on a parsed
YAML tree, because the rules being enforced are about the literal text: GitHub
rejects a bare `!` at the start of an `if:` value, and an action pin is only a
pin if the ref written in the file is a commit SHA. A YAML parser normalizes
quoting and would hide both.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "render.yml"
VERIFY = ROOT / ".github" / "verify-site-data.py"

SITE_ASSETS = {
    "meta.json": "site-meta.json",
    "latest.json": "site-latest.json",
    "stations.json": "site-stations.json",
    "summary_daily.parquet": "site-summary-daily.parquet",
    "history.parquet": "site-history.parquet",
}


def read() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_render_conventions():
    text = read()
    for line in text.splitlines():
        match = re.match(r"\s*if:\s*(.*)$", line)
        assert not (match and match.group(1).startswith("!")), line.strip()
    pins = re.findall(r"uses:\s*(\S+)@(\S+)(?:\s+#\s*(\S+))?", text)
    assert pins
    for action, ref, comment in pins:
        if action.startswith("actions/"):
            # GitHub's own actions, on a floating major tag: the account that
            # would have to be compromised to move one is GitHub's own, and the
            # tag is how security fixes arrive without a commit here.
            assert re.fullmatch(r"v\d+", ref), (action, ref)
        else:
            # Everything third-party is pinned by commit SHA with the version in
            # a trailing comment. A tag can be moved onto different code.
            assert re.fullmatch(r"[0-9a-f]{40}", ref), (action, ref)
            assert comment and comment.startswith("v"), (action, comment)
    assert "concurrency:\n  group: render\n  cancel-in-progress: true\n" in text
    assert "permissions:\n  contents: read\n  pages: write\n  id-token: write\n" in text
    assert "runs-on: ubuntu-latest" in text
    assert "    timeout-minutes: 30\n" in text
    assert "version: 1.10.18" in text


def test_render_only_reads_the_data_repository():
    """This repository holds no capture code and must never be given a writer token."""
    text = read()
    assert text.startswith("name: Render\n")
    assert "DATA_REPO: coatless-data/club-gas-prices" in text
    assert "CLUB_GAS_WRITER" not in text
    assert "permissions:\n  contents: read\n" in text
    # A GITHUB_TOKEN cannot start a workflow across repositories, so the render
    # polls. Reading a public repository's releases needs no token of its own.
    assert '--repo "$DATA_REPO"' in text
    assert "workflow_run" not in text


def test_render_fetches_the_site_assets_by_prefix():
    text = read()
    # Narrow patterns: a bare 'site-*' also matches the collector's reserved
    # `<name>.next-<token>` and `.old-<token>` mid-write temporaries, and this
    # directory is copied wholesale into the published site.
    assert "--pattern 'site-*.json' --pattern 'site-*.parquet'" in text
    assert "--pattern 'site-*'" not in text
    assert "python3 .github/verify-site-data.py site/data" in text
    # `.next-*` and `.old-*` are mid-write names on the data repository's
    # release that only its own reader may see.
    assert ".next-" not in text
    assert ".old-" not in text


def test_render_stages_no_source_files_and_smoke_tests():
    text = read()
    assert "quarto render site" in text
    assert "cp -R site/data _site/data" in text
    assert "find _site \\( -name '*.qmd' -o -name '*.scss' \\) -print" in text
    assert "uv sync --locked --group smoke" in text
    assert "uv run python tests/smoke/smoke_site.py _site" in text
    assert "uses: actions/upload-pages-artifact@v5" in text
    assert "uses: actions/deploy-pages@v5" in text


def test_render_deploys_only_from_the_default_branch():
    text = read()
    assert "  push:\n" in text
    push = text.split("  push:\n", 1)[1].split("\n  workflow_dispatch:", 1)[0]
    assert "branches: [main]" in push
    assert "      - site/**" in push
    assert "      - .github/workflows/render.yml" in push


def test_every_deploying_step_is_behind_the_same_gate():
    """One plan step decides; every step that can deploy reads only that.

    Two independent conditions is how a run ends up rendering but not
    deploying, or deploying an empty `_site`.
    """
    text = read()
    gate = "if: ${{ steps.plan.outputs.render == 'true' }}"
    for step in ("Render the site", "Stage _site", "Upload the Pages artifact", "Deploy to Pages"):
        block = text.split(f"- name: {step}\n", 1)[1].split("      - name:", 1)[0]
        assert gate in block, step


def write_release(directory: Path) -> None:
    payload = {
        "site-meta.json": b'{"capture_id":"2026-09-15T1817Z"}\n',
        "site-latest.json": b"[]\n",
        "site-stations.json": b"[]\n",
        "site-summary-daily.parquet": b"PAR1-stand-in-for-a-parquet-file",
        "site-history.parquet": b"PAR1-stand-in-for-another-one",
    }
    assets = {}
    for name, body in payload.items():
        (directory / name).write_bytes(body)
        assets[name] = {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
    # The real manifest also covers the six raw dataset assets, which this
    # repository neither downloads nor verifies.
    assets["club-gas-all.parquet"] = {"sha256": "0" * 64, "size": 1}
    (directory / "manifest.json").write_text(json.dumps({"assets": assets}), encoding="utf-8")


def run_verify(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY), str(tmp_path / "data")],
        capture_output=True,
        text=True,
        check=False,
    )


def test_verify_accepts_a_consistent_release_and_unprefixes_it(tmp_path):
    (tmp_path / "data").mkdir()
    write_release(tmp_path / "data")
    done = run_verify(tmp_path)
    assert done.returncode == 0, done.stderr
    assert "verified 5 site assets" in done.stdout
    assert {p.name for p in (tmp_path / "data").iterdir()} == set(SITE_ASSETS)


def test_verify_rejects_a_torn_release(tmp_path):
    # A release read while the collector is renaming assets can mix two
    # captures, which is what the retries in the workflow ride out.
    (tmp_path / "data").mkdir()
    write_release(tmp_path / "data")
    (tmp_path / "data" / "site-latest.json").write_bytes(b'[{"station_key":"US-1364"}]\n')
    done = run_verify(tmp_path)
    assert done.returncode != 0
    assert "site-latest.json" in done.stderr


def test_verify_rejects_a_missing_asset(tmp_path):
    (tmp_path / "data").mkdir()
    write_release(tmp_path / "data")
    (tmp_path / "data" / "site-history.parquet").unlink()
    done = run_verify(tmp_path)
    assert done.returncode != 0
    assert "site-history.parquet is missing" in done.stderr


def test_verify_rejects_a_release_that_predates_the_site_assets(tmp_path):
    """`current` written before the collector learned to publish these."""
    (tmp_path / "data").mkdir()
    write_release(tmp_path / "data")
    (tmp_path / "data" / "manifest.json").write_text(
        json.dumps({"assets": {"club-gas-all.parquet": {"sha256": "0" * 64, "size": 1}}}),
        encoding="utf-8",
    )
    done = run_verify(tmp_path)
    assert done.returncode != 0
    assert "no entry for site-meta.json" in done.stderr


def test_verify_removes_a_stray_that_would_otherwise_reach_pages():
    """`current` holds mid-write temporaries. Staging copies this directory
    wholesale, so anything left behind is published."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "data"
        root.mkdir()
        write_release(root)
        stray = root / "site-history.parquet.old-abc123"
        stray.write_bytes(b"a stale multi-megabyte parquet")

        done = subprocess.run(
            [sys.executable, str(VERIFY), str(root)], capture_output=True, text=True, check=False
        )

        assert done.returncode == 0, done.stderr
        assert "removing stray" in done.stderr
        assert {p.name for p in root.iterdir()} == set(SITE_ASSETS)


def test_the_render_cron_does_not_land_inside_a_publish():
    """Capture runs at :17 every six hours and rewrites `current` asset by asset."""
    text = read()
    assert '# - cron: "20 */3 * * *"' not in text.replace("  #", "#")
    assert '"50 */3 * * *"' in text
    # And it is actually on: the block ships commented out until the collector
    # has a `current` release to render, which is easy to leave that way.
    assert re.search(r'(?m)^  schedule:\n    - cron: "50 \*/3 \* \* \*"$', text)
