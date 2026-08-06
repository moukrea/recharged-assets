#!/usr/bin/env python3
"""Derive per-material metadata (plan step 4, spec §5).

Sources, in authority order:
- metadata/jak1/{materials,placements}.json  (imported dedup facts)
- the masters themselves                      (dims, map presence)
- the local rework pipeline's tiling.json     (human-confirmed wrap verdicts)
- the reference ESRGAN pack directory         (per-texture esrgan_dims —
  metadata only, those PNGs are never imported)

Writes metadata/jak1/materials/<tpage>/<name>.json, each validating against
schemas/material-metadata.schema.json. Derivation PROPOSES; committed files
are the overridable truth — re-running preserves any `"derived": false`
files untouched.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

WRAP_OF_TILING = {"both": "repeat", "none": "clamp",
                  "horizontal": "repeat_x", "vertical": "repeat_y"}

MAP_DEFS = {
    "albedo": {"colorspace": "srgb-encoded", "channels": "rgb"},
    "normal": {"colorspace": "linear", "channels": "rg"},
    "roughness": {"colorspace": "linear", "channels": "r"},
    "height": {"colorspace": "linear", "channels": "r"},
}


def png_dims(p: Path) -> list[int]:
    with Image.open(p) as im:
        return [im.width, im.height]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--tiling", default="", help="path to the rework pipeline's tiling.json")
    ap.add_argument("--esrgan-dir", default="", help="reference ESRGAN texture_replacements dir")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    meta_dir = repo / "metadata" / "jak1"
    materials = json.loads((meta_dir / "materials.json").read_text())
    out_root = meta_dir / "materials"

    tiling = {}
    if args.tiling and Path(args.tiling).exists():
        tiling = json.loads(Path(args.tiling).read_text())["items"]
    esrgan = Path(args.esrgan_dir) if args.esrgan_dir else None

    try:
        import jsonschema
        schema = json.loads((repo / "schemas" / "material-metadata.schema.json").read_text())
    except ImportError:
        jsonschema = schema = None

    written = skipped = 0
    for mat_path, info in sorted(materials.items()):
        tpage, name = mat_path.split("/", 1)
        out = out_root / tpage / f"{name}.json"
        if out.exists() and json.loads(out.read_text()).get("derived") is False:
            skipped += 1
            continue

        mdir = repo / "raw" / "jak1" / mat_path
        maps = {}
        for sem, defn in MAP_DEFS.items():
            if (mdir / f"{sem}.png").exists():
                maps[sem] = dict(defn)
        master_dims = png_dims(mdir / "albedo.png")

        # wrap: canonical id first, then any placement with a verdict
        wrap = None
        for key in [info["canonical_id"], mat_path, *info["placements"]]:
            k = key[:-4] if key.endswith(".png") else key
            if k in tiling and tiling[k].get("mode") in WRAP_OF_TILING:
                wrap = WRAP_OF_TILING[tiling[k]["mode"]]
                break

        # esrgan dims: canonical placement first, then any placement
        esrgan_dims = None
        if esrgan:
            for key in [info["canonical_id"] + ".png",
                        *(p if p.endswith(".png") else p + ".png"
                          for p in info["placements"])]:
                p = esrgan / key
                if p.exists():
                    esrgan_dims = png_dims(p)
                    break

        doc = {
            "schema_version": 1,
            "id": f"jak1/{mat_path}",
            "game": "jak1",
            "original": {"tpage_name": tpage, "texture_name": name},
            "master_dims": master_dims,
            "alpha_mode": "none",
            "levels": sorted({p.split("/")[0] for p in info["placements"]}),
            "recale": bool(info.get("recale") and info.get("is_variant")),
            "maps": maps,
        }
        if wrap:
            doc["wrap_mode"] = wrap
        if esrgan_dims:
            doc["esrgan_dims"] = esrgan_dims
        if info.get("is_variant"):
            doc["variant_of"] = f"jak1/{info['canonical_id']}"

        if schema is not None:
            jsonschema.validate(doc, schema)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
        written += 1

    n_wrap = sum(1 for f in out_root.rglob("*.json")
                 if "wrap_mode" in json.loads(f.read_text()))
    n_esr = sum(1 for f in out_root.rglob("*.json")
                if "esrgan_dims" in json.loads(f.read_text()))
    print(f"written {written}, preserved-overrides {skipped}; "
          f"wrap_mode on {n_wrap}, esrgan_dims on {n_esr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
