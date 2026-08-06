#!/usr/bin/env python3
"""Install a published asset pack into a Recharged game directory.

This is the reference implementation of the asset-manager install algorithm
(plan phase C): resolve profile × preset × features → shard set, download
only what is missing (resumable), verify size + SHA-256, then switch
`state.json` atomically. The engine's C++ installer (M2) must behave
identically — this tool is what makes the M1 managed tier usable today, and
it is the oracle the C++ implementation is checked against.

  install_pack.py --manifest <url|file> --profile pc-bc --preset default \\
                  --target <jak-project>/managed_assets/jak1 [--with-pbr]

Offline/interrupt safety: downloads land in `staging/` with `.part` files and
are only promoted once verified; `state.json` is replaced by rename, so a
kill at any point leaves the previous install intact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

CHUNK = 1 << 20


def fetch(url: str) -> bytes:
    if "://" not in url:
        return Path(url).read_bytes()
    with urllib.request.urlopen(url) as r:
        return r.read()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def download_resumable(url: str, dest: Path, expect_size: int) -> None:
    """HTTP Range resume onto a .part file, then rename into place."""
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > expect_size:  # corrupt leftover
        part.unlink()
        have = 0
    if have < expect_size:
        req = urllib.request.Request(url)
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req) as r, open(part, "ab" if have else "wb") as f:
                # a server ignoring Range restarts the file
                if have and r.status != 206:
                    f.seek(0)
                    f.truncate()
                while True:
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"download failed ({e.code}) {url}") from e
    part.rename(dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, help="release manifest URL or local path")
    ap.add_argument("--manifest-sha256", default="", help="expected hash (from assets.lock.json)")
    ap.add_argument("--profile", required=True,
                    choices=["pc-bc", "pc-bc-legacy", "android-etc2", "android-astc",
                             "rgba8-fallback"])
    ap.add_argument("--preset", required=True, choices=["low", "default", "bonkers"])
    ap.add_argument("--target", required=True, help="<game dir>/managed_assets/<game>")
    ap.add_argument("--with-pbr", action="store_true",
                    help="also install material-map shards (requires an OG_FEAT_PBR build)")
    ap.add_argument("--local-catalog", default="",
                    help="copy shards from a local build dir instead of downloading")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    raw = fetch(args.manifest)
    got = hashlib.sha256(raw).hexdigest()
    if args.manifest_sha256 and got != args.manifest_sha256:
        print(f"manifest sha256 mismatch: {got} != {args.manifest_sha256}", file=sys.stderr)
        return 1
    manifest = json.loads(raw)
    if manifest["schema_version"] != 1:
        print(f"unsupported schema_version {manifest['schema_version']}", file=sys.stderr)
        return 1

    # ---- resolve the wanted shard set ------------------------------------
    wanted = []
    skipped_features = 0
    for s in manifest["shards"]:
        if s["profile"] != args.profile or s["preset"] != args.preset:
            continue
        if s.get("requires_features") and not args.with_pbr:
            skipped_features += 1
            continue
        wanted.append(s)
    if not wanted:
        print(f"no shards for {args.profile}/{args.preset} in {manifest['asset_version']}",
              file=sys.stderr)
        return 1
    total = sum(s["size"] for s in wanted)
    print(f"{manifest['asset_version']} {args.profile}/{args.preset}: "
          f"{len(wanted)} shards, {total / 2**30:.2f} GiB"
          + (f" ({skipped_features} material shards skipped — pass --with-pbr)"
             if skipped_features else ""))

    target = Path(args.target).resolve()
    staging = target / "staging"
    if args.dry_run:
        for s in wanted:
            print(f"  would install {s['name']} ({s['size'] / 2**20:.1f} MB)")
        return 0

    free = shutil.disk_usage(target.parent if target.exists() else target.parent.parent).free
    if free < total * 1.1:
        print(f"not enough disk space: {free / 2**30:.1f} GiB free, need "
              f"{total * 1.1 / 2**30:.1f} GiB", file=sys.stderr)
        return 1

    staging.mkdir(parents=True, exist_ok=True)
    installed = []
    for i, s in enumerate(wanted, 1):
        final = target / s["name"]
        # content-addressed names: an identical file is already the right one
        if final.exists() and final.stat().st_size == s["size"]:
            print(f"  [{i}/{len(wanted)}] keep {s['name']}")
            installed.append(s["name"])
            continue
        tmp = staging / s["name"]
        print(f"  [{i}/{len(wanted)}] fetch {s['name']} ({s['size'] / 2**20:.1f} MB)", flush=True)
        if args.local_catalog:
            shutil.copy2(Path(args.local_catalog) / s["name"], tmp)
        else:
            download_resumable(s["url"], tmp, s["size"])
        if tmp.stat().st_size != s["size"] or sha256_file(tmp) != s["sha256"]:
            tmp.unlink(missing_ok=True)
            print(f"    VERIFY FAILED for {s['name']} — install aborted, previous state kept",
                  file=sys.stderr)
            return 1
        tmp.rename(final)
        installed.append(s["name"])

    # ---- atomic switch ---------------------------------------------------
    state = {
        "schema_version": 1,
        "asset_version": manifest["asset_version"],
        "profile": args.profile,
        "preset": args.preset,
        "verified": True,
        "shards": sorted(installed),
    }
    tmp_state = target / "state.json.new"
    tmp_state.write_text(json.dumps(state, indent=1))
    os.replace(tmp_state, target / "state.json")

    # ---- drop orphans (previous versions' shards) ------------------------
    keep = set(installed) | {"state.json"}
    removed = 0
    for f in target.iterdir():
        if f.is_file() and f.name not in keep and f.suffix == ".rpack":
            f.unlink()
            removed += 1
    shutil.rmtree(staging, ignore_errors=True)
    print(f"installed {len(installed)} shards into {target}"
          + (f", removed {removed} orphaned" if removed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
