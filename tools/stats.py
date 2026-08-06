"""Offline mirrors of the engine's decode-time per-map statistics (plan D6).

The Recharged loader measures these from decoded PNG pixels; with compressed
payloads the pipeline computes them here, on the exact preset-resolution
level-0 pixels, and ships them in the RPACK index.

These are TRANSCRIPTIONS of the engine's algorithms, not re-derivations —
they must stay bit-comparable with:
  jak-project game/graphics/opengl_renderer/loader/LoaderStages.cpp
    * normal DC              : mean of clamp(n.xy / max(n.z, 0.05), +-4)
    * height mean / norm     : 256-bin cumulative histogram, p2/p98,
                               half = max(p98 - mean, mean - p2), floor 2/255,
                               norm = clamp(0.5 / half, 0.5, 16)
    * height_lambda_tiles    : measure_height_lambda_tiles() — subsample to
                               <=1024, box-halve until variance <= 0.5*var0,
                               log-interpolated crossing, lambda_texels =
                               2^(l*+1), /max(cw0,ch0), clamp [1/1024, 1]
Every input is quantized to bytes first, exactly like the engine, which
decodes 8-bit PNG data. tests/test_stats_engine_parity.py holds a second,
literal loop-for-loop transcription and asserts the two agree.
"""

from __future__ import annotations

import math

import numpy as np


def _to_bytes(gray01: np.ndarray) -> np.ndarray:
    """Quantize to the 8-bit domain the engine actually measures."""
    if gray01.dtype == np.uint8:
        return gray01
    return np.clip(np.rint(np.asarray(gray01, dtype=np.float64) * 255.0), 0, 255).astype(np.uint8)


def normal_dc(rgb01: np.ndarray) -> tuple[float, float]:
    """Mean tangent-space surface gradient (the shader subtracts this)."""
    b = np.clip(np.rint(np.asarray(rgb01, dtype=np.float64) * 255.0), 0, 255)
    v = b * (2.0 / 255.0) - 1.0
    z = np.maximum(v[..., 2], 0.05)
    gx = np.clip(v[..., 0] / z, -4.0, 4.0)
    gy = np.clip(v[..., 1] / z, -4.0, 4.0)
    return float(np.mean(gx)), float(np.mean(gy))


def height_stats(gray01: np.ndarray) -> tuple[float, float]:
    """(height_mean, height_norm) — the shader does (h-mean)*norm+0.5."""
    b = _to_bytes(gray01).ravel()
    npx = b.size
    if npx == 0:
        return 0.5, 1.0
    mean = float(b.sum() / npx / 255.0)
    hist = np.bincount(b, minlength=256)
    cum = np.cumsum(hist)
    lo_target = int(npx * 0.02)  # C++ (u64)(npx * 0.02): truncation
    hi_target = int(npx * 0.98)
    # first bin whose cumulative count reaches the target (engine loop order)
    p2_b = int(np.argmax(cum >= lo_target)) if bool((cum >= lo_target).any()) else 0
    p98_b = int(np.argmax(cum >= hi_target)) if bool((cum >= hi_target).any()) else 255
    p2 = p2_b / 255.0
    p98 = p98_b / 255.0
    half = max(p98 - mean, mean - p2)
    half = max(half, 2.0 / 255.0)
    return mean, float(min(max(0.5 / half, 0.5), 16.0))


def height_lambda_tiles(gray01: np.ndarray) -> float:
    """Characteristic feature wavelength in tiles (1 tile = whole texture)."""
    b = _to_bytes(gray01)
    h, w = b.shape[:2]
    if w <= 0 or h <= 0:
        return 0.25
    step = max(1, max(w, h) // 1024)
    cw0 = max(1, (w + step - 1) // step)
    ch0 = max(1, (h + step - 1) // step)
    buf = (b[: ch0 * step : step, : cw0 * step : step].astype(np.float64)) / 255.0

    def variance(a: np.ndarray) -> float:
        if a.size == 0:
            return 0.0
        mean = float(a.mean())
        return max(float((a * a).mean()) - mean * mean, 0.0)

    var0 = variance(buf)
    if var0 < 1e-8:
        return 0.25
    target = 0.5 * var0
    var_prev = var0
    cw, ch = cw0, ch0
    l_last = 0
    l_star = 0.0
    crossed = False
    level = 1
    while cw >= 2 and ch >= 2 and level <= 12:
        nw, nh = cw // 2, ch // 2  # truncate odd dims, like the engine
        buf = 0.25 * (buf[0 : 2 * nh : 2, 0 : 2 * nw : 2] + buf[0 : 2 * nh : 2, 1 : 2 * nw : 2]
                      + buf[1 : 2 * nh : 2, 0 : 2 * nw : 2] + buf[1 : 2 * nh : 2, 1 : 2 * nw : 2])
        cw, ch = nw, nh
        l_last = level
        var_l = variance(buf)
        if var_l <= target:
            t = 0.0
            if var_prev > 0.0 and var_l > 0.0:
                denom = math.log(var_prev) - math.log(var_l)
                t = (math.log(var_prev) - math.log(target)) / max(denom, 1e-12)
            l_star = (level - 1) + min(max(t, 0.0), 1.0)
            crossed = True
            break
        var_prev = var_l
        level += 1
    if not crossed:
        l_star = float(l_last)
    lambda_texels = 2.0 ** (l_star + 1.0)
    lambda_tiles = lambda_texels / float(max(cw0, ch0))
    return float(min(max(lambda_tiles, 1.0 / 1024.0), 1.0))
