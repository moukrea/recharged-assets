# Engine milestone M1 — managed KTX2/RPACK pack loader

Branch: [`feat/recharged-managed-assets`](https://github.com/moukrea/jak-project/tree/feat/recharged-managed-assets)
off `origin/autoport/android-port`. Four commits, each compiling; `gk` builds
with `-DOG_FEAT_PBR=ON`.

## What landed

**1. Readers (`common/`, platform-independent)**
- `util/Ktx2Subset.{h,cpp}` — a ~200-line KTX2 reader for exactly the subset
  the pipeline emits (plain block-compressed 2D, full mip chain, no
  supercompression). Deliberately not libktx: the subset is guaranteed by the
  assets repo's own `ktx validate` gate, and this keeps a large dependency out
  of the fragile Android build. Validates every level's size against the
  format's block geometry before anything is uploaded.
- `util/RPack.{h,cpp}` — RPACK v1 reader (trailer → JSON index → payload
  seeks) with a self-contained SHA-256. Rejects bad magic, index corruption
  (4-byte fast check) and out-of-bounds entries.
- `util/AssetsLock.{h,cpp}` — `assets.lock.json` reader. Refuses a
  `manifest_url` containing `/latest/`: a lock that points at a mutable
  release defeats its purpose. An absent lock is dormant, not an error.

**2. Managed source tier (`game/graphics/opengl_renderer/loader/`)**
- `ManagedAssets.{h,cpp}` — installed-pack index built from
  `managed_assets/<game>/state.json` (only `verified: true` states are read),
  lookups by `(tpage/name, map kind)`, compressed upload via
  `glTexStorage2D` + `glCompressedTexSubImage2D`, VkFormat → GL internal
  format table for all 9 pack formats. Mutex-protected scan (the audited
  GL-thread vs loader-thread race).
- `add_texture` precedence is now **user drop dir > managed > bundled >
  stock** (owner decision). A managed hit uploads the offline mip chain and
  **skips `glGenerateMipmap`**; a failed upload falls back to stock on a
  fresh texture object (post-`glTexStorage2D` storage is immutable).
- Managed PBR maps: all seven suffixes resolved from the pack, same-source
  paired with the managed base, and their statistics read from the pack
  index instead of the three full-texel CPU passes the PNG path runs.

**3. Two-channel normals** — new `u_pbr_mode` bit 128. Pack normals ship X/Y
(BC5 / EAC RG11 / ASTC), so the shader rebuilds
`z = sqrt(max(1 - x² - y², 0))` before the existing surface-gradient/DC
decode, in both `pbr_fused.glsl` and `tfrag3.frag`'s standalone path. Plain
GLSL that compiles under 4.10 core and 3.20 es; the Android blob regenerates
(54 shader pairs + 3 chunks) with both branches present.

**4. Audited defects fixed along the way**
| Defect (docs/AUDIT.md §5) | Fix |
|---|---|
| Streaming budget counted `tex.w*h*4`, blind to replacements | `g_last_add_texture_bytes` — real uploaded bytes, used by both budget sites |
| PBR maps leaked on level unload | `release_pbr_material()` called per texture during eviction; ids join the throttled garbage list |
| Registry keyed by bare `debug_name` → cross-tpage collisions | keyed by `pbr_material_key(tpage, name)`; all four call sites updated |
| No `GL_MAX_TEXTURE_SIZE` guard | oversized packs drop leading mips (free, the chain is offline) and log once |
| Unsynchronised scan state | mutex around the managed index |
| `std::powf` broke clang/libc++ builds | → `std::pow` (portability drive-by) |

**5. Tests** — `test_managed_assets` (standalone target, `common` + gtest
only, no runtime needed): 11 tests including a **cross-language conformance
check** — the fixture pack is written by the pipeline's Python writer and its
SHA-256s are re-derived by the C++ reader.

## Not in M1 (next milestones)

- No downloader: packs must be installed manually into
  `managed_assets/<game>/` with a `state.json`. That is M2 (asset manager
  core + curl) and M3 (Android).
- No user-facing toggle yet — the managed tier rides the Recharged master
  gate; the settings row lands with the asset-manager UI (phase F).
- **B7 parity gate is not yet signed off**: it needs a real play-test
  (village1 side-by-side vs the bundled PNGs, x86 and device). Everything up
  to that point — build, unit tests, integration — is green.

## Notes for the fork owner

- The branch is built with `cmake --preset Release-linux-clang -DOG_FEAT_PBR=ON`.
  Two pre-existing issues were hit: `Loader.cpp:426` uses
  `recharged_pbr_enable` outside any `#ifdef OG_FEAT_PBR`, so the **flagless
  build is broken on this branch** (untouched here — it is your code and a
  guard would conflict); and `std::powf` (fixed).
- Adding a new `.glsl` chunk still requires a cmake re-configure, per the
  comment in `android/CMakeLists.txt` — M1 added no new chunk.
