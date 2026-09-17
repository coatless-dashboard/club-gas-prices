#!/usr/bin/env python3
"""Check a `site-*` download against the collector's manifest, then unprefix it.

The data repository uploads the dashboard's five files into its `current`
release under a `site-` prefix, so one download pattern fetches exactly them,
and records each one's size and sha256 in manifest.json. `current` is rewritten
asset by asset, so a download that straddles a publish is possible and this is
what catches it. On success the files are left under the plain names the site
reads, and manifest.json is removed: it describes the dataset, not the site.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

# Kept in step with club_gas.sitedata.SITE_ASSETS in the data repository.
SITE_ASSETS = {
    "meta.json": "site-meta.json",
    "latest.json": "site-latest.json",
    "stations.json": "site-stations.json",
    "summary_daily.parquet": "site-summary-daily.parquet",
    "history.parquet": "site-history.parquet",
}


def main(argv: list[str]) -> int:
    root = pathlib.Path(argv[1])
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        print("manifest.json is missing from the download", file=sys.stderr)
        return 1
    assets = json.loads(manifest_path.read_text(encoding="utf-8")).get("assets") or {}

    for asset in SITE_ASSETS.values():
        entry = assets.get(asset)
        if entry is None:
            print(f"manifest.json has no entry for {asset}", file=sys.stderr)
            return 1
        path = root / asset
        if not path.exists():
            print(f"{asset} is missing from the download", file=sys.stderr)
            return 1
        data = path.read_bytes()
        if len(data) != entry["size"]:
            print(f"{asset}: size {len(data)} != manifest {entry['size']}", file=sys.stderr)
            return 1
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry["sha256"]:
            print(f"{asset}: sha256 {digest} != manifest {entry['sha256']}", file=sys.stderr)
            return 1

    for name, asset in SITE_ASSETS.items():
        (root / asset).replace(root / name)
    manifest_path.unlink()
    print(f"verified {len(SITE_ASSETS)} site assets against manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
