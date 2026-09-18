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
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "render.yml"
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"
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


def step_script(name: str, **expressions: str) -> str:
    """A render step's `run: |` block, as the script the runner would execute.

    Each `${{ ... }}` the step reads is given as a keyword, its dots written as
    underscores, so a test can run the step under the event it is about.
    """
    block = read().split(f"      - name: {name}\n", 1)[1].split("\n      - name:", 1)[0]
    body = block.split("        run: |\n", 1)[1]
    script = textwrap.dedent(body)
    for key, value in expressions.items():
        script = script.replace("${{ " + key.replace("__", ".") + " }}", value)
    assert "${{" not in script, "a step expression was left unset"
    return script


def run_step(script: str, cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=cwd,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


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


def test_staging_leaves_exactly_one_copy_of_the_data(tmp_path):
    """Quarto copies the files the pages name into _site/data as it renders, so
    by the time the stage step runs the directory exists, and `cp -R` into it
    nested a second copy of all five at _site/data/data. Pages then served
    history.parquet twice, and that is the file that grows without bound."""
    for directory in (tmp_path / "site" / "data", tmp_path / "_site" / "data"):
        directory.mkdir(parents=True)
        for name in SITE_ASSETS:
            (directory / name).write_bytes(f"{name} as verified".encode())
    (tmp_path / "_site" / "index.html").write_text("<html></html>", encoding="utf-8")

    done = run_step(step_script("Stage _site"), tmp_path)

    assert done.returncode == 0, done.stdout + done.stderr
    staged = tmp_path / "_site" / "data"
    assert not (staged / "data").exists()
    assert {p.name for p in staged.iterdir()} == set(SITE_ASSETS)
    for name in SITE_ASSETS:
        assert (staged / name).read_bytes() == f"{name} as verified".encode()


def test_the_pull_request_render_stages_the_data_the_same_way():
    """The Test workflow's smoke job stages the sample data the way Render
    stages the release, so it cannot pass on a layout Pages never gets."""
    for path in (WORKFLOW, TEST_WORKFLOW):
        text = path.read_text(encoding="utf-8")
        copies = text.count("cp -R site/data _site/data")
        assert copies == 1, path.name
        assert text.count("rm -rf _site/data\n          cp -R site/data _site/data") == copies


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


# A capture as the release's meta.json records it, and the same capture again
# after a Rebuild rewrote its site assets: same capture_id, new built_at_utc.
BUILT = b'{"built_at_utc":"2026-09-18T16:24:17Z","capture_id":"2026-09-18T1623Z"}\n'
REBUILT = b'{"built_at_utc":"2026-09-18T19:02:40Z","capture_id":"2026-09-18T1623Z"}\n'
NEXT = b'{"built_at_utc":"2026-09-18T22:24:09Z","capture_id":"2026-09-18T2217Z"}\n'

# Stands in for curl against Pages: serves $LIVE_META, to the -o file or to
# stdout, or fails the way `curl -f` does when there is nothing to fetch.
FAKE_CURL = """#!/bin/sh
[ -n "$LIVE_META" ] || exit 22
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [ -n "$out" ]; then cp "$LIVE_META" "$out"; else cat "$LIVE_META"; fi
"""


def plan(tmp_path: Path, *, release: bytes, live: bytes | None, event: str = "schedule") -> str:
    """Run the plan step against `release` with `live` on Pages; its decision."""
    (tmp_path / "site" / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "site" / "data" / "meta.json").write_bytes(release)
    tools = tmp_path / "bin"
    tools.mkdir(exist_ok=True)
    (tools / "curl").write_text(FAKE_CURL, encoding="utf-8")
    (tools / "curl").chmod(0o755)
    served = ""
    if live is not None:
        (tmp_path / "live.json").write_bytes(live)
        served = str(tmp_path / "live.json")
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir(exist_ok=True)
    output = tmp_path / "output"
    output.write_text("", encoding="utf-8")
    script = step_script(
        "Decide whether to render",
        steps__data__outputs__has_data="true",
        github__event_name=event,
    )
    done = run_step(
        script,
        tmp_path,
        PATH=f"{tools}{os.pathsep}{os.environ['PATH']}",
        LIVE_META=served,
        GITHUB_OUTPUT=str(output),
        RUNNER_TEMP=str(runner_temp),
        SITE_URL="https://pages.invalid/club-gas-prices",
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return output.read_text(encoding="utf-8").strip()


def test_a_scheduled_run_skips_only_what_is_already_deployed(tmp_path):
    assert plan(tmp_path, release=BUILT, live=BUILT) == "render=false"
    assert plan(tmp_path, release=NEXT, live=BUILT) == "render=true"
    # Nothing on Pages yet, or Pages unreachable: render rather than guess.
    assert plan(tmp_path, release=BUILT, live=None) == "render=true"


def test_a_rebuild_reaches_the_site_without_waiting_for_a_capture(tmp_path):
    """A Rebuild rewrites the site assets under the capture they already had.
    Compared on capture_id alone, every scheduled run called its corrections
    deployed until the next capture landed, most of a day on a late schedule."""
    assert plan(tmp_path, release=REBUILT, live=BUILT) == "render=true"


def test_a_push_or_a_manual_run_always_renders(tmp_path):
    for event in ("push", "workflow_dispatch"):
        assert plan(tmp_path, release=BUILT, live=BUILT, event=event) == "render=true"


def test_the_schedule_keeps_itself_on():
    """GitHub turns off a public repository's schedules after 60 days without
    activity, and nothing in this repository commits. Once a week a job
    re-enables the workflow through the API, the way the widely used keepalive
    actions do, with the one permission that needs and nothing else."""
    text = read()
    schedule = text.split("  schedule:\n", 1)[1].split("\n  push:\n", 1)[0]
    crons = re.findall(r'(?m)^    - cron: "([^"]+)"$', schedule)
    assert crons[0] == "50 */3 * * *"
    jobs = text.split("\njobs:\n", 1)[1]
    render_job, keepalive = jobs.split("\n  keepalive:\n", 1)
    # Its own weekly tick, which is one of the crons and not the render's.
    tick = re.search(r"github\.event\.schedule == '([^']+)'", keepalive).group(1)
    assert tick in crons and tick != "50 */3 * * *"
    assert "if: ${{ github.event_name == 'schedule' && github.event.schedule == " in keepalive
    assert (
        "run: gh api -X PUT repos/${{ github.repository }}/actions/workflows/render.yml/enable"
        in keepalive
    )
    assert "GH_TOKEN: ${{ github.token }}" in keepalive
    # actions: write on this job alone: not the workflow, not the render job.
    assert "    permissions:\n      actions: write\n" in keepalive
    assert text.count("actions: write") == 1
    assert "actions:" not in render_job
    workflow_permissions = text.split("\npermissions:\n", 1)[1].split("\n\n", 1)[0]
    assert "actions" not in workflow_permissions
    # The comment says what the call is, and what GitHub has not promised.
    above = text.split("\n  keepalive:\n", 1)[0].rsplit("\n\n", 1)[1]
    comment = " ".join(line.strip().removeprefix("# ") for line in above.splitlines())
    assert "the widely used keepalive actions" in comment
    assert "does not document the enable call as resetting the 60-day timer" in comment
