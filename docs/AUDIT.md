# Audit of the Recharged fork (moukrea/jak-project) — 2026-08-06

Programmatic audit of the engine fork prior to designing the assets pipeline
(mandated by the project spec §2). All file:line references are against
`moukrea/jak-project` @ `master` (commit `f6f8f277`). Three independent audit
passes: texture/GL pipeline, Recharged-specific additions, build &
distribution.

## Headline facts (the ones that shape the architecture)

1. **Texture replacement is 100% offline.** PNGs from
   `custom_assets/<game>/texture_replacements/<tpage>/<name>.png` are merged by
   the **decompiler** and baked into `.fr3` level files
   (`decompiler/decompilation_process.cpp:289-293` →
   `decompiler/data/TextureDB.cpp:132-154`, before `extract_all_levels` at
   `:302-309`). The shipping game never decodes a PNG (only `stbi_load` in
   `game/` is the window icon, `game/graphics/pipelines/opengl.cpp:277`).
2. **No compressed GPU format anywhere.** Zero hits for
   `glCompressedTex*`/`GL_COMPRESSED`/KTX/DDS/BCn/ETC2/ASTC in engine code.
   Every texture is uncompressed RGBA8 in RAM and VRAM
   (`LoaderStages.cpp:28-29`, unsized `GL_RGBA` internal format).
3. **Mipmapping IS already on.** `glGenerateMipmap` + unconditional max
   anisotropy on every upload path (`LoaderStages.cpp:30-33`,
   `TexturePool.cpp:49-52`, `TextureAnimator.cpp:298-301`). The PS2 mip chain
   itself is not uploaded; fr3 stores no mip levels
   (`common/custom_data/Tfrag3Data.h:308-316` — just `u16 w, h` + one RGBA
   payload; version constant `TFRAG3_VERSION = 43`).
4. **No sRGB handling at all.** No `GL_SRGB8*` formats, no
   `GL_FRAMEBUFFER_SRGB`, no shader gamma. All shader math operates on raw
   stored 8-bit values, matching PS2 behaviour.
5. **No PBR of any kind.** No shader or loader consumes normal / roughness /
   height maps; no naming convention for extra maps exists. The only "PBR"
   vocabulary is upstream glTF custom-level tooling
   (`common/util/gltf_util.cpp:686-841`) feeding the vanilla envmap path.
6. **Android is GLES 3.2** (`android/android_renderer.cpp:61-71`) with shaders
   transpiled offline from `#version 410 core` to `#version 320 es` by
   `game/graphics/opengl_renderer/shaders/preprocess.py` into an embedded blob
   (`Shader.cpp:16-35`). Any new shader must compile under **both** GLSL 4.10
   core and GLES 3.20.
7. **No network stack on Android.** libcurl is vendored and used on desktop
   (speedrun-leaderboard fetches via `game/system/background_worker.cpp`), but
   explicitly excluded from the Android link (`android/CMakeLists.txt:118-122,
   321-325`) and the manifest **removes** the INTERNET permission
   (`AndroidManifest.xml`, `tools:node="remove"`).
8. **Android assets ship inside the APK** and are extracted on first launch by
   `LoaderActivity.java` with per-payload sentinel files and wipe-on-partial
   semantics (`:87-154` iso_data, `:160-200` fr3). Committed today: 4 pre-baked
   fr3 (14 MB: GAME, intro, title, village1).
9. **The fr3 path is constructor-injected** — `Loader` gets its base dir at
   `game/graphics/pipelines/opengl.cpp:92` (PC) and
   `android/android_gfx.cpp:271-273` (Android, which probes `GAME.fr3` and
   falls back to checkerboards). Redirecting to a downloaded-assets dir is a
   two-call-site change.

## Per-question findings

### GL versions / profiles
- PC: SDL3 core profile GL **4.3** (4.1 on macOS), `opengl.cpp:130-145`. GLAD
  desktop loader. No sRGB-capable framebuffer requested.
- Android: SDL3 GLES **3.2**, desktop GLAD loader reused with hand-patched
  entry points (`android_gfx.cpp:172-243`): `glClearDepthf`, `glDepthRangef`,
  `glGetShaderPrecisionFormat`, `glVertexAttribDivisor`, `dlopen` fallback,
  KHR_debug callback.
- Consequences for compression profiles: GL 4.3 core ⇒ BPTC (BC7/BC6H) and
  RGTC (BC4/BC5) guaranteed; S3TC (BC1/BC3) via ubiquitous EXT. macOS GL 4.1 ⇒
  BPTC **not** core, needs runtime extension check. GLES 3.0+ ⇒ ETC2/EAC
  guaranteed core; ASTC via `GL_KHR_texture_compression_astc_ldr`.

### Replacement system
- Match is **by name**: `<tpage_name>/<texture_name>.png`, fallback
  `_all/<texture_name>.png` (`TextureDB.cpp:132-154`). tpage names (e.g.
  `beach-vis-tfrag`) are the game's own texture-page names harvested at
  extraction (`TextureDB.cpp:30,71`).
- `replace_textures` accepts **any resolution** (resizes buffer, overwrites
  w/h). `merge_textures` (applied before, loses to replacements) requires exact
  dims (`TextureDB.cpp:112-117`).
- Paletted "index textures" bypass replacement entirely
  (`extract_level.cpp:300-301`; jak1 doesn't use the CLUT animator).
- No size / count / VRAM limits anywhere. Only ceiling: `u16 w,h` in fr3
  (silent truncation past 65535).

### Mipmapping & filtering detail
- Min/mag filter set **per draw** on texture objects (not sampler objects),
  shared helper `background_common.cpp:101-108`:
  filt_enable ? (`GL_LINEAR_MIPMAP_LINEAR` if mipmap else `GL_LINEAR`) :
  `GL_NEAREST`.
- mipmap=true: tfrag/tie/shrub/etie (`background_common.cpp:159`), merc except
  eyes (`Merc2.cpp:1544,1571`), ocean (`CommonOceanRenderer.cpp:523,541`).
- mipmap=false: sprite (`Sprite3.cpp:632`), sprite-distort, hfrag
  (`Hfrag.cpp:412`), Generic2 (hardcoded TODO,
  `Generic2_OpenGL.cpp:247-249`), DirectRenderer/DirectRenderer2 (debug-gated,
  default off).
- No `GL_TEXTURE_BASE_LEVEL`/`MAX_LEVEL` (except an ocean FBO), no LOD bias,
  no user anisotropy setting (always driver max; note: EXT extension on GLES,
  unguarded `glGetFloatv` — latent GL-error hazard on drivers without it).

### sRGB & alpha
- No sRGB anywhere (see headline 4). Pipeline consequence: in-engine sampling
  returns raw encoded values; any offline tool that gamma-shifts pixels will
  visibly alter the game.
- Alpha test = shader `discard` against GS AREF-derived `alpha_min`
  (`background_common.cpp:113-119`, `tfrag3.frag:26-28`); merc hardcodes
  0.128 (`merc2.frag:46-48`, with `color.a *= 2.0`), sprite 0.016. Double-draw
  path for AFAIL modes (`background_common.cpp:125-131`). No
  alpha-to-coverage. Binary-alpha character must be preserved through mips.

### Level texture lifetime
- Common textures load synchronously at boot (`Loader.cpp:259-268`); level
  textures stream on a pacing budget (20 tex or 1 MiB/frame,
  `LoaderStages.cpp:50-76`). Residency: 3 levels for jak1
  (`goal_constants.h:46-48`), LRU-ish eviction after 180 unused frames
  (`Loader.cpp:365-379,464-533`), throttled deletion.
- VRAM model for an upscale pack: `sum(w*h*4 * 4/3)` over resident levels, no
  guard rails. One 2048² RGBA8+mips ≈ 22.4 MiB; the same in BC7 ≈ 5.6 MiB,
  ETC2 RGB8 ≈ 4.2 MiB.

### Builds & distribution
- PC packaging = `.github/scripts/releases/extract_build_unix.sh`: binaries +
  `data/` (goal_src, game/assets, shaders, **custom_assets** — so a PNG pack
  ships to users, but each user's own `extractor` run bakes it). `out/` (fr3)
  is never shipped.
- Runtime root resolution: `FileUtil.cpp:188-238` (`<exe>/data` →
  `path_to_data_folder`; single global root, no search path). Android
  synthesizes the same layout in app-private storage and symlinks
  `out/jak1/iso → iso_data/jak1` (`android_goal_main.cpp:265-290`).
- Android Gradle project (`compileSdk 34`, `minSdk 29`, arm64-v8a, flavors
  jak1/2/3) shells out to CMake, builds `gk` as shared lib
  (`android/CMakeLists.txt:431`), zstd for fr3. `-PslimIso=true` builds a
  ~77 MB APK without iso_data.
- CI: 12 workflows, none Android; release pipeline hard-gated on
  `github.repository == 'open-goal/jak-project'` (`release-pipeline.yaml:28,95,166`)
  — inert on the fork.
- No zip library vendored (zstd only, `common/util/compress.h`); no file
  mmap utilities (all stdio whole-file reads); xdelta3 and lzokay present but
  unrelated.

### Settings
- Graphics settings struct is stock upstream (`pckernel-h.gc:122-184`):
  supersampling `gfx-resolution`, `gfx-anisotropy` (unused by the C++ side's
  unconditional max), `gfx-msaa` (the only one surfaced in the menu,
  `progress-pc.gc:92,2765-2771`), PS2-fidelity toggles. **No texture-quality
  knob exists**, and none could work today since resolution is frozen into
  fr3 at build time.

## What this means for the assets project

| Spec assumption (§) | Audit reality | Resolution |
|---|---|---|
| Runtime KTX2 loader (§16) | No runtime texture loading exists at all | Build a **new runtime override path** keyed by (tpage, name) at fr3-upload time; stock fr3 textures become the fallback |
| "Confirm mipmapping" (§2) | Already on everywhere (driver-generated) | Offline chains replace `glGenerateMipmap` only for overridden textures |
| BC7 **sRGB** / ETC2 **sRGB** (§4) | Engine is sRGB-oblivious; shaders expect raw values | Encode with sRGB-aware filtering offline, but bind **UNORM** views initially; sRGB internal formats only if/when the engine gains a gamma-correct pipeline |
| Normal/roughness/height consumed (§5-6) | No consumer exists | Pipeline ships material maps from day one; the render pass is a separate engine milestone |
| Asset manager downloads on Android (§15) | No HTTP stack, INTERNET permission removed | Downloader in the Java/Kotlin layer (extends the existing LoaderActivity pattern) or curl+TLS reintroduction — decision recorded in the plan |
| "No heavy PNG in the normal APK" (§1) | Already true (nothing committed); but 4 fr3 are committed and iso_data is staged locally | Keep; downloaded packs land beside them |
