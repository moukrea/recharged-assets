# Recharged Assets Pipeline — Implementation Plan

Status: **proposed** (v1, 2026-08-06). Based on the project spec and the
engine audit in [AUDIT.md](AUDIT.md). Nothing beyond the repo bootstrap
(masters import + metadata) is implemented yet.

---

## 0. Ground truth this plan builds on

**Assets currently in this repo** (imported, LFS, pushed):

- 294 in-game placements → deduplicated to **172 physical material sets**
  (158 canonical + 14 `recale` pixel-shifted variants), each
  albedo (RGB, no alpha) + normal (RGB) + roughness (L) + height (L),
  2048×2048 (2 sets 2048×1024). `metadata/jak1/placements.json` +
  `materials.json` carry the mapping and content hashes.
- The full ESRGAN pack (3 457 PNGs, 64²…1024×512, albedo only) is NOT yet
  imported; it is the source for per-texture `esrgan_dims` metadata and a
  future albedo-only catalog expansion.

**Engine reality** (see AUDIT.md): replacements are baked into fr3 offline;
no runtime texture loading, no compressed formats, no sRGB, mipmaps
driver-generated at upload, no PBR consumer, no Android HTTP stack, fr3 path
constructor-injected, APK assets extracted by a sentineled LoaderActivity.

## 1. Architecture decisions (locked unless the user overrides)

**D1 — Runtime override path, not fr3 rebaking.**
The engine gains a new "asset pack" loading path: at fr3 texture-upload time
(`LoaderStages.cpp add_texture` / common-texture load), if an installed pack
provides an entry for `(tpage_name, texture_name)`, upload the pack's KTX2
payload instead of the fr3 RGBA8 payload. Stock fr3 textures remain the
universal fallback (offline mode, Very Low preset, partial packs). This is
what makes "download pack → restart → done" possible on both PC and Android
without shipping or regenerating fr3, and it removes remastered PNGs from
normal builds (spec §1). The fr3-side name/tpage info must be carried to
runtime: fr3 stores `combo_id` + debug name today — the loader override table
is keyed by the same `<tpage>/<name>` strings recorded in our
`placements.json`, so the pack index maps combo_id-compatible keys; the
engine milestone M1 (§9) includes emitting a `tpage/name → combo_id` table at
extraction OR matching on the debug-name field already present in fr3
(`Tfrag3Data.h` `debug_name`/`debug_tpage_name`) — audit confirms names
survive into fr3.

**D2 — KTX2 as the only texture container** (spec §3). BCn on PC, ETC2/EAC
Android baseline, ASTC Android on capable GPUs. No Basis Universal in v1.
`libktx` vendored into the fork for reading + `glTexStorage2D`/
`glCompressedTexSubImage2D` upload.

**D3 — UNORM binding despite sRGB-aware offline processing.**
The engine is sRGB-oblivious (audit §4/5): shaders and blending operate on
raw encoded values. Therefore: mip filtering and resampling are done
sRGB-aware offline (correct averaging), but the KTX2 files are flagged and
bound as **UNORM**, so sampled values remain byte-equivalent to today's
appearance. Switching to true sRGB internal formats is deliberately out of
scope until the engine grows a gamma-correct pipeline (would change the whole
game's look, not just packs).

**D4 — Material maps ship, the PBR pass comes later.**
Normal/roughness/height are encoded and sharded from day one (as their own
functional-group shards, spec §12), but no engine milestone in this plan
implements the lighting pass that consumes them (net-new renderer work, dual
GLSL 4.10 / GLES 3.20 constraint). Albedo-only value is delivered first;
material shards download only when a future engine version requests them
(manifest `engine_features: ["pbr"]` gating, §13 of spec).

**D5 — Android downloads in the Java layer.**
Native has no TLS stack (curl excluded, INTERNET permission removed). The
asset manager core (manifest parsing, profile/preset resolution, hash
verification, atomic install, GC) is shared C++; the **transport** is
platform-specific: libcurl on PC (already vendored+SSL), and on Android a
Kotlin downloader in the existing LoaderActivity/first-launch flow (resumable
via HTTP Range, storage checks, Wi-Fi-only option) writing into
`getFilesDir()/assets/`, then handing to the shared C++ installer. This keeps
the fragile native build unchanged and reuses the proven sentinel pattern.
(Alternative — mbedTLS+curl on Android — recorded as fallback if
downloads-from-native ever become necessary.)

**D6 — Normal maps re-encoded to 2 channels.**
Masters are RGB XYZ normals. The pipeline converts to X/Y (BC5 / EAC RG11 /
ASTC-suited layout), shaders reconstruct Z. Roughness/height stay
single-channel (BC4 / EAC R11).

**D7 — Alpha policy.**
All current albedos are opaque RGB (verified). Metadata still carries
`alpha_mode` per material (absent/binary/progressive) because the engine's
discard-at-AREF behaviour (audit §6) makes binary-alpha preservation mandatory
for any future cutout texture; validation cross-checks that a replaced
texture whose *original* had meaningful alpha is not shipped opaque.

**D8 — GPU profiles.**

| Profile id | Platform gate | Albedo | Normal | Rough/Height/Mask |
|---|---|---|---|---|
| `pc-bc` (main) | GL 4.2+ or BPTC ext | BC7 UNORM | BC5 UNORM (XY) | BC4 UNORM |
| `pc-bc-legacy` | S3TC+RGTC (macOS 4.1) | BC1 (opaque) / BC3 (alpha) | BC5 | BC4 |
| `android-etc2` | GLES 3.0+ (always) | ETC2 RGB8 (opaque) / RGBA8-EAC / punchthrough per alpha_mode | EAC RG11 | EAC R11 |
| `android-astc` | `KHR_texture_compression_astc_ldr` | ASTC 6×6 (5×5/4×4 for alpha/UI per metadata) | ASTC 4×4 XY **vs EAC RG11 — decided by measured error** | EAC R11 unless ASTC measurably better |
| (fallback) | none of the above | uncompressed RGBA8 mip chain from the pack — explicit, logged, never silent | — | — |

sRGB variants of these formats are intentionally not used (D3).

## 2. Contracts (schema_version = 1)

All four schemas live in `schemas/` as JSON Schema, versioned together by
`schema_version`. Breaking any parser/format bumps the major.

### 2.1 Material metadata (`metadata/jak1/materials/<id>.json`, plus catalog-level defaults)
Per spec §5. Key fields: `id` (stable, = `jak1/<tpage>/<name>`), `game`,
`original_texture` (tpage/name (+combo_id when extracted)), `original_dims`,
`esrgan_dims`, `master_dims`, per-map `semantic`, `colorspace`
(sRGB-encoded/linear-data), `alpha_mode`, `wrap_mode`
(clamp/repeat-x/repeat-y/repeat — seeds from the local pipeline's
`tiling.json` verdicts), `mip_policy` overrides, per-profile format
overrides, `levels` (derived from placements), `required` (functional
content), `variant_of` + `recale` (already captured in `materials.json`).
Automated detection proposes; explicit overrides win; ambiguous alpha/map
typing is never inferred from channel count alone.

### 2.2 Manifest (one per content release, e.g. `assets-v1.0.0`)
`schema_version`, `asset_version`, `games`, `engine_compat`
(min/max Recharged version, `min_loader_version`, `required_features`),
`profiles[]`, `presets[]`, `shards[]` (name, sha256, size, download URL →
immutable release asset, family tuple game/profile/preset/group/level-group),
`entries[]` (asset id → shard + offset or per-shard index delegation),
signing/attestation info. The manifest is the complete current state even
when shards live in older releases (spec §12).

### 2.3 RPACK v1
Purpose-built, zip rejected (no vendored zip lib; Deflate pointless on
GPU-compressed payloads). Layout: magic `RPK1`, header (schema ver, count,
index offset), concatenated raw KTX2 payloads (16-byte aligned for mmap
upload), footer index: per entry `id_hash` (xxh3 of stable id), `tpage/name`
key, offset, size, sha256, semantic, format. Random access without
extraction; readable via stdio now, mmap later (engine currently has no mmap
util — not required for v1).

### 2.4 `assets.lock.json` (lives in the jak-project fork)
Exactly per spec §14: `schema_version`, `asset_version`, `manifest_url`,
`manifest_sha256`, `required`. Builds never use `latest`; game CI downloads
only the manifest, verifies hash, runs loader integration tests; full pack
download only in explicit offline-distribution jobs.

## 3. Pipeline tools (this repo, `tools/`)

Python orchestration + pinned native encoders in a versioned toolchain image
(`ghcr.io/moukrea/recharged-assets-toolchain:<tag>`, Dockerfile in repo):

- **KTX-Software** (`ktx create`, `ktx validate`, libktx) — container
  authoring + validation.
- **astcenc** (Arm) — ASTC.
- **BCn/ETC2 encoder** — candidates: Compressonator CLI, `bc7enc_rdo`,
  `etcpak`, `toktx`-embedded paths. An **encoder bake-off stage**
  (`tools/benchmark_encoders.py`) runs once on a representative texture set,
  produces a scored report (quality metrics × wall time), and the winner is
  pinned in the toolchain image. No encoder is chosen by popularity (spec §9.3).

Stages (each deterministic, content-addressed cache keyed by
master-hash + tool-version + policy-hash):

1. `derive_metadata` — propose metadata from masters + local pipeline
   artifacts (`tiling.json` wrap modes, ESRGAN dims lookup, placements);
   writes proposals, humans commit overrides.
2. `make_variants` — resolution presets from masters (§5).
3. `make_mips` — semantic mip chains (§4).
4. `encode` — per profile per semantic → KTX2.
5. `validate` — §7 gates.
6. `pack` — deterministic shard assignment → RPACK shards.
7. `manifest` — manifest generation + SHA-256 + attestation.

Determinism rule: byte-identical outputs for identical inputs (fixed seeds,
no timestamps in KTX2 metadata, sorted iteration everywhere), verified in CI
by double-build of a sample.

## 4. Mipmap policies (offline, full chains, per spec §6)

- **Albedo (opaque)**: decode 8-bit → linearize (sRGB curve) → box/Kaiser
  downsample in linear → re-encode to storage values (D3 keeps *storage*
  UNORM; filtering is still done in linear light). Wrap-aware filtering
  (repeat modes from metadata) so tiled textures don't grow seams in low mips.
- **Albedo (alpha, future)**: premultiplied filtering + color dilation under
  transparent pixels; cutout: per-mip alpha-coverage preservation targeting
  the engine's AREF threshold semantics (audit §6), never plain averaging.
- **Normal**: decode XYZ, renormalize per mip after downsampling, store X/Y.
  Post-compression angular-error validation.
- **Roughness**: linear-space filtering; Toksvig/normal-variance option
  behind a flag, OFF for v1 (no specular consumer yet — revisit with the PBR
  engine milestone).
- **Height**: default average; per-material override (min/max/percentile
  preserve) once the engine defines its parallax/displacement use.
- Chain depth: down to 1×1 for repeat textures, down to 4×4 for clamp/UI
  (configurable); +~33% size accepted as part of the normal format.

## 5. Resolution presets

Explicit rule table in `tools/presets.py` config (not scattered in scripts):

- **very-low** — no pack at all (engine falls back to stock fr3 textures at
  original dims). This preset exists engine-side, not as built artifacts.
- **low** — cap at per-texture `esrgan_dims` (recorded from the ESRGAN pack,
  which is NOT a uniform ×2 — verified: 64²…1024×512).
- **default** — rationalization: master 2048 → 2048 only when
  ESRGAN ≥ 1024, else 1024 (ESRGAN 512) or 512 (ESRGAN 256); i.e. the spec's
  ESRGAN→Default table expressed as `min(master, esrgan*2)`.
- **bonkers** — full master (2048), never upscaled beyond what exists.

Downscaling happens on the *linear-light master* before mip generation, so a
preset's level-0 equals the corresponding mip of the bonkers chain wherever
dimensions align (maximizes shard-content stability). Presets control
resolution only; trilinear/aniso/PBR-quality/LOD-bias are separate engine
settings (spec §7) — note the engine currently forces max anisotropy
unconditionally (audit): M1 adds the missing user controls.

## 6. Sharding & releases

Shard family key: `(game, profile, preset, group, level-cluster)` where
`group` ∈ {albedo, material} and level-cluster is a fixed partition of the
jak1 tpage list (~4-6 clusters, frozen per schema version; new tpages append
to a dedicated overflow cluster — no rebalancing). Assignment is
deterministic (sorted ids). Name:
`jak1-<profile>-<preset>-<group>-<cluster>-<sha256-12>.rpack`.
Size target: 50-250 MiB (validated against the real catalog during the
vertical prototype; GitHub hard limit 2 GiB/asset, 1000 assets/release).
Manifests reference immutable assets across releases; releases use GitHub's
immutable-releases setting; artifacts attested (`gh attestation` /
actions/attest-build-provenance).

Estimated v1 catalog (172 materials, PC BC7+BC5+BC4+BC4, bonkers, full
mips): ~2.8 GiB → ~15-20 shards; default preset materially smaller.

## 7. Validation gates (CI, per texture, thresholds per profile+semantic in config)

Dimensions/mip-count/format/colorspace flags, `ktx validate`, libktx load
test (headless GL where runners allow, else libktx transcode-to-RGBA check),
deterministic hash, max-size budget, decode-and-compare vs pre-encode
reference: albedo ΔE/PSNR + SSIM; normal mean/95p/max angular error; rough/
height MAE + gradient preservation; tileables: edge continuity across
wrap after mip+encode; (future cutouts: per-mip coverage delta). Failure
ladder: better preset → smaller block/other allowed codec → hard fail with
diagnostic.

## 8. CI/CD

**PR pipeline**: changed-files detection → pull only affected LFS objects →
schema + metadata validation → build affected variants/mips → encode a
representative profile subset → validate → determinism double-build →
parser/packer unit tests. Never rebuilds the whole catalog for one texture.

**Release pipeline** (tag `assets-vX.Y.Z`): full affected build with
content-addressed cache → reuse identical published shards (query previous
manifest) → new shards only → manifest + hashes + attestation → create
immutable release → upload → post-publish verification download → smoke
parse. LFS bandwidth note: full rebuilds pull ~2.3 GiB of masters; cache LFS
objects in Actions cache keyed by lockfile of OIDs to stay inside the LFS
bandwidth quota.

## 9. Engine integration milestones (moukrea/jak-project)

- **M1 — KTX2 override path (PC)**: vendor libktx; pack-index load at boot
  (installed manifest → id → shard+offset); override in
  `LoaderStages::add_texture`/common load keyed by fr3 debug names; upload
  compressed mips via `glTexStorage2D`+`glCompressedTexSubImage2D`; skip
  `glGenerateMipmap` for overridden textures; GPU capability detection
  (BPTC/RGTC/S3TC/ETC2/ASTC probing); texture-quality + aniso + trilinear
  user settings; `assets.lock.json` read + local pack state. Acceptance:
  side-by-side identical-or-better vs today's baked-PNG build, VRAM ↓ ~4×
  on overridden textures, works with pack absent (stock fallback).
- **M2 — Asset manager core (shared C++)**: manifest fetch/verify, profile &
  preset resolution, shard diff, resumable download (curl PC), staged install
  to temp dir + atomic manifest switch + rollback + orphan GC. CLI/dev
  trigger first, menu UX after.
- **M3 — Android**: Kotlin downloader stage in the first-launch flow
  (INTERNET permission restored, resumable, size preview, Wi-Fi-only
  option, storage check), shared installer via JNI boundary, pack dir under
  `getFilesDir()/assets/`, GLES format probing (ETC2 core, ASTC ext),
  slim-APK path becomes the norm for texture assets.
- **M4 — Offline/preloaded distributions**: explicit CI job bundling a
  chosen manifest's shards into the PC archive / APK (assets staged like
  iso_data, extracted by the existing sentinel flow).
- **M5 (separate track) — PBR render pass** consuming normal/roughness/
  height (dual GLSL 4.10/GLES 3.20, preprocess.py constraint), gated by
  manifest `required_features`.

## 10. Execution order (maps to spec §19)

| Step | Deliverable | Depends on |
|---|---|---|
| 1. Contracts | 4 JSON schemas + RPACK spec doc + parser/packer + tests | — (audit done) |
| 2. Toolchain | Dockerfile + encoder bake-off report + pinned image | 1 |
| 3. Vertical prototype | 7 representative materials end-to-end (opaque, tileable, normal, rough, height, 2048×1024 case, recale variant) → 3 profiles, loaded by a libktx test harness | 2 |
| 4. Metadata fill | derive_metadata + wrap modes + esrgan_dims + original_dims (needs one local extraction run for tpage dims/combo_ids) + human overrides | 1 |
| 5. Full pipeline + CI | incremental PR flow, release flow, first `assets-v0.1.0` immutable release | 3,4 |
| 6. Engine M1 | PC KTX2 override path | 3 (can start in parallel) |
| 7. Engine M2+M3 | asset manager + Android | 5,6 |
| 8. Migration & presets | remove baked textures from normal builds; wire presets/lock | 7 |

## 11. Open decisions for the user

1. **Level-cluster granularity** for shards (4-6 clusters vs per-tpage) —
   affects update granularity vs request count; decide with real size data at
   step 3.
2. **Import the remaining ESRGAN-only textures** (3 457 PNGs, albedo-only, no
   PBR maps) as additional masters now or after the vertical slice?
3. **jak-project engine work branch strategy** — the fork's release CI is
   inert (upstream-gated); fixing release automation for the fork is a
   separate chore ticket.
4. Toolchain encoder winner — decided by the bake-off, rubber-stamp only.
