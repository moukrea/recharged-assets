#!/usr/bin/env python3
"""One-time import of the Jak 1 ESRGAN-edition 'pack-complet' masters.

Deduplicates the 294 in-game placements down to the physically distinct
material sets (canonical masters + 'recale' pixel variants), lays them out as

    raw/jak1/<group>/<name>/{albedo,normal,roughness,height}.png

and writes metadata/jak1/placements.json mapping every in-game
texture_replacements path to its material directory.

Idempotent: re-running overwrites deterministically.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict

MAP_SUFFIXES = {
    "albedo": "",
    "normal": "_normal",
    "roughness": "_roughness",
    "height": "_height",
}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def set_paths(pack_dir: str, placement_key: str) -> dict:
    """Return {semantic: absolute path} for the 4 maps of a placement."""
    stem = placement_key[: -len(".png")]
    d = os.path.dirname(stem)
    n = os.path.basename(stem)
    out = {}
    for semantic, suffix in MAP_SUFFIXES.items():
        out[semantic] = os.path.join(pack_dir, d, f"{n}{suffix}.png")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack-dir", required=True, help="export/pack-complet directory")
    ap.add_argument("--manifest", required=True, help="pack-complet-manifeste.json")
    ap.add_argument("--repo-root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    manifest = json.load(open(args.manifest))["entrees"]
    raw_root = os.path.join(args.repo_root, "raw", "jak1")
    meta_root = os.path.join(args.repo_root, "metadata", "jak1")

    # Group placements by canonical id, then split by physical content.
    groups = defaultdict(list)
    for key, entry in manifest.items():
        groups[entry["id"]].append((key, entry))

    placements = {}
    materials = {}
    n_copied = 0

    for canon_id, entries in sorted(groups.items()):
        # Hash the full 4-map set of every placement to find distinct contents.
        by_hash = {}
        for key, entry in entries:
            paths = set_paths(args.pack_dir, key)
            for sem, p in paths.items():
                if not os.path.isfile(p):
                    print(f"ERROR missing map {sem} for {key}: {p}", file=sys.stderr)
                    return 1
            set_hash = hashlib.sha256(
                "".join(sha256_file(paths[s]) for s in ("albedo", "normal", "roughness", "height")).encode()
            ).hexdigest()
            by_hash.setdefault(set_hash, []).append((key, entry, paths))

        # The canonical set is the one containing the placement key == id.png;
        # every other distinct content is a variant named after its first placement.
        canon_key = canon_id + ".png"
        for set_hash, members in by_hash.items():
            keys = [k for k, _, _ in members]
            is_canonical = canon_key in keys
            if is_canonical:
                material_id = canon_id
            else:
                # variant: use its first placement (sorted) as the material id
                material_id = sorted(keys)[0][: -len(".png")]
            _, entry0, paths0 = sorted(members)[0]
            dest_dir = os.path.join(raw_root, material_id)
            materials[material_id] = {
                "canonical_id": canon_id,
                "is_variant": not is_canonical,
                "recale": any(e.get("recale", False) for _, e, _ in members),
                "set_sha256": set_hash,
                "placements": sorted(keys),
            }
            for k in keys:
                placements[k] = material_id
            if not args.dry_run:
                os.makedirs(dest_dir, exist_ok=True)
                for sem in MAP_SUFFIXES:
                    dst = os.path.join(dest_dir, f"{sem}.png")
                    shutil.copy2(paths0[sem], dst)
                    n_copied += 1

    if not args.dry_run:
        os.makedirs(meta_root, exist_ok=True)
        with open(os.path.join(meta_root, "placements.json"), "w") as f:
            json.dump(
                {
                    "game": "jak1",
                    "source_pack": "Jak1_ESRGAN_Edition_v1.0.2 pack-complet",
                    "placements": dict(sorted(placements.items())),
                },
                f, indent=1, ensure_ascii=False,
            )
        with open(os.path.join(meta_root, "materials.json"), "w") as f:
            json.dump(dict(sorted(materials.items())), f, indent=1, ensure_ascii=False)

    variants = sum(1 for m in materials.values() if m["is_variant"])
    print(f"placements: {len(placements)}")
    print(f"materials:  {len(materials)} ({len(materials) - variants} canonical + {variants} variants)")
    print(f"files copied: {n_copied}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
