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

## Remaining

1. **In-game settings rows** (phase F): a `TEXTURE PACK` carousel and a pack
   status/update line in RECHARGED SETTINGS. Deliberately not done here — it
   means editing two parallel menu implementations whose row indices are
   computed with build-flag arithmetic, in a file the fork owner's agent
   changes daily, and it cannot be validated without a play session. The
   plumbing it would drive already exists and is exercised by `gk --assets`.
2. **Android device run**: the Java layer is written and the new logic class
   type-checks, but no APK was built here (that needs the NDK toolchain and the
   owner's game data). First run on an Adreno device is the outstanding check —
   in particular the ASTC upgrade path, which by design only takes effect on the
   *second* launch (the profile is unknown until a GL context exists).
3. **Retire the bundled 71 MB PNG set** from the APK once (2) is confirmed —
   that is the acceptance criterion "no heavy remastered PNG in the normal APK".
4. **Source cleanup**: a handful of masters carry noisy/denormalized normals
   (~15.7° worst-case angular error, identical across all three codecs, so it is
   the source and not the compression).
5. Optional: `pc-bc-legacy` profile if macOS is ever targeted; Basis Universal
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
