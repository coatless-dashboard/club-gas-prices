"""The smoke test's static server. It binds 127.0.0.1 only; no network."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

# tests/smoke/ sits beside this file and is not an importable package, so the
# directory itself goes on sys.path.
SMOKE = Path(__file__).resolve().parent / "smoke"
sys.path.insert(0, str(SMOKE))

from range_server import guess_type, parse_range, serve  # noqa: E402

PAYLOAD = bytes(range(256)) * 4  # 1024 deterministic bytes


@pytest.fixture
def site(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
    (tmp_path / "data" / "history.parquet").write_bytes(PAYLOAD)
    return tmp_path


@pytest.fixture
def base_url(site: Path):
    httpd, url = serve(site, "/costco-gas-prices/")
    try:
        yield url
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_parse_range_forms():
    assert parse_range("bytes=0-9", 1024) == (0, 9)
    assert parse_range("bytes=1000-", 1024) == (1000, 1023)
    assert parse_range("bytes=-8", 1024) == (1016, 1023)
    assert parse_range("bytes=0-99999", 1024) == (0, 1023)
    assert parse_range("bytes=x-y", 1024) is None
    with pytest.raises(ValueError):
        parse_range("bytes=2048-", 1024)


def test_guess_type_covers_the_site_payloads(tmp_path: Path):
    assert guess_type(tmp_path / "a.parquet") == "application/octet-stream"
    assert guess_type(tmp_path / "a.json") == "application/json"
    assert guess_type(tmp_path / "a.wasm") == "application/wasm"
    assert guess_type(tmp_path / "a.html") == "text/html"


def test_directory_serves_index_and_advertises_ranges(base_url: str):
    response = httpx.get(base_url)
    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    assert response.text.startswith("<!doctype")


def test_whole_file(base_url: str):
    response = httpx.get(base_url + "data/history.parquet")
    assert response.status_code == 200
    assert response.content == PAYLOAD
    assert response.headers["content-type"] == "application/octet-stream"


def test_range_gets_206_and_content_range(base_url: str):
    # DuckDB-WASM reads Parquet by byte range: a 200 with the whole body here
    # would hand it the wrong bytes.
    response = httpx.get(base_url + "data/history.parquet", headers={"Range": "bytes=0-9"})
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-9/{len(PAYLOAD)}"
    assert response.content == PAYLOAD[:10]


def test_head_with_range_gets_206(base_url: str):
    # This is the exact behavior the server exists for: DuckDB-WASM only opens a
    # Parquet file over HTTP when a ranged HEAD returns 206. `python -m
    # http.server` answers every HEAD with a plain 200 and no Content-Range,
    # which sends DuckDB-WASM down the whole-file fallback path.
    response = httpx.head(base_url + "data/history.parquet", headers={"Range": "bytes=0-9"})
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-9/{len(PAYLOAD)}"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.content == b""


def test_open_ended_and_suffix_ranges(base_url: str):
    open_ended = httpx.get(base_url + "data/history.parquet", headers={"Range": "bytes=1000-"})
    assert open_ended.status_code == 206
    assert open_ended.content == PAYLOAD[1000:]

    suffix = httpx.get(base_url + "data/history.parquet", headers={"Range": "bytes=-8"})
    assert suffix.status_code == 206
    assert suffix.content == PAYLOAD[-8:]
    assert suffix.headers["content-range"] == f"bytes 1016-1023/{len(PAYLOAD)}"


def test_unsatisfiable_range_gets_416(base_url: str):
    response = httpx.get(base_url + "data/history.parquet", headers={"Range": "bytes=99999-"})
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(PAYLOAD)}"


def test_paths_outside_the_prefix_and_the_root_are_404(base_url: str):
    origin = base_url[: base_url.index("/costco-gas-prices/")]
    assert httpx.get(origin + "/index.html").status_code == 404
    assert httpx.get(base_url + "missing.json").status_code == 404
    assert httpx.get(base_url + "../../etc/hosts").status_code == 404
