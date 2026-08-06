# Project status

Last updated 2026-08-06. Companion documents: [AUDIT.md](AUDIT.md) (engine
facts), [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) (design + decisions),
[ROADMAP.md](ROADMAP.md) (the phase plan), [ENGINE_M1_M2.md](ENGINE_M1_M2.md)
(engine work in detail), [ENCODER_BAKEOFF.md](ENCODER_BAKEOFF.md),
[PROTOTYPE.md](PROTOTYPE.md).

## Done

| Phase | Result |
|---|---|
| Audit | `autoport/android-port` inspected; the design starts from what the engine actually does |
| 1 — Contracts | schema v1: material metadata, manifest, `assets.lock.json`, RPACK v1 + frozen shard clusters |
| 2 — Toolchain | pinned image (bc7enc_rdo / etcpak / astcenc / KTX-Software); Compressonator evaluated and dropped |
| 3 — Prototype | 7 materials × 3 profiles end-to-end, verified |
| 4 — Metadata | 172/172 materials with wrap mode + per-texture ESRGAN dims |
| 5 — Pipeline & CI | full catalog build with a content-addressed cache, quality gates, incremental PR flow, release + offline-bundle workflows |
| Releases | **assets-v0.1.1** — 126 shards, 9.9 GiB, 63 shards reused byte-identically from v0.1.0 |
| M1 — Loader | KTX2/RPACK/lock readers, managed source tier, compressed upload, X/Y normals, 6 audited defects fixed |
| M2 — Asset manager | manifest client, resolver, resumable verified installer with atomic switch; `gk --assets status/install/verify` |
| M3 — Android | INTERNET restored, `AssetPackDownloader` in the first-launch flow, packs land in a wipe-proof directory |
| M4 — Offline | `offline-bundle` workflow + `OFFLINE_BUNDLE=` in the PC packager |
| F — Settings | `HD TEXTURE PACK` on/off row in RECHARGED SETTINGS (instant A/B, no re-download) |

Engine work lives on
[`feat/recharged-managed-assets`](https://github.com/moukrea/jak-project/tree/feat/recharged-managed-assets).

## Verified, not assumed

- 21 C++ tests + 45 Python tests, all green.
- The RPACK contract is checked **across languages**: the fixture is written by
  the Python packer and re-hashed by the C++ reader.
- The offline statistics are checked against a **literal transcription** of the
  engine's own algorithms (this caught a real formula error, hence v0.1.1).
- The engine's readers parse the **actually published** pack: 14 shards, 688
  entries, payloads SHA-verified.
- `gk` really downloaded ~860 MiB from the live release, verified every shard,
  switched atomically and garbage-collected the previous preset.

## Acceptance criteria (spec §18)

| # | Criterion | State |
|---|---|---|
| 1 | Masters independent of the Recharged repo | met |
| 2 | No heavy remastered PNG in the normal APK | **not yet** — the 71 MB bundled set still ships; removing it is gated on the device run below |
| 3 | PC and Android share one KTX2 loader | met (`common/util/Ktx2Subset` + `ManagedAssets`, compiled into both) |
| 4 | Payloads stay compressed all the way to the GPU | met (`glTexStorage2D` + `glCompressedTexSubImage2D`, no RGBA8 conversion) |
| 5 | Android ETC2/EAC works on the baseline profile | code complete, **device-unverified** |
| 6 | ASTC chosen only on capable hardware | met by construction (`GpuCaps` extension probe), device-unverified |
| 7 | PC picks a compatible BC profile automatically | met (incl. the macOS GL 4.1 case by extension string) |
| 8 | Semantically correct mipmaps everywhere | met (offline chains; `glGenerateMipmap` skipped for pack textures) |
| 9 | Presets generated automatically from the masters | met |
| 10 | The manifest describes each version precisely | met |
| 11 | Every build locks an exact asset version | met (`assets.lock.json`, `/latest/` URLs refused) |
| 12 | Changing one texture doesn't re-download the catalog | met — **proven**: v0.1.1 reused 63 of 126 shards |
| 13 | An interrupted download resumes | met (curl Range, Java Range, covered by tests) |
| 14 | A bad install never replaces a working one | met (verify-before-promote, atomic rename, covered by tests) |
| 15 | Every shard is hash-checked | met |
| 16 | Releases are immutable | met (setting enabled on the repo) |
| 17 | A full offline install can be produced separately | met (`offline-bundle` workflow + `OFFLINE_BUNDLE=`) |
| 18 | Compression validation is programmatic | met (per-semantic gates, no manual review) |
| 19 | CI results are reproducible | met (determinism double-build in the PR pipeline) |

17 of 19 are met. Both exceptions are the same dependency: a first run on a
real Android device.

## Remaining

1. **Android device run**: the Java layer is written and the new logic class
   type-checks, but no APK was built here (that needs the NDK toolchain and the
   owner's game data). First run on an Adreno device is the outstanding check —
   in particular the ASTC upgrade path, which by design only takes effect on the
   *second* launch (the profile is unknown until a GL context exists).
2. **Retire the bundled 71 MB PNG set** from the APK once (2) is confirmed —
   that is the acceptance criterion "no heavy remastered PNG in the normal APK".
3. **Source cleanup**: a handful of masters carry noisy/denormalized normals
   (~15.7° worst-case angular error, identical across all three codecs, so it is
   the source and not the compression).
4. Optional: `pc-bc-legacy` profile if macOS is ever targeted; Basis Universal
   evaluation; Toksvig roughness mips.

## Operating notes

- Build the fork with
  `cmake --preset Release-linux-clang -DCMAKE_EXPORT_COMPILE_COMMANDS=OFF -DOG_FEAT_PBR=ON`.
  Without the `COMPILE_COMMANDS` override, CMake 4.4 writes
  `compile_commands.json` after `cmake_install.cmake` and ninja loops on
  "manifest still dirty after 100 tries".
- The fork's **flagless build is broken independently of this work**
  (`Loader.cpp:426` reads `recharged_pbr_enable` outside `#ifdef OG_FEAT_PBR`).
- Never commit `out/cache/` — but never delete it either: it makes a full
  catalog rebuild take ~2 minutes instead of hours.
