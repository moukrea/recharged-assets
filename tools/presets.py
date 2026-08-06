"""Resolution preset rules (schema v1) — the explicit, modifiable policy the
spec requires instead of logic scattered across scripts.

very-low is engine-side (no pack installed; stock fr3 textures) and never a
built artifact. Downscaling always starts from the master; nothing is ever
upscaled to satisfy a preset name.
"""

from __future__ import annotations

PRESETS = ("low", "default", "bonkers")


def _cap(dims: tuple[int, int], cap: tuple[int, int]) -> tuple[int, int]:
    """Scale dims down (never up) by the largest power-of-two factor needed
    to fit within cap, preserving aspect ratio exactly."""
    w, h = dims
    cw, ch = cap
    factor = 1
    while w // factor > cw or h // factor > ch:
        factor *= 2
    return (max(1, w // factor), max(1, h // factor))


def preset_dims(preset: str,
                master_dims: tuple[int, int],
                esrgan_dims: tuple[int, int] | None) -> tuple[int, int]:
    """Level-0 dimensions of a map for a preset.

    - low:     capped at the per-texture ESRGAN dims (verified non-uniform —
               never assume x2); master dims when no ESRGAN reference exists.
    - default: min(master, esrgan x2) — the spec's rationalization table
               (ESRGAN 1024->2048, 512->1024, 256->512) expressed as a rule.
    - bonkers: full master, never enlarged.
    """
    if preset == "bonkers":
        return master_dims
    if esrgan_dims is None:
        return master_dims
    if preset == "low":
        return _cap(master_dims, esrgan_dims)
    if preset == "default":
        return _cap(master_dims, (esrgan_dims[0] * 2, esrgan_dims[1] * 2))
    raise ValueError(f"unknown preset: {preset}")
