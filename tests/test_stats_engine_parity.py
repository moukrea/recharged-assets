"""Gate B6: the pipeline's offline statistics must equal what the engine
measures from the same pixels.

`tools/stats.py` is a vectorized numpy implementation. The reference below is
a LITERAL loop-for-loop transcription of the C++ in
  jak-project game/graphics/opengl_renderer/loader/LoaderStages.cpp
(the normal-DC loop, the 256-bin height histogram, and
measure_height_lambda_tiles). Two independent implementations of the same
spec must agree — if this test fails, one of them drifted, and the shipped
pack metadata would no longer describe what the shader assumes.
"""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import stats  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Literal transcription of the engine (plain loops, byte domain).

def engine_normal_dc(rgba: np.ndarray) -> tuple[float, float]:
    sx = sy = 0.0
    n = 0
    flat = rgba.reshape(-1, rgba.shape[-1])
    for px in flat:
        nx = px[0] * (2.0 / 255.0) - 1.0
        ny = px[1] * (2.0 / 255.0) - 1.0
        nz = max(px[2] * (2.0 / 255.0) - 1.0, 0.05)
        sx += min(max(nx / nz, -4.0), 4.0)
        sy += min(max(ny / nz, -4.0), 4.0)
        n += 1
    return (sx / n, sy / n) if n else (0.0, 0.0)


def engine_height_stats(gray_bytes: np.ndarray) -> tuple[float, float]:
    hist = [0] * 256
    total = 0
    npx = 0
    for v in gray_bytes.ravel():
        hist[int(v)] += 1
        total += int(v)
        npx += 1
    if not npx:
        return 0.5, 1.0
    mean = total / npx / 255.0
    lo_target = int(npx * 0.02)
    hi_target = int(npx * 0.98)
    cum = 0
    p2_b, p98_b = 0, 255
    got_p2 = got_p98 = False
    for b in range(256):
        cum += hist[b]
        if not got_p2 and cum >= lo_target:
            p2_b, got_p2 = b, True
        if not got_p98 and cum >= hi_target:
            p98_b, got_p98 = b, True
    p2, p98 = p2_b / 255.0, p98_b / 255.0
    half = max(p98 - mean, mean - p2)
    half = max(half, 2.0 / 255.0)
    return mean, min(max(0.5 / half, 0.5), 16.0)


def engine_lambda_tiles(gray_bytes: np.ndarray) -> float:
    h, w = gray_bytes.shape[:2]
    step = max(1, max(w, h) // 1024)
    cw0 = max(1, (w + step - 1) // step)
    ch0 = max(1, (h + step - 1) // step)
    buf = [[gray_bytes[y * step][x * step] / 255.0 for x in range(cw0)] for y in range(ch0)]

    def variance(mat):
        vals = [v for row in mat for v in row]
        if not vals:
            return 0.0
        n = len(vals)
        s = sum(vals)
        s2 = sum(v * v for v in vals)
        mean = s / n
        return max(s2 / n - mean * mean, 0.0)

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
        nw, nh = cw // 2, ch // 2
        down = [[0.25 * (buf[2 * y][2 * x] + buf[2 * y][2 * x + 1]
                         + buf[2 * y + 1][2 * x] + buf[2 * y + 1][2 * x + 1])
                 for x in range(nw)] for y in range(nh)]
        buf = down
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
    return min(max((2.0 ** (l_star + 1.0)) / max(cw0, ch0), 1.0 / 1024.0), 1.0)


# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rng():
    return np.random.default_rng(20260806)


def test_normal_dc_matches_engine(rng):
    for _ in range(3):
        rgba = rng.integers(0, 256, size=(24, 24, 4), dtype=np.uint8)
        ex, ey = engine_normal_dc(rgba)
        gx, gy = stats.normal_dc(rgba[..., :3].astype(np.float64) / 255.0)
        assert abs(gx - ex) < 1e-9
        assert abs(gy - ey) < 1e-9


def test_height_stats_match_engine(rng):
    cases = [
        rng.integers(0, 256, size=(32, 32), dtype=np.uint8),           # uniform noise
        np.full((16, 16), 200, dtype=np.uint8),                        # constant (floor path)
        (np.tile(np.linspace(16, 120, 64), (32, 1))).astype(np.uint8),  # narrow ramp
        np.clip(rng.normal(80, 12, size=(48, 48)), 0, 255).astype(np.uint8),
    ]
    for img in cases:
        em, en = engine_height_stats(img)
        gm, gn = stats.height_stats(img.astype(np.float64) / 255.0)
        assert abs(gm - em) < 1e-9, (gm, em)
        assert abs(gn - en) < 1e-9, (gn, en)


def test_lambda_matches_engine(rng):
    x = np.linspace(0, 2 * np.pi, 64, endpoint=False)
    cases = [
        (128 + 100 * np.outer(np.sin(8 * x), np.sin(8 * x))).astype(np.uint8),
        (128 + 100 * np.outer(np.sin(2 * x), np.sin(2 * x))).astype(np.uint8),
        rng.integers(0, 256, size=(64, 64), dtype=np.uint8),
        np.full((64, 64), 128, dtype=np.uint8),  # flat -> default
    ]
    for img in cases:
        e = engine_lambda_tiles(img)
        g = stats.height_lambda_tiles(img.astype(np.float64) / 255.0)
        assert abs(g - e) < 1e-9, (g, e)


def test_parity_on_a_real_master():
    """The end-to-end case: a shipped master, both implementations."""
    from PIL import Image
    p = REPO / "raw" / "jak1" / "village1-vis-tfrag" / "vil-beach-01" / "height.png"
    if not p.exists():
        pytest.skip("LFS master not checked out")
    img = np.asarray(Image.open(p).convert("L"), dtype=np.uint8)
    small = img[::16, ::16]  # the literal transcription is O(n) python loops
    em, en = engine_height_stats(small)
    gm, gn = stats.height_stats(small.astype(np.float64) / 255.0)
    assert abs(gm - em) < 1e-9 and abs(gn - en) < 1e-9
    assert abs(stats.height_lambda_tiles(small.astype(np.float64) / 255.0)
               - engine_lambda_tiles(small)) < 1e-9
