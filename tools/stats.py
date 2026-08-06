"""Offline mirrors of the engine's decode-time per-map statistics (plan D6).

The Recharged loader measures these from decoded PNG pixels
(jak-project game/graphics/opengl_renderer/loader/LoaderStages.cpp); with
compressed payloads the pipeline computes them here, on the exact
preset-resolution level-0 pixels, and ships them in the RPACK index.

Keep in lockstep with the engine:
- normal_dc  <- LoaderStages.cpp normal-DC measurement (mean tangent-space
  gradient clamp(n.xy / max(n.z, 0.05), +-4))
- height_mean/norm <- height histogram stats (mean + robust p2/p98
  half-range, norm = clamp(0.5 / half, 0.5, 16))
- height_lambda_tiles <- measure_height_lambda_tiles (mip-energy spectrum:
  box-halve until variance halves, log-interpolated crossing, clamped
  [1/1024, 1])
The M1 engine spike must assert CPU-vs-pipeline equality on the bundled set.
"""

from __future__ import annotations

import numpy as np


def normal_dc(rgb01: np.ndarray) -> tuple[float, float]:
    v = rgb01 * 2.0 - 1.0
    z = np.maximum(v[..., 2], 0.05)
    gx = np.clip(v[..., 0] / z, -4.0, 4.0)
    gy = np.clip(v[..., 1] / z, -4.0, 4.0)
    return float(np.mean(gx)), float(np.mean(gy))


def height_stats(gray01: np.ndarray) -> tuple[float, float]:
    mean = float(np.mean(gray01))
    p2, p98 = np.percentile(gray01, [2.0, 98.0])
    half = max((float(p98) - float(p2)) * 0.5, 1e-6)
    norm = float(np.clip(0.5 / half, 0.5, 16.0))
    return mean, norm


def height_lambda_tiles(gray01: np.ndarray) -> float:
    """Characteristic feature wavelength in tiles (1 tile = whole texture)."""
    a = gray01.astype(np.float64)
    # analysis cap mirrors the engine's subsampling bound
    while max(a.shape) > 1024:
        a = 0.25 * (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2])
    v0 = float(np.var(a))
    if v0 <= 1e-12:
        return 1.0 / 4.0  # engine default for flat maps
    size0 = max(a.shape)
    prev_v, prev_size = v0, size0
    while min(a.shape) >= 2:
        a = 0.25 * (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2])
        v = float(np.var(a))
        size = max(a.shape)
        if v < 0.5 * v0:
            # log-interpolate the crossing between prev and current level
            if prev_v <= 0.5 * v0 or prev_v <= v:
                cross = size
            else:
                t = (np.log(prev_v) - np.log(0.5 * v0)) / (np.log(prev_v) - np.log(v))
                cross = prev_size * (0.5 ** t)
            lam = 2.0 * (1.0 / cross) * size0 / size0  # feature size in texels/size0
            lam = 2.0 / cross  # wavelength as a fraction of the texture
            return float(np.clip(lam, 1.0 / 1024.0, 1.0))
        prev_v, prev_size = v, size
    return 1.0
