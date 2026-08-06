# RPACK v1 — binary pack format

A minimal indexed container for KTX2 texture payloads. Design goals
(spec §11): random access without extraction, no re-compression of
GPU-compressed data, memory-mapping-friendly alignment, per-entry hashes,
deterministic byte output.

```
offset 0
+--------------------------------------------------------------+
| HEADER (16 bytes, little-endian)                              |
|   0  u8[4]  magic            "RPK1"                           |
|   4  u32    schema_version   1                                |
|   8  u32    entry_count                                       |
|  12  u32    flags            0 (reserved)                     |
+--------------------------------------------------------------+
| PAYLOADS                                                      |
|   entry_count KTX2 files, each starting at a 16-byte-aligned  |
|   offset (zero padding between payloads). Stored verbatim —   |
|   the GPU-ready bytes, never re-compressed.                   |
+--------------------------------------------------------------+
| INDEX (UTF-8 JSON, canonical form)                            |
|   json.dumps(index, sort_keys=True, separators=(",",":"))     |
|   validates against rpack-index.schema.json                   |
+--------------------------------------------------------------+
| TRAILER (24 bytes, little-endian)                             |
|   0  u64    index_offset                                      |
|   8  u64    index_size                                        |
|  16  u8[4]  index_sha256_prefix  (first 4 bytes of the        |
|             index's SHA-256, integrity fast-check)            |
|  20  u8[4]  magic            "RIDX"                           |
+--------------------------------------------------------------+
```

Reader algorithm: seek to `EOF-24`, verify `RIDX`, read
`index_offset/index_size`, read + verify + parse the JSON index, then seek
directly to any entry's `offset`/`size`. Each entry's `sha256` covers its
exact payload bytes; the shard's own published SHA-256 (manifest) covers the
whole file.

Index entries carry everything the engine loader needs without opening the
KTX2 (see `rpack-index.schema.json`): stable id, replacement key
(`tpage/name`), map kind, format, dimensions, mip count, colorspace,
alpha/wrap modes, and the **precomputed decode-time statistics**
(`normal_dc_x/y`, `height_mean`, `height_norm`, `height_lambda_tiles`) that
the Recharged PBR shaders require and that cannot be measured from
compressed blocks at load time.

Determinism rules: entries sorted by (key, map); payload order = index
order; canonical JSON; no timestamps anywhere (KTX2 payloads are produced
without writer/date metadata). Rebuilding an unchanged shard yields
byte-identical output — which is what makes content-addressed shard reuse
across releases work.

Reference implementation: `tools/rpack.py` (writer + reader + verifier).
Golden-file tests: `tests/test_rpack.py`.
