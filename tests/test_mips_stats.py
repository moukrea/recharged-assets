import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import mips  # noqa: E402
import stats  # noqa: E402


def test_level_count():
    assert mips.level_count(2048, 2048) == 12
    assert mips.level_count(2048, 1024) == 12
    assert mips.level_count(1, 1) == 1


def test_albedo_flat_is_stable():
    a = np.full((8, 8, 3), 0.5)
    chain = mips.albedo_chain(a, 4)
    assert len(chain) == 4
    for lvl in chain:
        assert np.allclose(lvl, 0.5, atol=1e-9)


def test_albedo_filters_in_linear_light():
    # A 50% black/white checker must average to linear-mid, not encoded-mid:
    # ((0 + 1^2.2)/2)^(1/2.2) ~= 0.73, NOT 0.5.
    a = np.zeros((2, 2, 3))
    a[0, 0] = a[1, 1] = 1.0
    chain = mips.albedo_chain(a, 2)
    expected = (0.5) ** (1 / mips.ENGINE_GAMMA)
    assert np.allclose(chain[1], expected, atol=1e-6)


def test_normal_chain_renormalizes():
    rng = np.random.default_rng(42)
    v = rng.normal(size=(16, 16, 3))
    v[..., 2] = np.abs(v[..., 2]) + 0.2
    v /= np.linalg.norm(v, axis=-1, keepdims=True)
    chain = mips.normal_chain((v + 1) * 0.5, 5)
    for lvl in chain[1:]:
        n = np.linalg.norm(lvl * 2 - 1, axis=-1)
        assert np.allclose(n, 1.0, atol=1e-6)


def test_data_chain_preserves_mean():
    rng = np.random.default_rng(1)
    g = rng.random((64, 64))
    chain = mips.data_chain(g, 7)
    assert abs(float(np.mean(chain[-1])) - float(np.mean(g))) < 1e-9
    assert chain[-1].shape == (1, 1)


# One 8-bit step is 2/255 = 0.0078 in normal space: a "flat" normal encodes as
# byte 128, i.e. +0.0039, never exactly 0. The engine measures the same
# quantized bytes and carries the identical residual, so the tolerance here is
# one quantization step — matching behaviour, not hiding it.
QUANT_STEP = 2.0 / 255.0


def test_normal_dc_flat_map_is_near_zero():
    flat = np.zeros((8, 8, 3))
    flat[..., 2] = 1.0  # straight-up normals
    dx, dy = stats.normal_dc((flat + 1) * 0.5)
    assert abs(dx) <= QUANT_STEP and abs(dy) <= QUANT_STEP


def test_normal_dc_detects_tilt():
    v = np.zeros((8, 8, 3))
    v[..., 0] = 0.3
    v[..., 2] = np.sqrt(1 - 0.09)
    dx, dy = stats.normal_dc((v + 1) * 0.5)
    assert dx > 0.25 and abs(dy) <= QUANT_STEP


def test_height_stats():
    # narrow-range map -> strong renormalization, clamped at 16
    g = np.full((32, 32), 0.4)
    g[0, 0] = 0.42
    mean, norm = stats.height_stats(g)
    assert 0.39 < mean < 0.41
    assert norm == 16.0
    # full-range map -> ~1.0
    ramp = np.tile(np.linspace(0, 1, 256), (16, 1))
    _, norm2 = stats.height_stats(ramp)
    assert 0.5 <= norm2 <= 1.2


def test_lambda_flat_default():
    assert stats.height_lambda_tiles(np.full((64, 64), 0.5)) == 0.25


def test_lambda_scales_with_feature_size():
    x = np.linspace(0, 2 * np.pi, 256, endpoint=False)
    fine = 0.5 + 0.5 * np.outer(np.sin(16 * x), np.sin(16 * x))
    coarse = 0.5 + 0.5 * np.outer(np.sin(2 * x), np.sin(2 * x))
    lf = stats.height_lambda_tiles(fine)
    lc = stats.height_lambda_tiles(coarse)
    assert lf < lc
