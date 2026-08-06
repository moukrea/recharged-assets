import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import presets  # noqa: E402


def test_bonkers_is_master():
    assert presets.preset_dims("bonkers", (2048, 2048), (512, 512)) == (2048, 2048)
    assert presets.preset_dims("bonkers", (2048, 1024), None) == (2048, 1024)


def test_low_caps_at_esrgan():
    assert presets.preset_dims("low", (2048, 2048), (512, 512)) == (512, 512)
    # non-square ESRGAN reference (the pack is NOT a uniform x2)
    assert presets.preset_dims("low", (2048, 1024), (512, 256)) == (512, 256)
    # no ESRGAN reference -> master
    assert presets.preset_dims("low", (2048, 2048), None) == (2048, 2048)


def test_default_is_min_master_esrgan_x2():
    assert presets.preset_dims("default", (2048, 2048), (1024, 1024)) == (2048, 2048)
    assert presets.preset_dims("default", (2048, 2048), (512, 512)) == (1024, 1024)
    assert presets.preset_dims("default", (2048, 2048), (256, 256)) == (512, 512)


def test_never_upscales():
    # ESRGAN larger than master must not enlarge the master
    assert presets.preset_dims("low", (1024, 1024), (2048, 2048)) == (1024, 1024)
    assert presets.preset_dims("default", (1024, 1024), (2048, 2048)) == (1024, 1024)


def test_aspect_preserved():
    assert presets.preset_dims("default", (2048, 1024), (512, 256)) == (1024, 512)


def test_unknown_preset():
    with pytest.raises(ValueError):
        presets.preset_dims("ultra", (2048, 2048), (512, 512))
