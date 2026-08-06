#!/usr/bin/env python3
"""Encoder bake-off (spec §9.3): compare candidate GPU-texture encoders
programmatically — per semantic, per format — on real masters, then pin the
winners in the toolchain image.

For each (texture, format) pair every registered encoder encodes, the result
is decoded back to PNG, and semantic-aware metrics are computed (PSNR + max
error for color, mean/p99 angular error for normals, MAE for single-channel
data). Wall time is measured around the encode call only.

Usage:
  benchmark_encoders.py --repo . --out out/bakeoff [--quick]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

# ---------------------------------------------------------------- adapters

def _run(cmd: list[str], cwd: Path | None = None) -> float:
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    dt = time.perf_counter() - t0
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd))}\n{r.stdout}\n{r.stderr}")
    return dt


class Encoder:
    """encode() returns (encode_seconds, decoded_png_path)."""
    name: str

    def encode(self, src: Path, fmt: str, work: Path) -> tuple[float, Path]:
        raise NotImplementedError


class Compressonator(Encoder):
    name = "compressonator"
    FMT = {"bc1": "BC1", "bc4": "BC4", "bc5": "BC5", "bc7": "BC7",
           "etc2_rgb": "ETC2_RGB"}

    def __init__(self, cli: str):
        self.cli = cli

    def encode(self, src: Path, fmt: str, work: Path) -> tuple[float, Path]:
        dds = work / f"{src.stem}.{fmt}.dds"
        # decode to BMP: the Linux CLI can't write PNG ("format is unsupported
        # for the file extension") and exits 0 while saying so
        dec = work / f"{src.stem}.{fmt}.dec.bmp"
        # the release wrapper is a shell script without a shebang — run via bash
        dt = _run(["bash", self.cli, "-fd", self.FMT[fmt], "-NumThreads", "8",
                   str(src), str(dds)])
        _run(["bash", self.cli, str(dds), str(dec)])
        if not dec.exists():
            raise RuntimeError("compressonator decode produced no output")
        return dt, dec


class Bc7enc(Encoder):
    name = "bc7enc_rdo"
    FMT = {"bc1": ["-1"], "bc4": ["-4"], "bc5": ["-5"], "bc7": []}

    def __init__(self, cli: str):
        self.cli = cli

    def encode(self, src: Path, fmt: str, work: Path) -> tuple[float, Path]:
        dds = work / f"{src.stem}.{fmt}.dds"
        dec = work / f"{src.stem}.{fmt}.dec.png"
        dt = _run([self.cli, "-q", *self.FMT[fmt], str(src), str(dds), str(dec)],
                  cwd=work)
        return dt, dec


class Etcpak(Encoder):
    # etcpak's BCn output could not be verified through its own -v viewer
    # (garbage round-trips for bc1/bc4/bc5 in the first probe run), so it
    # competes only on its home turf: ETC2/EAC.
    name = "etcpak"
    FMT = {"etc2_rgb": "etc2_rgb", "eac_r11": "etc2_r", "eac_rg11": "etc2_rg"}

    def __init__(self, cli: str):
        self.cli = cli

    def encode(self, src: Path, fmt: str, work: Path) -> tuple[float, Path]:
        pvr = work / f"{src.stem}.{fmt}.pvr"
        dec = work / f"{src.stem}.{fmt}.dec.png"
        dt = _run([self.cli, "-c", self.FMT[fmt], "--linear", str(src), str(pvr)])
        _run([self.cli, "-v", str(pvr), str(dec)])
        return dt, dec


class Astcenc(Encoder):
    name = "astcenc"
    FMT = {"astc_4x4": "4x4", "astc_5x5": "5x5", "astc_6x6": "6x6"}

    def __init__(self, cli: str, quality: str = "-thorough"):
        self.cli = cli
        self.quality = quality
        self.name = f"astcenc{quality}"

    def encode(self, src: Path, fmt: str, work: Path) -> tuple[float, Path]:
        astc = work / f"{src.stem}.{fmt}.astc"
        dec = work / f"{src.stem}.{fmt}.dec.png"
        dt = _run([self.cli, "-cl", str(src), str(astc), self.FMT[fmt],
                   self.quality, "-j", "8"])
        _run([self.cli, "-dl", str(astc), str(dec)])
        return dt, dec


# ---------------------------------------------------------------- metrics

def load_rgba(p: Path) -> np.ndarray:
    return np.asarray(Image.open(p).convert("RGBA"), dtype=np.float64)


def color_metrics(ref: np.ndarray, dec: np.ndarray) -> dict:
    diff = ref[..., :3] - dec[..., :3]
    mse = float(np.mean(diff ** 2))
    psnr = float("inf") if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)
    return {"psnr_db": round(psnr, 2), "max_err": int(np.max(np.abs(diff)))}


def gray_metrics(ref: np.ndarray, dec: np.ndarray) -> dict:
    diff = ref[..., 0] - dec[..., 0]
    return {"mae": round(float(np.mean(np.abs(diff))), 3),
            "max_err": int(np.max(np.abs(diff))),
            "psnr_db": round(10 * np.log10(255.0 ** 2 /
                                           max(np.mean(diff ** 2), 1e-9)), 2)}


def _to_vectors(img: np.ndarray, xy_only: bool) -> np.ndarray:
    v = img[..., :3] / 255.0 * 2.0 - 1.0
    if xy_only:
        z2 = np.clip(1.0 - v[..., 0] ** 2 - v[..., 1] ** 2, 0.0, 1.0)
        v = np.stack([v[..., 0], v[..., 1], np.sqrt(z2)], axis=-1)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-9)


def normal_metrics(ref: np.ndarray, dec: np.ndarray, dec_xy_only: bool) -> dict:
    a = _to_vectors(ref, xy_only=False)
    b = _to_vectors(dec, xy_only=dec_xy_only)
    dot = np.clip(np.sum(a * b, axis=-1), -1.0, 1.0)
    ang = np.degrees(np.arccos(dot))
    return {"ang_mean_deg": round(float(np.mean(ang)), 3),
            "ang_p99_deg": round(float(np.percentile(ang, 99)), 3),
            "ang_max_deg": round(float(np.max(ang)), 3)}


# ---------------------------------------------------------------- harness

# semantic -> (formats to test, metric kind)
PLAN = {
    "albedo": (["bc7", "bc1", "etc2_rgb", "astc_6x6"], "color"),
    "normal": (["bc5", "eac_rg11", "astc_4x4"], "normal"),
    # spec §4.3: ASTC vs EAC R11 must be compared programmatically for data maps
    "roughness": (["bc4", "eac_r11", "astc_4x4", "astc_6x6"], "gray"),
    "height": (["bc4", "eac_r11", "astc_4x4", "astc_6x6"], "gray"),
}

# formats where only the first two channels survive (X/Y normals)
XY_FORMATS = {"bc5", "eac_rg11"}


def pick_samples(repo: Path, quick: bool) -> list[tuple[str, str, Path]]:
    """(material, semantic, png). Fixed representative set."""
    mats = [
        "village1-vis-tfrag/vil-beach-01",         # shared with bundled set
        "village1-vis-tfrag/vil1-sages-stonewall-01",
        "village1-vis-tfrag/vil-beachrock",        # 2048x1024 case
    ]
    if quick:
        mats = mats[:1]
    out = []
    for m in mats:
        d = repo / "raw" / "jak1" / m
        if not d.is_dir():
            continue
        for sem in PLAN:
            p = d / f"{sem}.png"
            if p.exists():
                out.append((m, sem, p))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="out/bakeoff")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--compressonator", default="", help="path to compressonatorcli (disabled by default; see comment in main)")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    encoders: list[Encoder] = []
    # Compressonator is registered only on explicit request: the 4.5.52 Linux
    # CLI cannot save any decodable image format ("saving image failed ...
    # unsupported for the file extension" for png AND bmp, with exit code 0),
    # so no independent round-trip metric can be computed from it.
    if args.compressonator and shutil.which(args.compressonator):
        encoders.append(Compressonator(args.compressonator))
    for cls, exe in ((Bc7enc, "bc7enc"), (Etcpak, "etcpak")):
        if shutil.which(exe):
            encoders.append(cls(exe))
    if shutil.which("astcenc"):
        encoders.append(Astcenc("astcenc", "-thorough"))
        if not args.quick:
            encoders.append(Astcenc("astcenc", "-medium"))

    results = []
    samples = pick_samples(repo, args.quick)
    for mat, sem, png in samples:
        formats, metric_kind = PLAN[sem]
        # Some encoders mishandle mode-L PNGs — feed everyone RGB-expanded
        # sources so the comparison is encoder quality, not PNG plumbing.
        src_img = Image.open(png)
        if src_img.mode != "RGB":
            png_rgb = out / f"src.{mat.replace('/', '_')}.{sem}.png"
            src_img.convert("RGB").save(png_rgb)
            png = png_rgb
        ref = load_rgba(png)
        for fmt in formats:
            for enc in encoders:
                if fmt not in enc.FMT:
                    continue
                work = out / enc.name
                work.mkdir(exist_ok=True)
                try:
                    dt, dec_png = enc.encode(png, fmt, work)
                    dec = load_rgba(dec_png)
                    if dec.shape[:2] != ref.shape[:2]:
                        raise RuntimeError("decoded size mismatch")
                    if metric_kind == "color":
                        m = color_metrics(ref, dec)
                    elif metric_kind == "gray":
                        m = gray_metrics(ref, dec)
                    else:
                        m = normal_metrics(ref, dec, fmt in XY_FORMATS)
                    row = {"material": mat, "semantic": sem, "format": fmt,
                           "encoder": enc.name, "encode_s": round(dt, 2), **m}
                except Exception as e:  # a candidate failing IS a result
                    row = {"material": mat, "semantic": sem, "format": fmt,
                           "encoder": enc.name, "error": str(e)[-400:]}
                results.append(row)
                print(json.dumps(row), flush=True)

    (out / "results.json").write_text(json.dumps(results, indent=1))
    print(f"\n{len(results)} rows -> {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
