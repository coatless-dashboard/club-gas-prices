"""Static file server that answers HTTP Range requests, for the site smoke test.

`python -m http.server` ignores the `Range` request header: it always replies
200 with the whole file, and advertises no `Accept-Ranges`. DuckDB-WASM reads
Parquet by asking for byte ranges (the footer first, then individual row
groups), and GitHub Pages answers those with 206 plus `Content-Range`.

Pages also gzips what it serves, INCLUDING `.parquet`, and then answers ranges
against the compressed bytes: `Content-Length` is the compressed size and a
range past it is a 416, however long the real file is. A reader that trusts the
advertised length reads the "end" of the file somewhere in the middle of the
gzip stream. `--emulate-pages` reproduces that, because serving plain bytes is
what let a site that cannot read its own Parquet pass this test.
"""

from __future__ import annotations

import argparse
import gzip
import mimetypes
import os
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

_EXTRA_TYPES = {
    ".parquet": "application/octet-stream",
    ".arrow": "application/octet-stream",
    ".wasm": "application/wasm",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".json": "application/json",
    ".csv": "text/csv",
    ".gz": "application/gzip",
    ".map": "application/json",
}


def guess_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _EXTRA_TYPES:
        return _EXTRA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """Return an inclusive (start, end) pair, or None when the header is unusable.

    Raises ValueError when the range is syntactically valid but unsatisfiable,
    which the caller turns into a 416.
    """
    match = _RANGE_RE.match(header.strip())
    if match is None:
        return None
    first, last = match.group(1), match.group(2)
    if first == "" and last == "":
        return None
    if first == "":
        length = int(last)
        if length == 0:
            raise ValueError("zero-length suffix range")
        start = max(0, size - length)
        return start, size - 1
    start = int(first)
    if start >= size:
        raise ValueError("start beyond end of file")
    end = size - 1 if last == "" else min(int(last), size - 1)
    if end < start:
        raise ValueError("end before start")
    return start, end


# What Pages compresses. It is content-type driven, and octet-stream -- which
# is what a .parquet is served as -- is in it.
_COMPRESSIBLE = (
    "text/",
    "application/json",
    "application/javascript",
    "application/octet-stream",
    "image/svg+xml",
)


class RangeRequestHandler(BaseHTTPRequestHandler):
    server_version = "ClubGasRangeServer/1.0"
    protocol_version = "HTTP/1.1"

    directory: Path = Path(".")
    prefix: str = "/"
    verbose: bool = False
    emulate_pages: bool = False

    def log_message(self, fmt: str, *args: object) -> None:
        if self.verbose:
            super().log_message(fmt, *args)

    def resolve(self) -> Path | None:
        raw = unquote(urlsplit(self.path).path)
        if not raw.startswith(self.prefix):
            return None
        rest = raw[len(self.prefix) :].lstrip("/")
        root = self.directory.resolve()
        target = (root / rest).resolve() if rest else root
        if target != root and root not in target.parents:
            return None
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            return None
        return target

    def do_HEAD(self) -> None:
        self.respond(body=False)

    def do_GET(self) -> None:
        self.respond(body=True)

    def respond(self, *, body: bool) -> None:
        target = self.resolve()
        if target is None:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        content_type = guess_type(target)
        header = self.headers.get("Range")
        accepts_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "")
        payload: bytes | None = None
        encoding: str | None = None

        if self.emulate_pages and content_type.startswith(_COMPRESSIBLE):
            # Pages stores the object gzipped and ranges over THAT, whatever the
            # request's Accept-Encoding says -- and Chrome sends `identity` on
            # every ranged XHR, which is how a reader ends up being handed
            # compressed bytes it never asked for. Only a request with no Range
            # at all gets decompressed on the way out.
            squeezed = gzip.compress(target.read_bytes())
            if header is not None or accepts_gzip:
                payload = squeezed
                encoding = "gzip" if accepts_gzip else None
            else:
                payload = target.read_bytes()

        size = len(payload) if payload is not None else target.stat().st_size
        start, end = 0, max(size - 1, 0)
        partial = False
        if header:
            try:
                parsed = parse_range(header, size)
            except ValueError:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return
            if parsed is not None:
                start, end = parsed
                partial = True
        length = (end - start + 1) if size else 0
        status = HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not body or length == 0:
            return
        if payload is not None:
            self.wfile.write(payload[start : end + 1])
            return
        with target.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def serve(
    directory: os.PathLike[str] | str,
    prefix: str,
    port: int = 0,
    *,
    verbose: bool = False,
    emulate_pages: bool = False,
) -> tuple[ThreadingHTTPServer, str]:
    """Start the server on a background thread and return it with its base URL."""
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    if not prefix.endswith("/"):
        prefix = prefix + "/"
    handler = type(
        "BoundRangeRequestHandler",
        (RangeRequestHandler,),
        {
            "directory": Path(directory),
            "prefix": prefix,
            "verbose": verbose,
            "emulate_pages": emulate_pages,
        },
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="range-server", daemon=True)
    thread.start()
    host, bound_port = httpd.server_address[0], httpd.server_address[1]
    return httpd, f"http://{host}:{bound_port}{prefix}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Range-capable static file server")
    parser.add_argument("--dir", default="_site", help="directory to serve")
    parser.add_argument("--prefix", default="/club-gas-prices/", help="URL prefix")
    parser.add_argument("--port", type=int, default=8080, help="port (0 picks a free one)")
    parser.add_argument(
        "--emulate-pages",
        action="store_true",
        help="gzip like GitHub Pages, and range over the compressed bytes",
    )
    args = parser.parse_args()
    httpd, base_url = serve(
        args.dir, args.prefix, args.port, verbose=True, emulate_pages=args.emulate_pages
    )
    print(f"serving {args.dir} at {base_url}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
