# Vertical prototype — results (2026-08-06)

`tools/build_prototype.py` runs plan step 3 end-to-end on 7 representative
masters (the 4 village1 materials shared with the fork's bundled set +
`bch-outpostwall`, `snow-icewall-01`, `cit-temp-precursor-plain`, spanning
3 shard clusters and the 2048×1024 case):

semantic mip chains → per-profile GPU encode (pinned toolchain) → KTX2
(`ktx create --raw`, every file passes `ktx validate`) → RPACK shards
(deterministic assignment + content-hash names) → full pack re-read with
per-entry SHA-256 verification. **84 KTX2 files, 18 shards, zero failures.**

## Size results (bonkers preset, full mip chains)

| Profile | Albedo shards | Material shards | Total 7 materials | vs RGBA8+mips (~555 MiB) |
|---|---|---|---|---|
| pc-bc (BC7/BC5/BC4) | 36.4 MB | 72.7 MB | **109 MB** | ~5.1× smaller |
| android-etc2 (ETC2/EAC) | 18.2 MB | 72.7 MB | **91 MB** | ~6.1× smaller |
| android-astc (ASTC 6×6/4×4 + EAC R11) | 16.2 MB | 72.7 MB | **89 MB** | ~6.2× smaller |

RPACK stores the exact GPU payloads, so shard size ≈ VRAM cost: the 7
bundled village1-class materials drop from ~555 MiB VRAM (engine audit
estimate, uncompressed RGBA8 + driver mips) to ~90-110 MiB.

Material maps dominate (normal 16 B/block + rough/height 8 B/block on every
profile) — which validates the albedo/material shard split: an albedo-only
install is 16-36 MB for these 7 materials.

## Engine-stat mirrors (plan D6)

Computed offline per material and embedded in the RPACK index
(`normal_dc_x/y`, `height_mean`, `height_norm`, `height_lambda_tiles`) —
e.g. `vil1-sages-stonewall-01`: dc=(0.065, −0.236), height_mean=0.490,
norm=2.04, λ=0.092 tiles. Magnitudes line up with the engine's own logged
measurements of the bundled set (leafyground dc=(+0.076, −0.227)); the M1
engine spike must assert exact-match on shared materials.

## What this proves / what's next

Proven: masters → semantic mips (linear-light albedo, renormalized normals)
→ X/Y normals → pinned encoders → valid KTX2 → deterministic sharded RPACK
→ verified re-read. The full-catalog pipeline (step 5) is this code plus
change detection, preset variants, validation thresholds and manifest
generation.

Not yet proven (needs the M1 engine spike on a throwaway branch off
`autoport/android-port`): libktx upload path in the real renderer, X/Y
normal reconstruction shader bit, side-by-side vs the bundled PNGs on
screen, and stat-mirror equality with the engine's CPU measurements.
