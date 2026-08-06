"""RPACK v1 reference implementation (writer, reader, verifier).

Format spec: schemas/rpack-v1.md. Deterministic by construction: entries are
sorted by (key, map), the index is canonical JSON, payloads are 16-byte
aligned, and no timestamps exist anywhere.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

MAGIC_HEADER = b"RPK1"
MAGIC_TRAILER = b"RIDX"
SCHEMA_VERSION = 1
HEADER_SIZE = 16
TRAILER_SIZE = 24
ALIGNMENT = 16

MAP_KINDS = ("albedo", "normal", "roughness", "height", "metallic", "ao",
             "specular", "emissive", "mask")


class RpackError(Exception):
    pass


@dataclass
class Entry:
    """One KTX2 payload plus the loader-facing metadata for it."""
    id: str
    key: str            # engine replacement key: <tpage>/<name>
    map: str            # one of MAP_KINDS
    format: str         # VkFormat name matching the KTX2 vkFormat
    width: int
    height: int
    mip_levels: int
    colorspace: str     # srgb-encoded | linear
    payload: bytes = b""
    alpha_mode: str = "none"
    wrap_mode: str = "repeat"
    channels: str | None = None
    stats: dict | None = None
    # filled by the writer/reader:
    offset: int = 0
    size: int = 0
    sha256: str = ""

    def index_record(self) -> dict:
        rec = {
            "id": self.id,
            "key": self.key,
            "map": self.map,
            "format": self.format,
            "width": self.width,
            "height": self.height,
            "mip_levels": self.mip_levels,
            "offset": self.offset,
            "size": self.size,
            "sha256": self.sha256,
            "colorspace": self.colorspace,
            "alpha_mode": self.alpha_mode,
            "wrap_mode": self.wrap_mode,
        }
        if self.channels:
            rec["channels"] = self.channels
        if self.stats:
            rec["stats"] = self.stats
        return rec


@dataclass
class PackMeta:
    game: str
    profile: str
    preset: str
    group: str
    cluster: str


def _canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _align(n: int) -> int:
    return (n + ALIGNMENT - 1) // ALIGNMENT * ALIGNMENT


def write(path: Path | str, meta: PackMeta, entries: list[Entry]) -> str:
    """Write an RPACK file; returns its SHA-256 (the shard's published hash)."""
    if not entries:
        raise RpackError("an RPACK must contain at least one entry")
    for e in entries:
        if e.map not in MAP_KINDS:
            raise RpackError(f"unknown map kind: {e.map}")
        if not e.payload:
            raise RpackError(f"empty payload for {e.key} ({e.map})")

    ordered = sorted(entries, key=lambda e: (e.key, e.map))

    offset = HEADER_SIZE
    blobs: list[tuple[int, bytes]] = []
    for e in ordered:
        offset = _align(offset)
        e.offset = offset
        e.size = len(e.payload)
        e.sha256 = hashlib.sha256(e.payload).hexdigest()
        blobs.append((offset, e.payload))
        offset += e.size

    index = {
        "schema_version": SCHEMA_VERSION,
        "game": meta.game,
        "profile": meta.profile,
        "preset": meta.preset,
        "group": meta.group,
        "cluster": meta.cluster,
        "entries": [e.index_record() for e in ordered],
    }
    index_bytes = _canonical_json(index)
    index_offset = offset
    index_sha = hashlib.sha256(index_bytes).digest()

    out = bytearray()
    out += MAGIC_HEADER
    out += struct.pack("<III", SCHEMA_VERSION, len(ordered), 0)
    for blob_offset, payload in blobs:
        out += b"\x00" * (blob_offset - len(out))
        out += payload
    out += index_bytes
    out += struct.pack("<QQ", index_offset, len(index_bytes))
    out += index_sha[:4]
    out += MAGIC_TRAILER

    data = bytes(out)
    Path(path).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


@dataclass
class Reader:
    """Random-access RPACK reader (spec-compliant: trailer -> index -> seeks)."""
    path: Path
    meta: PackMeta = field(init=False)
    entries: list[Entry] = field(init=False)
    _by_key: dict = field(init=False)

    def __post_init__(self):
        self.path = Path(self.path)
        size = self.path.stat().st_size
        if size < HEADER_SIZE + TRAILER_SIZE:
            raise RpackError("file too small to be an RPACK")
        with open(self.path, "rb") as f:
            header = f.read(HEADER_SIZE)
            if header[:4] != MAGIC_HEADER:
                raise RpackError("bad header magic")
            schema_version, entry_count, _flags = struct.unpack("<III", header[4:16])
            if schema_version != SCHEMA_VERSION:
                raise RpackError(f"unsupported schema_version {schema_version}")

            f.seek(size - TRAILER_SIZE)
            trailer = f.read(TRAILER_SIZE)
            if trailer[20:24] != MAGIC_TRAILER:
                raise RpackError("bad trailer magic")
            index_offset, index_size = struct.unpack("<QQ", trailer[:16])
            if index_offset + index_size > size - TRAILER_SIZE:
                raise RpackError("index range out of bounds")

            f.seek(index_offset)
            index_bytes = f.read(index_size)
            if hashlib.sha256(index_bytes).digest()[:4] != trailer[16:20]:
                raise RpackError("index integrity check failed")
            index = json.loads(index_bytes)

        if index["schema_version"] != SCHEMA_VERSION:
            raise RpackError("index schema_version mismatch")
        if len(index["entries"]) != entry_count:
            raise RpackError("header/index entry count mismatch")

        self.meta = PackMeta(index["game"], index["profile"], index["preset"],
                             index["group"], index["cluster"])
        self.entries = []
        for rec in index["entries"]:
            e = Entry(id=rec["id"], key=rec["key"], map=rec["map"],
                      format=rec["format"], width=rec["width"], height=rec["height"],
                      mip_levels=rec["mip_levels"], colorspace=rec["colorspace"],
                      alpha_mode=rec.get("alpha_mode", "none"),
                      wrap_mode=rec.get("wrap_mode", "repeat"),
                      channels=rec.get("channels"), stats=rec.get("stats"))
            e.offset, e.size, e.sha256 = rec["offset"], rec["size"], rec["sha256"]
            self.entries.append(e)
        self._by_key = {(e.key, e.map): e for e in self.entries}

    def find(self, key: str, map_kind: str) -> Entry | None:
        return self._by_key.get((key, map_kind))

    def read_payload(self, entry: Entry, verify: bool = True) -> bytes:
        with open(self.path, "rb") as f:
            f.seek(entry.offset)
            data = f.read(entry.size)
        if len(data) != entry.size:
            raise RpackError(f"short read for {entry.key} ({entry.map})")
        if verify and hashlib.sha256(data).hexdigest() != entry.sha256:
            raise RpackError(f"payload hash mismatch for {entry.key} ({entry.map})")
        return data

    def verify_all(self) -> None:
        """Full-pack verification: every payload hash + alignment."""
        for e in self.entries:
            if e.offset % ALIGNMENT != 0:
                raise RpackError(f"misaligned payload for {e.key} ({e.map})")
            self.read_payload(e, verify=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Inspect / verify an RPACK file")
    ap.add_argument("file")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    r = Reader(Path(args.file))
    m = r.meta
    print(f"{args.file}: {m.game}/{m.profile}/{m.preset}/{m.group}/{m.cluster}, "
          f"{len(r.entries)} entries")
    for e in r.entries:
        print(f"  {e.key} [{e.map}] {e.format} {e.width}x{e.height} "
              f"mips={e.mip_levels} size={e.size}")
    if args.verify:
        r.verify_all()
        print("verify: OK")
