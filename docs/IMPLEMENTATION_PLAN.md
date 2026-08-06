# Recharged Assets Pipeline — Implementation Plan (v2)

Status: **proposed** (v2, 2026-08-06). v1 was based on an audit of the stale
`master` branch and is superseded. This plan builds on
[AUDIT.md](AUDIT.md) (branch `autoport/android-port` @ `ec554ed6f`).

Objective (spec §1): this repo is the source of truth for the remastered
textures + material maps; **Recharged binaries download compressed asset
packs from this repo's immutable GitHub releases instead of embedding them**
(today: 71 MB of PNGs inside a ~580 MB APK). Offline/preloaded distributions
remain possible as a separately produced artifact.

---

## 0. Ground truth

**This repo**: 172 deduplicated jak1 material sets (158 canonical + 14
`recale` variants; albedo/normal/roughness/height at 2048², masters = the
owner's full regenerations), `metadata/jak1/{placements,materials}.json`.
The raw ESRGAN pack is **never** imported here (owner decision) — it is only
a metadata source (`esrgan_dims` per texture, non-uniform: 64²…1024×512).

**The engine** (see AUDIT.md): already has a runtime PNG replacement system
keyed `<tpage>/<name>` (+`_suffix` for 7 PBR map kinds), with source
precedence user > bundled > stock and in-game toggles; a full PBR path
(POM + tessellation displacement) behind `OG_FEAT_PBR`; **no compressed
formats, no KTX reader, no downloads, no INTERNET permission on Android**;
per-map statistics measured at decode time that the shaders require; a set
of loader defects (budget blindness, PBR map leak on unload, debug-name
collision, no size guard) that any new loader must not inherit.

**Working constraints** (owner):
- Engine changes on a **new branch off `origin/autoport/android-port`**
  (an autonomous agent pushes there continuously); rebase before merge.
- The fork's release CI is out of scope — users build the game binary
  themselves. Assets and binaries are fully decoupled; the contract between
  them is ONLY `assets.lock.json` + the manifest schema.

## 1. Architecture decisions

**D1 — The pack loader is a new SOURCE TIER in the existing replacement
system.** `custom_tex` gains a third index: the *managed* (downloaded) pack,
between user and bundled: **user drop dir > managed pack > bundled >
stock**. Key space is unchanged (`<tpage>/<name>` and suffixed maps), which
is exactly what `metadata/jak1/placements.json` records. On a managed-pack
hit, `add_texture` reads a KTX2 payload from an RPACK shard (via the
installed manifest's index) and uploads with
`glTexStorage2D` + `glCompressedTexSubImage2D` (all mip levels, no
`glGenerateMipmap`), instead of stbi-decoding a PNG. Stock fr3 textures
remain the universal fallback; the user PNG drop dir keeps working unchanged
for modders (and stays PNG-based).

**D2 — KTX2 is the only texture container** (spec §3). Native GPU blocks per
profile, no transcoding on user machines, `libktx` vendored for reading.
Basis Universal: not in v1 (may be evaluated later as an extra
"universal" profile).

**D3 — GPU profiles** (spec §4, confirmed by the audit's GL versions):

| Profile | Gate | Albedo | Normal | Rough/Height/Mask | Specular/Emissive (future) |
|---|---|---|---|---|---|
| `pc-bc` | GL 4.2+/BPTC (4.3 requested on non-mac) | BC7 | BC5 (X/Y) | BC4 | BC7 |
| `pc-bc-legacy` | S3TC+RGTC (macOS GL 4.1) | BC1/BC3 | BC5 | BC4 | BC3 |
| `android-etc2` | GLES 3.0+ core (always true, 3.2 requested) | ETC2 RGB8 / RGBA8-EAC / punchthrough per alpha_mode | EAC RG11 (X/Y) | EAC R11 | ETC2 RGB8 |
| `android-astc` | `KHR_texture_compression_astc_ldr` | ASTC 6×6 (5×5/4×4 per metadata) | ASTC 4×4 X/Y **vs EAC RG11 by measured error** | EAC R11 unless ASTC measurably wins | ASTC 6×6 |
| fallback | none of the above | uncompressed RGBA8 mip chain from the pack — explicit and logged, never silent | | | |

Engine detects real support at context init (BPTC/RGTC/S3TC ext strings on
PC incl. the macOS 4.1 case; ASTC ext on GLES) and the asset manager selects
the profile accordingly (spec §15 order).

**D4 — Gamma: UNORM storage + offline linear-light mips.** The engine has no
GL sRGB state; PBR shaders hand-decode albedo/specular/emissive with
`pow(2.2)` and read data maps linear. So: mips are *filtered* in linear
light offline (a strict quality fix — today the driver averages
sRGB-encoded bytes), but *stored* as the same encoded values in UNORM
formats. No sRGB internal formats until the engine ever moves gamma into the
texture unit; the manifest's per-map `colorspace` field keeps that door open
without a schema change.

**D5 — Normal maps ship X/Y two-channel (BC5 / EAC RG11)**; roughness and
height ship single-channel (BC4 / EAC R11 — today they waste 4× as RGBA8).
The engine currently samples normals `.xyz`: the loader/shader gain a
per-material "reconstruct Z" mode (a `u_pbr_mode`-style bit or a
sampler-format flag from pack metadata). Spec §4/6.3 compliance; angular
error validated offline.

**D6 — Decode-time statistics move offline.** `normal_dc_x/y`,
`height_mean`, `height_norm`, `height_lambda_tiles` are computed by the
pipeline on the exact preset-resolution pixels and shipped per material in
the pack index / manifest; the engine reads them instead of measuring
(compressed blocks can't be cheaply measured, and offline values are also
deterministic across devices). The measuring code stays as the fallback for
PNG sources (user drop dir).

**D7 — Mipmap policies** (spec §6, informed by shader reality):
- Albedo: decode 2.2 → linear filtering (wrap-aware per metadata wrap mode)
  → re-encode; premultiplied filtering + dilation for future alpha
  textures; cutout coverage preservation targeting the engine's AREF
  semantics.
- Normal: renormalize per mip, then project to X/Y.
- Roughness: linear filtering; optional Toksvig folding is **deferred** —
  the shader already widens roughness with mip-variance at runtime
  (`pbr_fused.glsl:428`), so doubling it must be evaluated, not assumed.
- Height: full chains are **load-bearing** (tess `textureLod` band-limiting
  and `pbr_cavity()` sample specific mips); default average, per-material
  override policy. Height stats (D6) computed on level 0 of each preset.
- Full chains to 1×1 (repeat) / 4×4 (clamp); +~33% size accepted.

**D8 — Resolution presets** (spec §7): explicit rule table, single source
master. very-low = no pack (stock fr3 textures); low = per-texture
`esrgan_dims`; default = `min(master, esrgan×2)`; bonkers = full master.
Preset ≠ other settings: trilinear/aniso/PBR toggles/displacement are
engine settings (they exist already; anisotropy needs the missing user
control + GLES ext guard while we're in that code).

**D9 — RPACK v1** (spec §11): magic `RPK1`, concatenated KTX2 payloads
16-byte aligned, footer index (stable id, `tpage/name` key + suffix kind,
offset, size, sha256, semantic, format, D6 stats), random access, stdio
now / mmap-ready. No zip (none vendored; Deflate pointless on GPU blocks).

**D10 — Sharding & releases** (spec §12): family =
`(game, profile, preset, group, level-cluster)`; `group` ∈ {albedo,
material}; level-cluster = fixed partition of jak1 tpage list (v1: by level
prefix, ~4-6 clusters + overflow; frozen per schema version). Deterministic
assignment, content-hash names
(`jak1-pc-bc-default-albedo-c03-<sha12>.rpack`), target 50-250 MiB,
immutable GitHub releases, manifests reference assets across releases,
artifacts attested. Material-map shards download only when the binary
declares PBR support (`engine_features` in the lock/manifest handshake) —
albedo-only installs stay small.

**D11 — Asset manager** (spec §15): shared C++ core (manifest parse +
verify, GPU profile detection, preset resolution, shard diff, staged
install to temp dir, atomic manifest switch, rollback, orphan GC, offline
behavior per spec). Transport is per-platform:
- **PC**: libcurl (already linked), resumable via Range.
- **Android**: Kotlin downloader stage in the LoaderActivity flow (restore
  INTERNET permission, resumable, size preview before first download,
  Wi-Fi-only option, storage check), handing verified files to the shared
  installer. Rationale: no TLS stack in the native Android build (curl
  excluded) and the first-launch UX already lives there with proven
  wipe-on-partial sentinel semantics.
- **Landing zone Android**: a new persistent managed dir under
  `<filesDir>` (e.g. `files/managed_assets/<game>/`) — NOT
  `custom/<game>/…`, which the updater wipes on every APK version change.
  PC: `<appdir>/managed_assets/` beside the existing `custom/`.

**D12 — Engine defect fixes ride the same milestone.** The pack loader work
touches exactly the defective code paths; the milestone includes: real
byte accounting for replacement/pack uploads (budget + VRAM readout), PBR
map release on level unload + registry keyed by `tpage/name`, a
`GL_MAX_TEXTURE_SIZE` guard with explicit downscale-or-fail, wiring
`invalidate()` (rescan on pack install / settings toggle), and a mutex (or
call-once) around the source-index scan.

## 2. Contracts (schema_version = 1) — `schemas/`

- **Material metadata** (per material, this repo): stable id
  (`jak1/<tpage>/<name>`), original texture key + original dims,
  `esrgan_dims`, master dims, per-map semantic/colorspace/alpha_mode/wrap
  (seeded from local pipeline verdicts), mip policy, per-profile format
  overrides, level groups (from placements), `variant_of`/`recale`,
  `required`, relations. Auto-derived proposals + explicit override files;
  ambiguity never resolved from channel count alone (spec §5).
- **Manifest**: schema_version, asset_version, games, engine_compat
  (min/max Recharged version, min loader version, `required_features` e.g.
  `pbr`), profiles, presets, shard list (family tuple, sha256, size,
  release-asset URL), per-shard entry index delegation, D6 stats location,
  signing/attestation block. Represents the complete state across releases.
- **RPACK**: as D9, with a written format spec + reference parser
  (Python for CI, C++ for the engine) and golden-file tests.
- **`assets.lock.json`** (lives in the game repo, spec §14): pinned
  asset_version + manifest URL + sha256 + `required`; no implicit `latest`.
  Game-side CI integration is limited to a loader unit test consuming a
  fixture manifest — no fork release automation (out of scope).

## 3. Pipeline tools — `tools/` (Python + pinned native encoders)

Toolchain image `ghcr.io/moukrea/recharged-assets-toolchain` (Dockerfile in
repo): KTX-Software (`ktx create/validate`, libktx), Arm `astcenc`, BCn/ETC2
encoder chosen by a programmatic **bake-off** (`benchmark_encoders.py`
scoring candidates — Compressonator, bc7enc_rdo, etcpak, toktx — on quality
metrics × time over a representative set; winner pinned, report committed).

Deterministic, content-addressed stages (cache key = master hash + tool
versions + policy hash): `derive_metadata` → `make_variants` (presets) →
`make_mips` (semantic policies, D7) → `compute_stats` (D6) → `encode`
(profiles, D3) → `validate` (§4 below) → `pack` (RPACK shards) →
`manifest`. Determinism enforced by CI double-build (no timestamps in KTX2
metadata, sorted iteration, fixed seeds).

## 4. Validation gates (spec §10)

Per texture: dims, mip count, KTX2 format + `ktx validate`, libktx load,
alpha consistency vs original texture alpha (guards the "replaced a
transparent original with opaque" class), deterministic hash, size budget,
decode-and-compare metrics by semantic — albedo ΔE/SSIM; normal mean/95p/max
angular error (post X/Y reconstruction); rough/height MAE + gradient/extrema
preservation; tileables edge continuity after mip+encode; **content sanity:
reject mislabeled containers (the bundled set already contains JPEG-in-.png)
and palette-mode PNGs**. Thresholds per profile+semantic in config; failure
ladder: higher-quality encode → block-size change → alternate codec → hard
fail with diagnostic.

## 5. CI/CD (this repo only)

- **PR**: changed-file detection → fetch only needed LFS objects → schema +
  metadata validation → build affected variants/mips/encodes (representative
  profile subset) → validate → determinism check → parser/packer tests.
  Never a full-catalog rebuild for one texture.
- **Release** (tag `assets-vX.Y.Z`): full affected build with cache → reuse
  already-published identical shards → new shards only → manifest + SHA-256
  + attestation → immutable release → post-publish download verification.
  LFS bandwidth guarded by an Actions cache keyed on LFS OIDs.

## 6. Engine milestones (new branch off `autoport/android-port` each)

- **M1 — Managed-pack source + KTX2 loader (PC first, code shared):**
  vendor libktx; manifest/RPACK reader; third source tier in `custom_tex`
  (D1); compressed upload path incl. X/Y-normal mode (D5) and offline stats
  consumption (D6); GPU compression capability detection; defect fixes
  (D12); `assets.lock.json` reader + installed-state file. Acceptance:
  village1 with the managed pack ≥ visually identical to the bundled PNG
  set, VRAM for those materials ↓ ~4-6×, loader hitches gone (real byte
  budget), works with pack absent.
- **M2 — Asset manager core (shared C++)** + PC curl transport; CLI/dev
  trigger; then menu UX (RECHARGED SETTINGS gains pack status/preset rows).
- **M3 — Android:** Kotlin download stage (INTERNET permission back,
  resumable, size preview, Wi-Fi-only), managed dir under `<filesDir>`,
  JNI/file handshake to the shared installer, GLES profile pick
  (ASTC ext → astc else etc2). Bundled 71 MB set leaves the APK once parity
  is proven (M4 keeps an offline path).
- **M4 — Offline/preloaded distributions:** explicit job bundling a pinned
  manifest's shards into the PC archive / an "offline APK" flavor reusing
  the existing zip+sentinel extraction.
- **M5 — quality follow-ups:** per-preset settings coupling (aniso user
  setting + GLES guard), Toksvig evaluation, ASTC-vs-EAC decision data,
  Basis universal profile evaluation.

## 7. Execution order

| Step | Deliverable | Notes |
|---|---|---|
| 1. Contracts | 4 schemas + RPACK spec + Python/C++ parsers + tests | starts now |
| 2. Toolchain | Dockerfile + encoder bake-off + pinned image | |
| 3. Vertical prototype | 7 representative repo masters end-to-end (the 4 village1 materials shared with the bundled set — `vil1-sages-stonewall-01`, `vil1-sages-strawroof-01`, `vil-beachrock`, `vil-beach-01` — plus 3 covering the remaining spec §19 cases: tileable-both-axes, clamp/UI-like, 2048×1024): 3 profiles, loaded by a libktx harness AND by an M1 spike in the fork on a throwaway branch; side-by-side vs the bundled PNGs for the 4 shared ones | proves stats/normal-XY/gamma on the real renderer |
| 4. Metadata fill | derive_metadata + wrap modes + esrgan_dims + original dims/keys | placements already imported |
| 5. Full pipeline + CI | incremental PR flow, release flow, `assets-v0.1.0` | |
| 6. Engine M1 | PR onto a fresh branch off android-port | rebase discipline |
| 7. M2 + M3 | asset manager + Android downloads | |
| 8. M4 + migration | offline distributions; bundled set removed from APK | spec §1 satisfied |

## 8. Decisions (settled with the owner, 2026-08-06)

1. **Bundled village1 test materials are NOT imported.** The 3 fork-only
   materials (`vil-wallplaster`, `vil1-jng-leafyground`,
   `vil-hut-roof-tile-01`) were test material and are dropped; for the 4
   names existing in both, **this repo's masters are canonical** (the APK
   set includes the JPEG-in-png `vil-beach-01` defect). The bundled APK
   tier goes away after M3 (offline distributions via M4 instead).
2. **User drop dir always outranks the managed pack** — user custom assets
   stay top priority; ours are the "recharged" tier below them
   (user > managed/recharged > bundled-until-M3 > stock).
3. **Shard level-clusters** (pipeline's call): a frozen table
   `schemas/shard-clusters-v1.json` partitioning jak1 tpage prefixes into
   **6 named clusters** grouped by game geography (village1+beach+training,
   jungle+misty, village2+swamp+rolling+sunken, snow+firecanyon+ogre,
   village3+cave+lavatube, citadel+finalboss+intro) **plus an `overflow`
   cluster** for any future tpage. Assignment is by tpage prefix lookup,
   deterministic, frozen per schema version (a new tpage lands in
   `overflow`, never rebalances existing shards). Cluster sizes get
   validated against the 50-250 MiB target with real encode data at step 3
   and may be re-cut **once** before `assets-v1.0.0`; frozen afterwards.
4. **Asset manager surface** (pipeline's call, phased): M2 ships a
   CLI/dev trigger; M3 puts the initial mandatory download in the Android
   first-launch (LoaderActivity) flow with size preview; the in-game
   "RECHARGED SETTINGS" page gains a small row group last (installed
   version/preset carousel/check-for-updates/re-verify), same rows on PC
   and Android, greyed under the master toggle like every other row.
