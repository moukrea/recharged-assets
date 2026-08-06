#!/usr/bin/env python3
"""Full-catalog build (plan phase A1/A2): metadata-driven, cached, gated.

For every material (or --materials subset) × profile × preset:
semantic mip chain from the master → preset sub-chain → GPU encode → KTX2
(validated) with a content-addressed cache → level-0 round-trip quality
gates → RPACK shards + build report.

Cache key = sha256(source map) × TOOLCHAIN_ID × policy hash (format, preset
dims, mip policy). A cache hit skips mips+encode+gates entirely, which is
what makes the PR pipeline incremental and release rebuilds cheap.

Exit codes: 0 ok, 2 quality-gate failures (report lists format_overrides
proposals — a human commits overrides; the build never downgrades silently).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import encode_ktx2  # noqa: E402
import mips as mipgen  # noqa: E402
import presets as presetmod  # noqa: E402
import rpack  # noqa: E402
import shards  # noqa: E402
import stats  # noqa: E402

TOOLCHAIN_ID = "tc1"  # bump when toolchain/Dockerfile pins change

PROFILES = {
    "pc-bc": {"albedo": ("bc7", "rgb"), "normal": ("bc5", "rg"),
              "roughness": ("bc4", "r"), "height": ("bc4", "r")},
    "android-etc2": {"albedo": ("etc2_rgb", "rgb"), "normal": ("eac_rg11", "rg"),
                     "roughness": ("eac_r11", "r"), "height": ("eac_r11", "r")},
    "android-astc": {"albedo": ("astc_6x6", "rgb"), "normal": ("astc_4x4", "rg"),
                     "roughness": ("eac_r11", "r"), "height": ("eac_r11", "r")},
}
COLORSPACE = {"albedo": "srgb-encoded", "normal": "linear",
              "roughness": "linear", "height": "linear"}


# ---------------------------------------------------------------- metrics

def _color_psnr(ref: np.ndarray, dec: np.ndarray) -> float:
    diff = ref[..., :3].astype(np.float64) - dec[..., :3].astype(np.float64)
    mse = float(np.mean(diff ** 2))
    return float("inf") if mse == 0 else float(10 * np.log10(255.0 ** 2 / mse))


def _gray_mae(ref: np.ndarray, dec: np.ndarray) -> float:
    return float(np.mean(np.abs(ref[..., 0].astype(np.float64) -
                                dec[..., 0].astype(np.float64))))


def _normal_ang(ref: np.ndarray, dec: np.ndarray) -> tuple[float, float]:
    def vec(img, xy_only):
        v = img[..., :3].astype(np.float64) / 255.0 * 2 - 1
        if xy_only:
            z2 = np.clip(1 - v[..., 0] ** 2 - v[..., 1] ** 2, 0, 1)
            v = np.stack([v[..., 0], v[..., 1], np.sqrt(z2)], -1)
        return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-9)
    dot = np.clip(np.sum(vec(ref, False) * vec(dec, True), -1), -1, 1)
    ang = np.degrees(np.arccos(dot))
    return float(np.mean(ang)), float(np.percentile(ang, 99))


def _gate_ok_from_metrics(sem: str, fmt: str, metrics: dict,
                          thresholds: dict) -> bool:
    t = thresholds[sem]
    if sem == "albedo":
        return bool(metrics["psnr_db"] >= t["min"].get(fmt, 0))
    if sem == "normal":
        return bool(metrics["ang_mean_deg"] <= t["max"].get(fmt, 99)
                    and metrics["ang_p99_deg"] <= t["p99_max"].get(fmt, 999))
    return bool(metrics["mae"] <= t["max"].get(fmt, 255))


def _gate(sem: str, fmt: str, ref_rgba: np.ndarray, dec_rgba: np.ndarray,
          thresholds: dict) -> tuple[dict, bool]:
    t = thresholds[sem]
    if sem == "albedo":
        v = _color_psnr(ref_rgba, dec_rgba)
        ok = bool(v >= t["min"].get(fmt, 0))
        return {"psnr_db": round(v, 2)}, ok
    if sem == "normal":
        mean, p99 = _normal_ang(ref_rgba, dec_rgba)
        ok = bool(mean <= t["max"].get(fmt, 99) and p99 <= t["p99_max"].get(fmt, 999))
        return {"ang_mean_deg": round(mean, 3), "ang_p99_deg": round(p99, 2)}, ok
    v = _gray_mae(ref_rgba, dec_rgba)
    ok = bool(v <= t["max"].get(fmt, 255))
    return {"mae": round(v, 3)}, ok


# ---------------------------------------------------------------- worker

def _preset_subchain(chain: list[np.ndarray], dims: tuple[int, int]) -> list[np.ndarray]:
    for i, lvl in enumerate(chain):
        if (lvl.shape[1], lvl.shape[0]) == dims:
            return chain[i:]
    raise RuntimeError(f"preset dims {dims} not on the mip chain")


def build_one(job: dict) -> dict:
    """One (material, map) unit: chain once, encode all profile×preset combos."""
    repo = Path(job["repo"])
    cache = Path(job["cache"])
    out = Path(job["out"])
    thresholds = job["thresholds"]
    meta = job["meta"]
    sem = job["sem"]
    mat = job["mat"]

    src = repo / "raw" / "jak1" / mat / f"{sem}.png"
    src_bytes = src.read_bytes()
    src_sha = hashlib.sha256(src_bytes).hexdigest()

    img = Image.open(src)
    gray = sem in ("roughness", "height")
    arr = np.asarray(img.convert("L" if gray else "RGB"), dtype=np.float64) / 255.0
    master_dims = (img.width, img.height)
    chain = None  # built lazily on first cache miss

    results = []
    for profile in job["profiles"]:
        fmt_name, channels = PROFILES[profile][sem]
        for preset in job["presets"]:
            pdims = presetmod.preset_dims(preset, master_dims,
                                          tuple(meta["esrgan_dims"]) if meta.get("esrgan_dims") else None)
            policy = f"{TOOLCHAIN_ID}|{fmt_name}|{channels}|{pdims}|mips1|{job['astc_quality']}"
            key = hashlib.sha256((src_sha + policy).encode()).hexdigest()
            cached = cache / key[:2] / f"{key}.ktx2"
            gates_cached = cached.with_suffix(".gates.json")
            rec = {"mat": mat, "sem": sem, "profile": profile, "preset": preset,
                   "format": fmt_name, "dims": pdims, "cache_key": key}
            if cached.exists() and gates_cached.exists():
                rec.update(json.loads(gates_cached.read_text()))
                # gate verdicts are re-evaluated against the CURRENT
                # thresholds — recalibration must not require re-encoding
                rec["gate_ok"] = _gate_ok_from_metrics(sem, fmt_name,
                                                       rec["metrics"], thresholds)
                rec["cache"] = "hit"
            else:
                if chain is None:
                    n = mipgen.level_count(master_dims[0], master_dims[1])
                    if sem == "albedo":
                        chain = mipgen.albedo_chain(arr, n)
                    elif sem == "normal":
                        chain = mipgen.normal_chain(arr, n)
                    else:
                        chain = mipgen.data_chain(arr, n)
                sub = _preset_subchain(chain, pdims)
                ref0 = np.zeros((sub[0].shape[0], sub[0].shape[1], 4), dtype=np.uint8)
                q = mipgen.quantize(sub[0])
                if q.ndim == 2:
                    q = q[..., None]
                ref0[..., :q.shape[-1]] = q
                cached.parent.mkdir(parents=True, exist_ok=True)
                dec0 = encode_ktx2.encode_map(sub, channels, fmt_name, cached,
                                              astc_quality=job["astc_quality"],
                                              capture_level0=True)
                metrics, ok = _gate(sem, fmt_name, ref0, dec0, thresholds)
                # stats on the preset's level 0 (what the engine samples)
                st = {}
                if sem == "normal":
                    dx, dy = stats.normal_dc(sub[0])
                    st = {"normal_dc_x": round(dx, 6), "normal_dc_y": round(dy, 6)}
                elif sem == "height":
                    mean, norm = stats.height_stats(sub[0])
                    st = {"height_mean": round(mean, 6), "height_norm": round(norm, 6),
                          "height_lambda_tiles": round(stats.height_lambda_tiles(sub[0]), 6)}
                payload_info = {"metrics": metrics, "gate_ok": ok, "stats": st,
                                "mip_levels": len(sub)}
                gates_cached.write_text(json.dumps(payload_info))
                rec.update(payload_info)
                rec["cache"] = "miss"
            results.append(rec)
    return {"mat": mat, "sem": sem, "results": results}


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="out/catalog")
    ap.add_argument("--cache", default="out/cache")
    ap.add_argument("--profiles", default="pc-bc,android-etc2,android-astc")
    ap.add_argument("--presets", default="low,default,bonkers")
    ap.add_argument("--materials", default="", help="comma-separated subset (tpage/name)")
    ap.add_argument("--astc-quality", default="-thorough")
    ap.add_argument("--jobs", type=int, default=max(1, mp.cpu_count() - 2))
    ap.add_argument("--no-shards", action="store_true", help="encode+gate only")
    args = ap.parse_args()

    missing = encode_ktx2.have_tools()
    if missing:
        print(f"missing tools: {missing}", file=sys.stderr)
        return 1

    repo = Path(args.repo).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    thresholds = json.loads((repo / "tools" / "validation-thresholds.json").read_text())

    subset = set(args.materials.split(",")) if args.materials else None
    jobs = []
    metas = {}
    for f in sorted((repo / "metadata" / "jak1" / "materials").rglob("*.json")):
        meta = json.loads(f.read_text())
        mat = meta["id"].removeprefix("jak1/")
        if subset and mat not in subset:
            continue
        metas[mat] = meta
        for sem in meta["maps"]:
            jobs.append({"repo": str(repo), "cache": str(Path(args.cache).resolve()),
                         "out": str(out), "thresholds": thresholds, "meta": meta,
                         "sem": sem, "mat": mat,
                         "profiles": args.profiles.split(","),
                         "presets": args.presets.split(","),
                         "astc_quality": args.astc_quality})

    print(f"{len(jobs)} (material,map) units × {len(args.profiles.split(','))} profiles "
          f"× {len(args.presets.split(','))} presets, {args.jobs} workers")
    t0 = time.perf_counter()
    all_results = []
    with mp.Pool(args.jobs) as pool:
        for i, res in enumerate(pool.imap_unordered(build_one, jobs)):
            all_results.extend(res["results"])
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(jobs)} units, {time.perf_counter() - t0:.0f}s")

    failures = [r for r in all_results if not r.get("gate_ok", True)]
    hits = sum(1 for r in all_results if r.get("cache") == "hit")
    print(f"encoded {len(all_results)} outputs in {time.perf_counter() - t0:.0f}s "
          f"({hits} cache hits), {len(failures)} gate failures")

    report = {"toolchain": TOOLCHAIN_ID, "gate_failures": [], "shards": [],
              "cache_hits": hits, "outputs": len(all_results)}
    ladder = thresholds.get("ladder", {})
    for r in failures:
        proposal = ladder.get(r["profile"], {}).get(r["sem"])
        report["gate_failures"].append({**{k: r[k] for k in
                                           ("mat", "sem", "profile", "preset", "format", "metrics")},
                                        "override_proposal": proposal})

    # ---- shard assembly (unless suppressed) -------------------------------
    # One family at a time: payloads are only resident for the shard being
    # written (the full catalog is several GB — holding everything OOMs).
    if not args.no_shards:
        cache_dir = Path(args.cache).resolve()
        by_family: dict = {}
        for r in all_results:
            tpage = r["mat"].split("/")[0]
            fam = shards.shard_family("jak1", r["profile"], r["preset"], r["sem"], tpage)
            by_family.setdefault(fam, []).append(r)
        for fam, recs in sorted(by_family.items()):
            game, prof, preset, group, cluster = fam
            entries = []
            for r in recs:
                key = r["cache_key"]
                ktx = cache_dir / key[:2] / f"{key}.ktx2"
                gates = json.loads(ktx.with_suffix(".gates.json").read_text())
                entries.append(rpack.Entry(
                    id=f"jak1/{r['mat']}", key=r["mat"], map=r["sem"],
                    format="VK_FORMAT_" + encode_ktx2.FORMATS[r["format"]].vk,
                    width=r["dims"][0], height=r["dims"][1],
                    mip_levels=gates["mip_levels"], colorspace=COLORSPACE[r["sem"]],
                    channels=PROFILES[r["profile"]][r["sem"]][1],
                    wrap_mode=metas[r["mat"]].get("wrap_mode", "repeat"),
                    payload=ktx.read_bytes(),
                    stats=gates["stats"] or None))
            meta = rpack.PackMeta(game, prof, preset, group, cluster)
            tmp = out / f"tmp-{prof}-{preset}-{group}-{cluster}.rpack"
            sha = rpack.write(tmp, meta, entries)
            del entries
            final = out / shards.shard_name(fam, sha)
            tmp.rename(final)
            report["shards"].append({"name": final.name, "family": list(fam),
                                     "entries": len(recs), "sha256": sha,
                                     "size": final.stat().st_size})
        print(f"{len(report['shards'])} shards written to {out}", flush=True)

    (out / "build-report.json").write_text(json.dumps(report, indent=1))
    if failures:
        print(f"\nGATE FAILURES ({len(failures)}) — override proposals in build-report.json",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
