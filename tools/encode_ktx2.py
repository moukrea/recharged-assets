"""Encode one map (with its full mip chain) into a KTX2 file.

Chain: semantic mip levels (numpy) -> per-level PNG -> external encoder
(bc7enc / etcpak / astcenc) -> strip the tool's container (DDS/PVR/.astc)
-> `ktx create --raw --levels N` -> `ktx validate`.

Determinism: encoders are deterministic for fixed inputs/versions; ktx create
writes no timestamps; KTX2 output is byte-stable for identical inputs.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

import mips as mipgen


class EncodeError(Exception):
    pass


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise EncodeError(f"{' '.join(map(str, cmd))}\n{r.stdout[-400:]}\n{r.stderr[-400:]}")


# ---------------------------------------------------------------- containers

def strip_dds(path: Path) -> bytes:
    data = path.read_bytes()
    if data[:4] != b"DDS " or len(data) < 128:
        raise EncodeError(f"not a DDS: {path}")
    fourcc = data[84:88]
    offset = 148 if fourcc == b"DX10" else 128
    return data[offset:]


def strip_pvr(path: Path) -> bytes:
    data = path.read_bytes()
    if data[:4] != b"PVR\x03" or len(data) < 52:
        raise EncodeError(f"not a PVRv3: {path}")
    meta_size = struct.unpack("<I", data[48:52])[0]
    return data[52 + meta_size:]


def strip_astc(path: Path) -> bytes:
    data = path.read_bytes()
    if data[:4] != b"\x13\xab\xa1\x5c":
        raise EncodeError(f"not an .astc: {path}")
    return data[16:]


# ---------------------------------------------------------------- formats

@dataclass(frozen=True)
class Fmt:
    vk: str                 # VkFormat name (without prefix) for ktx create
    block_bytes: int
    block_w: int
    block_h: int
    encoder: str            # bc7enc | etcpak | astcenc
    encoder_arg: str        # tool-specific format switch
    pad4: bool              # tool requires dims padded to block multiples


FORMATS = {
    "bc7":      Fmt("BC7_UNORM_BLOCK", 16, 4, 4, "bc7enc", "", True),
    "bc5":      Fmt("BC5_UNORM_BLOCK", 16, 4, 4, "bc7enc", "-5", True),
    "bc4":      Fmt("BC4_UNORM_BLOCK", 8, 4, 4, "bc7enc", "-4", True),
    "bc1":      Fmt("BC1_RGB_UNORM_BLOCK", 8, 4, 4, "bc7enc", "-1", True),
    "etc2_rgb": Fmt("ETC2_R8G8B8_UNORM_BLOCK", 8, 4, 4, "etcpak", "etc2_rgb", True),
    "eac_r11":  Fmt("EAC_R11_UNORM_BLOCK", 8, 4, 4, "etcpak", "etc2_r", True),
    "eac_rg11": Fmt("EAC_R11G11_UNORM_BLOCK", 16, 4, 4, "etcpak", "etc2_rg", True),
    "astc_4x4": Fmt("ASTC_4x4_UNORM_BLOCK", 16, 4, 4, "astcenc", "4x4", False),
    "astc_6x6": Fmt("ASTC_6x6_UNORM_BLOCK", 16, 6, 6, "astcenc", "6x6", False),
}


def expected_size(fmt: Fmt, w: int, h: int) -> int:
    bw = (w + fmt.block_w - 1) // fmt.block_w
    bh = (h + fmt.block_h - 1) // fmt.block_h
    return bw * bh * fmt.block_bytes


def _encode_level_png(png: Path, fmt: Fmt, work: Path, astc_quality: str,
                      decode_to: Path | None = None) -> bytes:
    """Encode one level; when decode_to is set, also produce the decoded
    round-trip image there (used for the level-0 quality gates)."""
    if fmt.encoder == "bc7enc":
        dds = work / (png.stem + ".dds")
        args = ["bc7enc", "-q"]
        if decode_to is None:
            args.append("-g")
        if fmt.encoder_arg:
            args.append(fmt.encoder_arg)
        tail = [png, dds] + ([decode_to] if decode_to is not None else [])
        _run(args + tail, cwd=work)
        return strip_dds(dds)
    if fmt.encoder == "etcpak":
        pvr = work / (png.stem + ".pvr")
        _run(["etcpak", "-c", fmt.encoder_arg, "--linear", png, pvr])
        if decode_to is not None:
            _run(["etcpak", "-v", pvr, decode_to])
        return strip_pvr(pvr)
    if fmt.encoder == "astcenc":
        astc = work / (png.stem + ".astc")
        _run(["astcenc", "-cl", png, astc, fmt.encoder_arg, astc_quality, "-j", "8", "-silent"])
        if decode_to is not None:
            _run(["astcenc", "-dl", astc, decode_to, "-silent"])
        return strip_astc(astc)
    raise EncodeError(f"unknown encoder {fmt.encoder}")


def _level_png(level01: np.ndarray, channels: str, fmt: Fmt, path: Path) -> tuple[int, int]:
    """Write one mip level as the RGB PNG the encoder expects; returns true (w, h)."""
    q = mipgen.quantize(level01)
    if q.ndim == 2:
        q = q[..., None]
    h, w = q.shape[:2]
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    if channels == "r":
        rgb[..., 0] = q[..., 0]
        rgb[..., 1] = q[..., 0]  # replicate for encoders sampling luma
        rgb[..., 2] = q[..., 0]
    elif channels == "rg":
        rgb[..., 0] = q[..., 0]
        rgb[..., 1] = q[..., 1]
    else:  # rgb
        rgb[..., :3] = q[..., :3]
    if fmt.pad4:
        ph = (h + fmt.block_h - 1) // fmt.block_h * fmt.block_h
        pw = (w + fmt.block_w - 1) // fmt.block_w * fmt.block_w
        if (ph, pw) != (h, w):
            padded = np.zeros((ph, pw, 3), dtype=np.uint8)
            padded[:h, :w] = rgb
            padded[h:, :w] = rgb[h - 1:h, :]          # edge-clamp padding
            padded[:, w:] = padded[:, w - 1:w]
            rgb = padded
    Image.fromarray(rgb, "RGB").save(path)
    return w, h


def encode_map(levels01: list[np.ndarray], channels: str, fmt_name: str,
               out_ktx2: Path, astc_quality: str = "-thorough",
               validate: bool = True,
               capture_level0: bool = False) -> np.ndarray | None:
    """levels01: semantic mip chain (level 0 first), floats in [0,1].
    With capture_level0, returns the decoded round-trip of level 0 as an
    RGBA uint8 array (cropped to the true dims) for quality gating."""
    fmt = FORMATS[fmt_name]
    w0 = levels01[0].shape[1]
    h0 = levels01[0].shape[0]
    decoded0 = None
    with tempfile.TemporaryDirectory(prefix="rchg-enc-") as td:
        work = Path(td)
        raw_files = []
        for i, lvl in enumerate(levels01):
            png = work / f"m{i}.png"
            w, h = _level_png(lvl, channels, fmt, png)
            dec_path = work / f"m{i}.dec.png" if (capture_level0 and i == 0) else None
            blob = _encode_level_png(png, fmt, work, astc_quality, decode_to=dec_path)
            if dec_path is not None:
                arr = np.asarray(Image.open(dec_path).convert("RGBA"), dtype=np.uint8)
                decoded0 = arr[:h, :w]  # crop any block padding
            want = expected_size(fmt, w, h)
            if len(blob) != want:
                raise EncodeError(
                    f"{fmt_name} level {i} ({w}x{h}): got {len(blob)} bytes, want {want}")
            raw = work / f"m{i}.raw"
            raw.write_bytes(blob)
            raw_files.append(raw)
        out_ktx2.parent.mkdir(parents=True, exist_ok=True)
        _run(["ktx", "create", "--raw", "--format", fmt.vk,
              "--width", str(w0), "--height", str(h0),
              "--levels", str(len(levels01)),
              "--assign-tf", "linear",
              *raw_files, out_ktx2])
        if validate:
            _run(["ktx", "validate", out_ktx2])
    return decoded0


def have_tools() -> list[str]:
    missing = [t for t in ("bc7enc", "etcpak", "astcenc", "ktx") if not shutil.which(t)]
    return missing
