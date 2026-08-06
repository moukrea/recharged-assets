# Audit of the Recharged fork — branch `autoport/android-port` @ `ec554ed6f` (2026-08-06)

Programmatic audit of the engine fork (spec §2), v2. **v1 of this document
audited the stale `master` branch and was wrong on the central point** — the
active branch is `autoport/android-port` (daily commits, no merge base with
master). Four independent audit passes on the correct branch: runtime
replacement system, PBR pipeline, GL fundamentals, builds & distribution.
All file:line references are against `ec554ed6f`.

> Branch workflow constraint: an autonomous agent on another machine pushes
> to `autoport/android-port` continuously. Any engine work for this project
> happens on a fresh branch off `origin/autoport/android-port`, rebased
> before merging — never direct commits.

## Headline facts

1. **Runtime PNG texture replacement EXISTS** —
   `game/graphics/opengl_renderer/loader/CustomTextureReplacements.cpp`.
   At fr3 texture-upload time (`add_texture`, `LoaderStages.cpp:134-188`),
   two PNG indexes are consulted and, on a hit, the PNG is uploaded in place
   of the baked fr3 texture. Sources & precedence: **user drop dir >
   package-bundled first-party set > stock**, with in-game toggles.
2. **A full PBR material pipeline EXISTS** (`OG_FEAT_PBR`, off by default,
   enabled via `./build.sh --pbr`): seven suffixed maps per base texture —
   `_normal _roughness _metallic _ao _height _specular _emissive` — bound to
   texture units 11-17 with a `u_pbr_mode` bitmask, consumed by a fused
   lighting path (`pbr_fused.glsl` + friends) in the tfrag, TIE (both
   variants), TIE-wind and shrub renderers. POM + **tessellation
   displacement** (`tfrag3_tess.*`, tfrag only), realtime sun shadow map,
   SSAO/HBAO/GTAO, follow-probe IBL, procedural grass. merc (characters),
   hfrag, generic and grass are outside PBR.
3. **The APK bundles a 7-material first-party set** —
   `custom_assets/jak1/recharged_textures/village1-vis-tfrag/`, 28 PNGs,
   71 MB git-tracked (base+normal+roughness+height at 2048², one 2048×1024;
   no metallic/ao/specular/emissive ships). APK ≈ **560-580 MB** (cgo pack
   ~108 MB + custom pack ~420 MB stored-not-deflated + libgk). The engine
   code itself calls a packaged-asset change "a 581 MB re-download"
   (`kmachine.cpp:1052`). This is exactly what the assets pipeline must
   replace with downloads.
4. **No download infrastructure anywhere.** Desktop links libcurl (idle
   `BackgroundWorker`); Android excludes curl (`android/CMakeLists.txt:433-437`)
   and the manifest **removes** the INTERNET permission
   (`AndroidManifest.xml:19-20`). No HTTP/OkHttp/DownloadManager in the Java
   layer. No remote-manifest or update-check concept in C++/Java/GOAL
   ("version" today = local content md5).
5. **No compressed GPU texture format anywhere, no KTX/DDS reader** —
   zero hits for `glCompressedTex*`/`COMPRESSED_`/KTX/BCn/ETC2/ASTC. Every
   texture (including every 2048² replacement PNG and every single-channel
   height/roughness map) is uploaded as **uncompressed unsized `GL_RGBA`**
   with driver-generated mips.
6. **The engine measures per-map statistics at PNG decode time** and the
   shaders depend on them: normal-map DC (mean tangent-space gradient,
   `LoaderStages.cpp:280-302`), height mean + robust half-range
   (`:348-391`), height feature wavelength `height_lambda_tiles` by
   mip-energy analysis (`:45-128`). These drive `u_pbr_normal_dc`,
   `hnorm()` and the POM/tessellation amplitude law. **A compressed-payload
   pipeline must precompute these offline and ship them as metadata** —
   they cannot be measured cheaply from compressed blocks at load.

## 1. The runtime replacement system (CustomTextureReplacements)

- **Key space**: `scan_dir` (`CustomTextureReplacements.cpp:254-298`)
  registers up to 4 keys per PNG: full relative path minus extension; the
  same with a leading `texture_replacements/` wrapper stripped ("how
  internet packs ship"); `<tpage>/<stem>` for nested
  `<tpage>/<tex>/<tex>.png` layouts (the bundled set's layout); bare stem as
  a last-resort fallback (subsumes the decompiler's `_all/`). Lookup:
  exact `<tpage>/<name>` then bare name (`find_key`, `:301-312`).
- **Gates** (`gfx.h:138-161`, helpers `:502-543`): master
  `recharged_master` (default ON, env/prop override `OG_RECHARGED` /
  `debug.opengoal.recharged`); user source gated by `load_custom_assets`
  (default OFF); bundled base swaps by `recharged_textures` (default ON);
  bundled **PBR maps gated only by the master** — deliberately not by the
  base-swap toggle (`resolve_suffixed`, `:405-455`).
- **Same-source pairing** (owner rule, `:428-450`): PBR maps apply only from
  the same source as the winning base (user↔user, bundled↔bundled); a stock
  base accepts user > bundled maps.
- **GL upload of a replacement** (`LoaderStages.cpp:166-188`): stbi decode
  forced RGBA8 → `glTexImage2D(GL_RGBA, rep->w, rep->h, …)` +
  `glGenerateMipmap` + unconditional max anisotropy. No min/mag/wrap set on
  the base (GL defaults until a renderer parameterizes). PBR maps via
  `make_map` (`:251-266`): `GL_RGBA`/`GL_UNSIGNED_BYTE`,
  `glGenerateMipmap`, trilinear, `GL_REPEAT`, **no anisotropy**.
- **Author tooling**: drop a `dump_keys` marker file → every uploaded
  texture's `tpage/name` key appended to `texture_keys_dump.txt`
  (`:734-750`; note the marker path is one level above what the header
  comment claims). `.autoport/pbr_material_prep.py` converts ambientCG sets
  into game materials.
- **Settings/menu**: "RECHARGED SETTINGS" page (`progress-pc.gc:7169+`).
  Relevant rows: RECHARGED MASTER, LOAD CUSTOM ASSETS, RECHARGED TEXTURES,
  PBR MATERIALS, TEXTURE RELIEF (slider 0..3, default 1.5), SPECULAR
  INTENSITY, DISPLACEMENT (Off/Parallax/Tessellation, default Parallax),
  ENV PROBE, PBR ISOLATE, REALTIME LIGHTING + ambient/shadow family, AO
  (Off/SSAO/HBAO/GTAO), FOLIAGE WIND, ENHANCED MODELS + per-character HD
  looks, PHYSICS, GRASS submenu. All persist to `settings.ini` except
  `follow-probe` (defect). Rows compile out with their `OG_FEAT_*` flag;
  GOAL mirrors via generated `recharged-flags.gc`.

## 2. PBR pipeline facts that constrain asset authoring

- Suffix → unit/bit: `_normal`→11/1, `_roughness`→12/2, `_metallic`→13/4,
  `_ao`→14/8, `_height`→15/16, `_specular`→16/32, `_emissive`→17/64
  (`LoaderStages.cpp:221-241`).
- Channels read: normal `.xyz` (3-channel tangent-space; **no 2-channel X/Y
  path exists yet** — reconstruction-in-shader is a pipeline+shader change),
  roughness/metallic/ao/height `.r`, specular/emissive `.rgb`.
- Gamma model: no GL sRGB state anywhere; the PBR shaders hand-decode
  albedo/specular/emissive with `pow(2.2)` and re-encode `pow(1/2.2)`
  (`pbr_fused.glsl:389,411,700,717`); normal/rough/height/ao/metal read
  linear. Consequence: mip generation currently averages sRGB-encoded bytes
  (wrong space) — offline linear-light mips are a strict improvement, while
  storage stays UNORM-encoded values.
- Defaults when a map is absent: roughness 0.9, metallic 0.0, ao 1.0.
  Specular follows the UE dielectric convention (F0 capped 0.08 unless
  metallic). Emissive unlit, added on top.
- Normal maps decoded as **surface gradient** with the measured DC
  subtracted (zero-mean perturbation; below-horizon slid, not clamped).
  Height consumed through `hnorm()` (recentre by `height_mean`, rescale by
  `height_norm`) and the amplitude law scaled by `height_lambda_tiles` ×
  measured per-renderer UV density. Tessellation samples height with
  explicit band-limited `textureLod` and re-derives the normal by central
  differences — **full mip chains on height maps are load-bearing**.
- Tessellation: tfrag-only program (`TFRAG3_TESS`), triangle patches,
  GLES 3.2-core-with-EXT/OES-fallback handling (`Shader.cpp:32-103`);
  demoted to Parallax with a logged reason on any failure. POM/tess
  cross-fade sums to one full displacement (`pbr_fused.glsl:109-120`).
- The bundled `vil-beach-01` maps are **JPEG data in `.png` files** (lossy
  normal/height) — the pipeline's validation gates must catch this class.

## 3. GL fundamentals (re-verified on this branch)

- PC: SDL3 core GL **4.3** (4.1 macOS), `opengl.cpp:130-141`. Android:
  GLES **3.2** (`android_renderer.cpp:70-73`), desktop glad loader with
  hand-patched entry points. Shaders `#version 410 core` transpiled to
  `#version 320 es` by `preprocess.py` (glob-driven — new shaders and
  `.glsl` chunks are picked up; chunk additions need a cmake reconfigure).
- Formats: unsized `GL_RGBA` everywhere on the texture path;
  `GL_UNSIGNED_INT_8_8_8_8_REV` desktop vs `GL_UNSIGNED_BYTE` Android.
- Mipmaps generated at every upload; anisotropy always driver max, no user
  setting (unguarded `glGetFloatv` = latent GL-error on GLES drivers
  without the EXT). Min/mag decided per draw
  (`setup_opengl_from_draw_mode`): mipmapped for tfrag/tie/shrub/merc,
  not for sprite/hfrag/generic (hardcoded TODO). No `GL_TEXTURE_BASE_LEVEL`,
  no LOD bias on textures. One `GL_MAX_TEXTURE_SIZE` guard exists — for the
  shadow map only (`background_common.cpp:1193-1201`).
- Alpha: unchanged from upstream (AREF-derived discard, merc 0.128, sprite
  0.016, AFAIL double-draw, no alpha-to-coverage).
- Level lifetime: 3 resident levels both platforms, 180-frame eviction,
  deferred deletes (20 textures/frame). Streaming budgets: 4.5 ms /
  1 MiB/frame — **accounted with the original baked dimensions**, so
  replacements are invisible to the budget.

## 4. Builds & distribution

- **Android**: Gradle flavors jak1/2/3/collection (`minSdk 29`, arm64-v8a),
  assets = two zips (`<game>_cgo.zip` ~108 MB, `<game>_custom.zip` ~418 MB
  raw, PNGs stored uncompressed). `LoaderActivity` unpacks with
  wipe→unpack→stamp-last sentinels (`.cgo_pack_stamp_<game>`,
  `.custom_pack_stamp_<game>`), per-entry CRC32 + file-count checks, keyed
  on manifest `version=` (content md5). A third flow streams a user-picked
  `<game>_assets.zip` (zip-slip-guarded) into the external game root.
  Gradle shells out to CMake; **bare `./gradlew` builds silently compile
  out all `OG_FEAT_*` features** — `build.sh android-arm64 --pbr …` is the
  only correct entry point.
- **Runtime asset roots** (`FileUtil.cpp:450-498`):

  | Root | Android | PC packaged | PC repo |
  |---|---|---|---|
  | user drop (`get_custom_assets_replacements_dir`) | `<externalRoot>/<game>… = <chosenBase>/jak1/custom_assets/` (flat) | `<root>/custom_assets` with `--game-root` | `custom_assets/jak1/texture_replacements/` |
  | bundled (`get_bundled_recharged_textures_dir`) | `<filesDir>/custom/jak1/recharged_textures/` | `<appdir>/custom/recharged_textures/` | `custom_assets/jak1/recharged_textures/` |

  **The Android bundled root is destroyed on every APK version change**
  (`LoaderActivity.java:1087`) — a downloaded pack must NOT land there.
  `<filesDir>` itself persists across APK updates; only the app's own
  wipe logic clears `custom/<game>`.
- **PC packaging bug**: `package_release.sh` never stages
  `recharged_textures/` or `mesh_index/` although its generated `run.sh`
  points `--custom-assets` at them — PC packages silently lose the bundled
  set (Android has a `MISSING DERIVED FILE` guard for this class; PC has
  none).
- **Precedent for external-overrides-package**: `physics_chains.txt`
  (`kmachine.cpp:1050-1069`) exists precisely to avoid "a 581 MB
  re-download" for a parameter tweak.
- **CI**: `port-ci.yaml` builds gk/goalc on 4 desktop targets from
  snapshot pushes (history has >100 MB blobs GitHub rejects); no APK CI, no
  release automation (upstream's release-pipeline is repo-gated and inert).
  Out of scope for this project per owner decision — users build the game
  binary themselves; assets are fully decoupled.
- Repo: 573 MB tracked / 4.6 GB `.git`; `recharged_textures` is the one
  texture path deliberately committed (everything else texture-ish is
  gitignored).

## 5. Defects found in the current system (relevant to the pipeline design)

1. `custom_tex::invalidate()` is dead code — freshly dropped PNGs are never
   picked up without toggling `load-custom-assets?` or restarting.
2. Unsynchronised `g_state` index shared between GL thread and loader
   thread (PBR builds) — data race on first scan / gate transitions.
3. **PBR map GL textures leak on level unload**; `g_pbr_materials` never
   pruned; only freed when the same debug name re-registers.
4. `g_pbr_materials` keyed by bare `debug_name` (not `tpage/name`) —
   cross-tpage same-name collisions delete a live material's maps.
5. Upload budget and VRAM readout use original baked dims — a 2048²
   replacement is accounted as e.g. 64 KB; loader hitches (one
   `add_texture` can decode 8 × 2048² PNGs + 3 full-texel stat passes in
   one "1 MiB" charge, measured multi-hundred-ms).
6. No `GL_MAX_TEXTURE_SIZE` guard and no `glGetError` on the replacement
   path — oversized PNG ⇒ silent black texture.
7. VRAM reality: one 2048² RGBA8 + mips ≈ 21.3 MiB; the 7 bundled village1
   materials ≈ **~555 MiB GPU memory** — uncompressed single-channel maps
   waste 4×. GPU compression is existential for the catalog (172 materials),
   not an optimization.
8. `follow-probe` setting not persisted; `dump_keys` ungated + per-texture
   `fs::exists`; PC package drops bundled assets (§4); `vil-beach-01`
   JPEG-in-png; Gradle-direct builds lose feature flags.

## 6. What this means for the assets project

| Topic | Reality on the branch | Consequence for the pipeline |
|---|---|---|
| Integration seam | `add_texture` already consults sources by `<tpage>/<name>` (+suffix) | The downloaded-pack loader plugs in as a **new source tier** in the existing precedence (user > **downloaded** > bundled > stock); key space = our `placements.json` keys |
| Payloads | PNG→RGBA8 only | Add a KTX2/RPACK read path + `glCompressedTexImage2D`/`glTexStorage2D` upload; skip `glGenerateMipmap` for pack textures |
| PBR maps | 7 suffixes consumed; normals 3-channel; stats measured at decode | Pipeline ships normal X/Y (BC5/EAC-RG11) **plus a shader/loader mode for Z-reconstruct**; stats precomputed into pack metadata |
| Gamma | Shader-side pow(2.2), storage raw | Offline linear-light mips (fixes today's wrong-space mips); storage stays UNORM-encoded |
| Android landing zone | bundled root wiped on update | Dedicated persistent managed dir under `<filesDir>` (or external root), owned by the asset manager |
| Downloads | None; no INTERNET permission | Kotlin downloader in the LoaderActivity flow (restore permission); desktop curl already linked |
| APK size | 560-580 MB incl. 71 MB textures | Goal: recharged textures leave the APK; bundled tier remains only as an optional offline distribution |
| Engine defects §5 | Budget/leak/collision/size-guard | Fixing these is in-scope for the engine milestone — the pack loader must not inherit them |
