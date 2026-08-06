"""Semantic mip-chain generation (plan D7).

All filtering is a 2x2 box on power-of-two levels (wrap-safe by
construction), but the SPACE it happens in depends on the semantic:

- albedo:   decode with the engine's own gamma model (pow 2.2 — the shaders
            hand-decode with that exact curve, see AUDIT.md), average in
            linear light, re-encode. Fixes today's wrong-space driver mips.
- normal:   decode to vectors, average, renormalize per mip.
- data (roughness/height/masks): plain linear average.

Returns float arrays in [0,1]; callers quantize once at the end.
"""

from __future__ import annotations

import numpy as np

ENGINE_GAMMA = 2.2  # matches pbr_fused.glsl pow(T0.rgb, 2.2)


def _box2(a: np.ndarray) -> np.ndarray:
    """2x2 box downsample; odd dims drop to max(1, n//2) by edge-averaging."""
    h, w = a.shape[:2]
    nh, nw = max(1, h // 2), max(1, w // 2)
    a = a[: nh * 2 if h > 1 else 1, : nw * 2 if w > 1 else 1]
    if h > 1:
        a = (a[0::2] + a[1::2]) * 0.5
    if w > 1:
        a = (a[:, 0::2] + a[:, 1::2]) * 0.5
    return a


def level_count(w: int, h: int, min_size: int = 1) -> int:
    n = 1
    while max(w, h) > min_size and (w > 1 or h > 1):
        w, h = max(1, w // 2), max(1, h // 2)
        n += 1
    return n


def albedo_chain(rgb01: np.ndarray, levels: int) -> list[np.ndarray]:
    lin = np.power(rgb01, ENGINE_GAMMA)
    out = [rgb01]
    for _ in range(levels - 1):
        lin = _box2(lin)
        out.append(np.power(lin, 1.0 / ENGINE_GAMMA))
    return out


def normal_chain(rgb01: np.ndarray, levels: int) -> list[np.ndarray]:
    v = rgb01 * 2.0 - 1.0
    out = [rgb01]
    for _ in range(levels - 1):
        v = _box2(v)
        n = np.linalg.norm(v, axis=-1, keepdims=True)
        v = v / np.maximum(n, 1e-9)
        out.append((v + 1.0) * 0.5)
    return out


def data_chain(gray01: np.ndarray, levels: int) -> list[np.ndarray]:
    a = gray01
    out = [gray01]
    for _ in range(levels - 1):
        a = _box2(a)
        out.append(a)
    return out


def quantize(level01: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(level01 * 255.0), 0, 255).astype(np.uint8)
