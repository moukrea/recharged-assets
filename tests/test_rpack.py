import hashlib
import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import rpack  # noqa: E402


META = rpack.PackMeta(game="jak1", profile="pc-bc", preset="default",
                      group="albedo", cluster="c1-village1-beach")


def make_entries():
    return [
        rpack.Entry(id="jak1/village1-vis-tfrag/vil-beach-01",
                    key="village1-vis-tfrag/vil-beach-01", map="albedo",
                    format="VK_FORMAT_BC7_UNORM_BLOCK", width=2048, height=2048,
                    mip_levels=12, colorspace="srgb-encoded",
                    payload=b"\xabKTX2-payload-A" * 100),
        rpack.Entry(id="jak1/beach-vis-tfrag/bch-rock",
                    key="beach-vis-tfrag/bch-rock", map="albedo",
                    format="VK_FORMAT_BC7_UNORM_BLOCK", width=1024, height=1024,
                    mip_levels=11, colorspace="srgb-encoded",
                    payload=b"payload-B", wrap_mode="repeat_x",
                    stats={"height_mean": 0.32, "height_norm": 2.5}),
    ]


def test_roundtrip(tmp_path):
    p = tmp_path / "test.rpack"
    sha = rpack.write(p, META, make_entries())
    assert sha == hashlib.sha256(p.read_bytes()).hexdigest()

    r = rpack.Reader(p)
    assert r.meta == META
    assert len(r.entries) == 2
    # sorted by (key, map): beach before village1
    assert r.entries[0].key == "beach-vis-tfrag/bch-rock"
    e = r.find("village1-vis-tfrag/vil-beach-01", "albedo")
    assert e is not None
    assert r.read_payload(e) == b"\xabKTX2-payload-A" * 100
    assert e.mip_levels == 12
    b = r.find("beach-vis-tfrag/bch-rock", "albedo")
    assert b.stats == {"height_mean": 0.32, "height_norm": 2.5}
    assert b.wrap_mode == "repeat_x"
    r.verify_all()


def test_determinism(tmp_path):
    p1, p2 = tmp_path / "a.rpack", tmp_path / "b.rpack"
    # different input order must yield byte-identical packs
    rpack.write(p1, META, make_entries())
    rpack.write(p2, META, list(reversed(make_entries())))
    assert p1.read_bytes() == p2.read_bytes()


def test_alignment(tmp_path):
    p = tmp_path / "test.rpack"
    rpack.write(p, META, make_entries())
    r = rpack.Reader(p)
    for e in r.entries:
        assert e.offset % rpack.ALIGNMENT == 0


def test_corrupted_payload_detected(tmp_path):
    p = tmp_path / "test.rpack"
    rpack.write(p, META, make_entries())
    r = rpack.Reader(p)
    data = bytearray(p.read_bytes())
    data[r.entries[0].offset] ^= 0xFF
    p.write_bytes(bytes(data))
    r2 = rpack.Reader(p)
    with pytest.raises(rpack.RpackError, match="hash mismatch"):
        r2.read_payload(r2.entries[0])


def test_corrupted_index_detected(tmp_path):
    p = tmp_path / "test.rpack"
    rpack.write(p, META, make_entries())
    data = bytearray(p.read_bytes())
    idx_off = struct.unpack("<Q", data[-24:-16])[0]
    data[idx_off + 2] ^= 0xFF
    p.write_bytes(bytes(data))
    with pytest.raises(rpack.RpackError, match="integrity"):
        rpack.Reader(p)


def test_bad_magic(tmp_path):
    p = tmp_path / "test.rpack"
    p.write_bytes(b"NOPE" + b"\x00" * 64)
    with pytest.raises(rpack.RpackError, match="magic"):
        rpack.Reader(p)


def test_empty_pack_rejected(tmp_path):
    with pytest.raises(rpack.RpackError, match="at least one"):
        rpack.write(tmp_path / "e.rpack", META, [])


def test_index_validates_against_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    p = tmp_path / "test.rpack"
    rpack.write(p, META, make_entries())
    data = p.read_bytes()
    idx_off, idx_size = struct.unpack("<QQ", data[-24:-8])
    index = json.loads(data[idx_off:idx_off + idx_size])
    schema = json.loads((Path(__file__).resolve().parent.parent /
                         "schemas" / "rpack-index.schema.json").read_text())
    jsonschema.validate(index, schema)
