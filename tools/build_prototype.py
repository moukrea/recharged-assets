#!/usr/bin/env python3
"""Vertical prototype (plan step 3): a set of materials end-to-end —
semantic mips -> per-profile GPU encode -> KTX2 (validated) -> RPACK shards
-> full pack re-read + hash verification.

Usage: build_prototype.py --repo . --out out/proto [--preset bonkers]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import encode_ktx2  # noqa: E402
import mips as mipgen  # noqa: E402
import rpack  # noqa: E402
import shards  # noqa: E402
import stats  # noqa: E402

# The 4 village1 materials shared with the fork's bundled set + 3 covering
# the remaining spec §19 cases (other clusters, shrub/pris pages).
MATERIALS = [
    "village1-vis-tfrag/vil1-sages-stonewall-01",
    "village1-vis-tfrag/vil1-sages-strawroof-01",
    "village1-vis-tfrag/vil-beachrock",           # 2048x1024
    "village1-vis-tfrag/vil-beach-01",
    "beach-vis-tfrag/bch-outpostwall",
    "snow-vis-shrub/snow-icewall-01",
    "citadel-vis-tfrag/cit-temp-precursor-plain",
]

# profile -> map kind -> (format, channels)
PROFILES = {
    "pc-bc": {
        "albedo": ("bc7", "rgb"),
        "normal": ("bc5", "rg"),
        "roughness": ("bc4", "r"),
        "height": ("bc4", "r"),
    },
    "android-etc2": {
        "albedo": ("etc2_rgb", "rgb"),
        "normal": ("eac_rg11", "rg"),
        "roughness": ("eac_r11", "r"),
        "height": ("eac_r11", "r"),
    },
    # data maps stay EAC R11 per the bake-off (ASTC 6x6 no better, 4x4 = 2x size)
    "android-astc": {
        "albedo": ("astc_6x6", "rgb"),
        "normal": ("astc_4x4", "rg"),
        "roughness": ("eac_r11", "r"),
        "height": ("eac_r11", "r"),
    },
}

COLORSPACE = {"albedo": "srgb-encoded", "normal": "linear",
              "roughness": "linear", "height": "linear"}


def load01(png: Path, gray: bool) -> np.ndarray:
    img = Image.open(png)
    a = np.asarray(img.convert("L" if gray else "RGB"), dtype=np.float64) / 255.0
    return a


def chain_for(sem: str, arr01: np.ndarray) -> list[np.ndarray]:
    h, w = arr01.shape[:2]
    n = mipgen.level_count(w, h)
    if sem == "albedo":
        return mipgen.albedo_chain(arr01, n)
    if sem == "normal":
        return mipgen.normal_chain(arr01, n)
    return mipgen.data_chain(arr01, n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="out/proto")
    ap.add_argument("--preset", default="bonkers")
    ap.add_argument("--astc-quality", default="-thorough")
    args = ap.parse_args()

    missing = encode_ktx2.have_tools()
    if missing:
        print(f"missing tools: {missing}", file=sys.stderr)
        return 1

    repo = Path(args.repo).resolve()
    out = Path(args.out).resolve()
    (out / "ktx2").mkdir(parents=True, exist_ok=True)
    report = {"preset": args.preset, "profiles": {}, "shards": []}

    # ---- per material: mips + stats once, encode per profile -------------
    prepared = {}
    for mat in MATERIALS:
        mdir = repo / "raw" / "jak1" / mat
        if not mdir.is_dir():
            print(f"SKIP missing material {mat}", file=sys.stderr)
            continue
        maps = {}
        for sem in ("albedo", "normal", "roughness", "height"):
            png = mdir / f"{sem}.png"
            if not png.exists():
                continue
            arr = load01(png, gray=sem in ("roughness", "height"))
            maps[sem] = chain_for(sem, arr)
        entry_stats = {}
        if "normal" in maps:
            dx, dy = stats.normal_dc(np.asarray(
                Image.open(mdir / "normal.png").convert("RGB"), dtype=np.float64) / 255.0)
            entry_stats["normal_dc_x"] = round(dx, 6)
            entry_stats["normal_dc_y"] = round(dy, 6)
        if "height" in maps:
            hm = maps["height"][0]
            mean, norm = stats.height_stats(hm)
            entry_stats["height_mean"] = round(mean, 6)
            entry_stats["height_norm"] = round(norm, 6)
            entry_stats["height_lambda_tiles"] = round(stats.height_lambda_tiles(hm), 6)
        prepared[mat] = (maps, entry_stats)
        print(f"prepared {mat}: {list(maps)} stats={entry_stats}")

    for profile, table in PROFILES.items():
        t0 = time.perf_counter()
        entries_by_family: dict = {}
        sizes = {}
        for mat, (maps, entry_stats) in prepared.items():
            tpage, name = mat.split("/", 1)
            for sem, chain in maps.items():
                fmt_name, channels = table[sem]
                ktx_path = out / "ktx2" / profile / f"{mat.replace('/', '_')}.{sem}.ktx2"
                encode_ktx2.encode_map(chain, channels, fmt_name, ktx_path,
                                       astc_quality=args.astc_quality)
                payload = ktx_path.read_bytes()
                sizes[f"{mat}/{sem}"] = len(payload)
                fam = shards.shard_family("jak1", profile, args.preset, sem, tpage)
                e = rpack.Entry(
                    id=f"jak1/{mat}", key=mat, map=sem,
                    format="VK_FORMAT_" + encode_ktx2.FORMATS[fmt_name].vk,
                    width=chain[0].shape[1], height=chain[0].shape[0],
                    mip_levels=len(chain), colorspace=COLORSPACE[sem],
                    channels=channels, payload=payload,
                    stats=entry_stats if sem in ("normal", "height") and entry_stats else None)
                entries_by_family.setdefault(fam, []).append(e)

        prof_report = {"encode_s": round(time.perf_counter() - t0, 1),
                       "ktx2_sizes": sizes, "shards": []}
        for fam, entries in sorted(entries_by_family.items()):
            game, prof, preset, group, cluster = fam
            meta = rpack.PackMeta(game, prof, preset, group, cluster)
            tmp = out / f"tmp-{prof}-{group}-{cluster}.rpack"
            sha = rpack.write(tmp, meta, entries)
            final = out / shards.shard_name(fam, sha)
            tmp.rename(final)
            # full re-read + per-entry hash verification
            r = rpack.Reader(final)
            r.verify_all()
            prof_report["shards"].append(
                {"name": final.name, "entries": len(entries),
                 "size": final.stat().st_size, "sha256": sha})
            print(f"shard {final.name}: {len(entries)} entries, "
                  f"{final.stat().st_size / 1e6:.1f} MB")
        report["profiles"][profile] = prof_report

    (out / "report.json").write_text(json.dumps(report, indent=1))
    print(f"\nreport -> {out / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
