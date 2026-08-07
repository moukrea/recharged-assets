#!/usr/bin/env python3
"""Generate the release manifest (plan A4) from a built catalog dir.

Shard reuse: with --previous-manifest, any shard whose sha256 the previous
manifest already references keeps its old immutable URL/release_tag and is
listed in the 'reused' output so the release job skips uploading it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True, help="build_catalog.py --out dir")
    ap.add_argument("--tag", required=True, help="assets-vX.Y.Z")
    ap.add_argument("--repo-slug", default="moukrea/recharged-assets")
    ap.add_argument("--min-recharged", default="0.1.0")
    ap.add_argument("--min-loader", type=int, default=1)
    ap.add_argument("--previous-manifest", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    catalog = Path(args.catalog)
    report = json.loads((catalog / "build-report.json").read_text())
    if report["gate_failures"]:
        print("refusing: build report has gate failures", file=sys.stderr)
        return 1

    prev_by_sha = {}
    if args.previous_manifest:
        prev = json.loads(Path(args.previous_manifest).read_text())
        prev_by_sha = {s["sha256"]: s for s in prev["shards"]}

    shards_out, reused, to_upload = [], [], []
    profiles, presets = set(), set()
    for s in sorted(report["shards"], key=lambda x: x["name"]):
        game, profile, preset, group, cluster, _part = s["family"]
        profiles.add(profile)
        presets.add(preset)
        prev_s = prev_by_sha.get(s["sha256"])
        if prev_s:
            url, tag = prev_s["url"], prev_s.get("release_tag", args.tag)
            reused.append(s["name"])
        else:
            url = (f"https://github.com/{args.repo_slug}/releases/download/"
                   f"{args.tag}/{s['name']}")
            tag = args.tag
            to_upload.append(s["name"])
        rec = {"name": s["name"], "game": game, "profile": profile,
               "preset": preset, "group": group, "cluster": cluster,
               "sha256": s["sha256"], "size": s["size"], "url": url,
               "release_tag": tag, "entry_count": s["entries"]}
        if group == "material":
            rec["requires_features"] = ["pbr"]
        shards_out.append(rec)

    manifest = {
        "schema_version": 1,
        "asset_version": args.tag,
        "games": ["jak1"],
        "engine_compat": {
            "min_recharged_version": args.min_recharged,
            "max_recharged_version": None,
            "min_loader_version": args.min_loader,
            "required_features": [],
        },
        "profiles": sorted(profiles),
        "presets": sorted(presets),
        "shards": shards_out,
    }

    try:
        import jsonschema
        schema = json.loads((Path(__file__).resolve().parent.parent /
                             "schemas" / "manifest.schema.json").read_text())
        jsonschema.validate(manifest, schema)
    except ImportError:
        pass

    Path(args.out).write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({"shards": len(shards_out), "reused": len(reused),
                      "to_upload": len(to_upload)}))
    (Path(args.out).parent / "to-upload.txt").write_text("\n".join(to_upload) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
