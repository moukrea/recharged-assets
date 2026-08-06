# Roadmap — remaining work, in execution order

Status: steps 1-4 of [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) are
done (contracts, pinned toolchain + bake-off, vertical prototype, per-material
metadata). This document plans everything that remains, at file level where
the target code is already known from [AUDIT.md](AUDIT.md).

Ground rules carried over: engine work happens on fresh branches off
`origin/autoport/android-port` (another agent pushes there daily — rebase
before merge, never commit directly); the fork's release CI stays untouched;
the only game↔assets contract is `assets.lock.json` + the manifest schema;
the raw ESRGAN pack is never imported.

---

## Phase A — Step 5: full-catalog pipeline, incremental CI, `assets-v0.1.0`
*(this repo only; no engine dependency — can ship first)*

### A1. `tools/build_catalog.py`
Generalizes `build_prototype.py` to the whole catalog:
- Iterate `metadata/jak1/materials/**/*.json` (172) instead of a hardcoded
  list; honor `format_overrides` and `mip_policy` per material.
- **Preset variants**: for each preset (`low`, `default`, `bonkers`),
  compute level-0 dims via `presets.preset_dims(master_dims, esrgan_dims)`;
  produce the variant by *starting the semantic mip chain from the master*
  and taking the sub-chain from the matching level (levels align because
  the cap is power-of-two — this is the plan's shard-stability property).
  Non-square ESRGAN references (e.g. 512×498) round conservatively down.
- **Stats per preset**: `normal_dc`/`height_*` computed on the *preset's*
  level-0 (the engine consumes what it samples, not the master).
- **Content-addressed cache**: cache key =
  `sha256(master bytes) × tool-versions × policy-hash(metadata+preset rules)`;
  layout `cache/<key>/<map>.<profile>.<preset>.ktx2`. A cache hit skips
  mips+encode entirely. This is also what makes CI incremental.
- Output tree: `out/catalog/<profile>/<preset>/…` + shard assembly via
  `shards.py` + `rpack.py`.
- `--materials a,b,c` filter (used by the PR pipeline), `--profiles`,
  `--presets` filters.

### A2. `tools/validate_catalog.py` + `tools/validation-thresholds.json`
The spec §10 gates, executed on every built KTX2 (decode via the same
encoder tools' decoders used in the bake-off):
- Structural: dims per preset rule, mip count = `level_count()`, vkFormat
  per profile table, `ktx validate` pass, deterministic hash (double-build
  in CI), size budget per entry.
- Quality per semantic (thresholds keyed `<profile>.<semantic>`, initial
  values derived from the bake-off measurements +20% margin):
  - albedo: PSNR ≥ 45 dB (pc-bc) / 37 dB (etc2) / 42 dB (astc), max err
  - normal: mean ≤ 1.5°, p99 ≤ 12°, hard-fail max unbounded → warn-list
  - rough/height: MAE ≤ 0.5, gradient preservation (Sobel correlation)
  - tileable (wrap≠clamp): edge-continuity check across the wrap after
    mip 2 + encode (max seam delta)
  - alpha coverage: n/a in v1 (no alpha masters) but the gate stub exists
- **Failure ladder** (spec §10): retry with `astc_4x4` (android profiles) /
  keep-uncompressed entry (pc) via per-material `format_overrides`
  auto-proposal written to a report — a human commits the override; CI
  fails loudly, never silently downgrades.
- Pre-encode source sanity (the class the fork's own bundle failed):
  reject JPEG-in-.png, palette PNGs, denormalized normal maps (renorm pass
  + warn), 16-bit inputs.

### A3. `tools/affected.py` — PR change detection
- `git diff --name-only origin/main...HEAD` → touched `raw/**` /
  `metadata/**` / `schemas|tools/**`.
- raw/metadata change → affected material ids; tools/schema change →
  representative sample (the 7 prototype materials) + full unit tests.
- Emits the `--materials` list for A1. PR builds one profile per platform
  family (pc-bc + android-etc2) at `default` preset only, `-medium` ASTC.

### A4. Workflows
1. `toolchain.yml` — on `toolchain/**` change: build + push
   `ghcr.io/moukrea/recharged-assets-toolchain:<n>` (tag = counter, never
   `latest` in consumers), verify tool versions inside.
2. `pr.yml` (extend existing) — add the incremental encode job: runs in the
   toolchain image, LFS-pulls **only** affected masters
   (`git lfs pull --include=<paths>`), A1 on affected, A2, determinism
   double-build, artifact = validation report.
3. `release.yml` — on tag `assets-v*`:
   - guard: tag on main, tests green, changelog entry exists;
   - full A1 with cache (Actions cache keyed on an LFS-OID lockfile,
     `git lfs ls-files -l | sha256` — protects the 10 GiB/month LFS
     bandwidth quota);
   - **shard reuse**: download the *previous* release manifest, skip
     uploading any shard whose sha256 it already references (manifest URL
     keeps pointing at the old immutable release asset);
   - manifest generation (schema-validated) + SHA-256SUMS;
   - provenance: `actions/attest-build-provenance` on manifest + shards;
   - `gh release create assets-vX.Y.Z` + uploads; repo setting **immutable
     releases enabled before the first release** (one-time, verify via API);
   - post-publish job: download every asset fresh, re-verify hashes, smoke
     `rpack.py --verify`, then mark the release non-draft.
4. Shard-size check: assert every shard is 25-500 MB; if a cluster lands
   outside 50-250 MiB target on real data, this is the ONE allowed re-cut
   of `shard-clusters-v1.json` before `assets-v1.0.0`.

### A5. Catalog estimates & release content
Extrapolating the prototype (7 materials ≈ 109/91/89 MB bonkers):
~172 materials → **~2.6 GB per profile at bonkers**, less at default/low.
v0.1.0 ships: 3 profiles × 3 presets, jak1, ~6-9 GB total across ~60-90
shards — all within GitHub release limits (1000 assets, 2 GiB each).
`pc-bc-legacy` (macOS) is **deferred** until the fork actually targets
macOS; the profile enum + manifest support it already.

Deliverable: **`assets-v0.1.0` immutable release + manifest** consumable by
M2, plus a committed `manifests/assets-v0.1.0.json` pointer copy.

---

## Phase B — M1: KTX2/RPACK loader + managed source tier (jak-project)

Branch: `feat/recharged-managed-assets` off `origin/autoport/android-port`.
Sliced into 4 reviewable PRs (B1-B2 / B3-B4 / B5 / B6-B7), each x86-tested
and device-smoke-tested, rebased daily.

### B1. Readers (`common/util/Ktx2Reader.{h,cpp}`, `common/util/RPack.{h,cpp}`)
- **KTX2-subset reader**, not full libktx: our files are plain block-
  compressed KTX2 (no supercompression, no BasisU) — header + level index +
  vkFormat→GL internal-format table is ~250 lines and keeps the fragile
  Android build free of a large vendored dep. The subset contract is
  guaranteed by our own pipeline (`ktx validate` gate in A2). Divergence
  from spec §3's "libktx" noted and justified; the acceptance criterion
  ("PC and Android share one KTX2 loader") holds.
- vkFormat table: BC7/BC5/BC4/BC1, ETC2 RGB8, EAC R11/RG11, ASTC 4x4/6x6,
  RGBA8 fallback → `{GLenum internal, bool compressed, block size/bytes}`.
- RPack reader mirrors `tools/rpack.py`: trailer → JSON index (use the
  vendored `third-party/json`) → entry seeks. Golden-file unit tests in
  `test/` using a fixture pack committed tiny (few KB, generated by the
  Python writer — cross-language conformance test).

### B2. Capability detection (`game/graphics/opengl_renderer/GpuCaps.{h,cpp}`)
- PC: GL ≥ 4.2 core ⇒ BPTC+RGTC; else extension strings
  (`GL_ARB_texture_compression_bptc`, `GL_EXT_texture_compression_rgtc`,
  `GL_EXT_texture_compression_s3tc`) — covers the macOS 4.1 case.
- Android: ETC2/EAC = GLES 3.0 core; ASTC via
  `GL_KHR_texture_compression_astc_ldr`.
- Exposes `preferred_profile()` per spec §15 order; logged once at init.

### B3. Managed source tier (`CustomTextureReplacements`)
- New index: `managed_index` built from the installed state file
  (`managed_assets/<game>/state.json`: manifest + local shard paths).
  Maps `(tpage/name, map kind)` → `(shard path, offset, size, entry meta)`.
- Precedence: user > **managed** > bundled > stock (owner decision #2).
  New gate `g_global_settings.recharged_managed_assets` (default ON,
  composed with the master like every gate; GOAL mirror + menu row).
- `invalidate()` finally gets call sites: settings toggle handler + pack
  install/uninstall + the existing gate-transition path; the scan gets a
  `std::mutex` (fixes audit defects 1-2).

### B4. Compressed upload in `add_texture` (`LoaderStages.cpp`)
- Managed hit → read entry payload (stdio pread; mmap later if profiled),
  parse KTX2, `glTexStorage2D(levels, internal, w, h)` +
  `glCompressedTexSubImage2D` per level (or `glTexSubImage2D` for the
  RGBA8 fallback profile). **No `glGenerateMipmap`**; aniso set as today;
  min filter left to renderers (unchanged contract); wrap applied from
  entry metadata (managed textures only — stock behaviour untouched).
- PBR maps: suffix probe consults the managed index first (same-source
  pairing extended: managed base pairs with managed maps); stats
  (`normal_dc_*`, `height_*`) read from the entry **instead of the CPU
  measurement passes** — the two full-texel scans and the lambda analysis
  are skipped entirely for managed entries (kills the audited load hitch).
- **Normal X/Y mode**: managed normals are 2-channel. New `u_pbr_mode`
  bit 128 `PBR_NORMAL_RG` → `pbr_fused.glsl` normal decode reconstructs
  `z = sqrt(max(0, 1 - x² - y²))` before the surface-gradient path. Must
  compile under GLSL 4.10 core AND GLES 3.20 (no new syntax — safe);
  remember the preprocess.py chunk-glob **cmake reconfigure** caveat.
- Defect fixes riding along (audit §5 / plan D12):
  - budget: `bytes_this_run += actual uploaded bytes` (entry size or
    decoded PNG size), same for `Loader.cpp:650`; VRAM readout gets real
    dims (display-only field, no pool-buffer change — the OOB constraint
    on `src_data` stays respected);
  - `GL_MAX_TEXTURE_SIZE` guard: if level 0 exceeds the limit, **skip
    leading mips** (offline chains make this free) instead of failing;
    log once per texture;
  - PBR registry keyed `tpage/name`; level unload walks the level's
    texture list and releases its PBR map GL ids (fixes the leak);
    `register_pbr_material` collision path becomes impossible by key.

### B5. Lock + installed state (`common/util/AssetsLock.{h,cpp}`)
- Parse `assets.lock.json` (from `<data>/assets.lock.json`, packaged with
  builds; absent = feature dormant). Validate `schema_version`,
  `min_loader_version` vs `RPACK_LOADER_VERSION` constant.
- `managed_assets` state file: current manifest json + per-shard
  `{sha256, size, verified}` — read-only in M1 (installs are manual/CLI
  until M2).

### B6. Stat-mirror equality test
- Dev command (`-cmd pbr_stats_check` or debug menu): for every material
  present in BOTH the bundled PNG set and a managed pack, run the CPU
  measurement on the PNG and diff against pack metadata. Gate: |Δdc| ≤ 1e-3,
  |Δmean| ≤ 1e-3, |Δnorm| ≤ 1%, λ within 10% (analysis approximation).
  Divergence = pipeline bug (`tools/stats.py` drifted from the engine).

### B7. M1 acceptance (before any M2 work)
- x86: village1 with a locally-built managed pack ≡ bundled PNGs
  side-by-side (same POM/tess behaviour — stats equal ⇒ same amplitude law);
  VRAM for the 7 materials ↓ ~5×; no loader hitch (frame-time trace).
- Device (arm64): same scene, etc2 + astc profiles, tess + POM verified,
  no GL errors (KHR_debug clean).
- Pack absent / setting OFF / corrupt shard (bit-flip test) → stock
  fallback, no crash, logged reason.

---

## Phase C — M2: asset manager core (shared C++, PC transport)

`game/assets_manager/` (new dir, compiled on both platforms):
- **C1** `ManifestClient`: fetch manifest by URL (PC: libcurl easy, 10 s
  timeout, ≤3 retries), verify `manifest_sha256` from the lock, parse +
  schema-check (required fields only, tolerant of additive fields).
- **C2** `Resolver`: GpuCaps profile × user preset (new persisted setting
  `recharged-texture-pack` Off/low/default/bonkers) × `requires_features`
  gating (material shards need `pbr` ⇒ skipped on non-PBR builds) → target
  shard set; diff vs installed state by sha256.
- **C3** `Installer`: staging dir `managed_assets/<game>/staging/`;
  download (resumable: HTTP Range from existing partial, fsync), verify
  size+sha256, then **atomic promote**: write `state.json.new`, fsync,
  rename over `state.json`; previous shards kept until the new state is
  verified once by a successful boot (two-phase); orphan GC afterwards;
  rollback = delete `state.json.new` + keep old.
- **C4** Offline rules (spec §15): no network → last verified install;
  none → stock textures; never block launch for an optional update;
  `required:true` + nothing installed = the ONLY warning-screen case.
- **C5** Disk-space check (`std::filesystem::space`) before download with
  the manifest's total size; abort cleanly under threshold.
- **C6** Surface: `-assets install|verify|status` CLI verbs first (dev
  usable, CI-testable headless), ImGui debug panel second, GOAL menu last
  (phase F).
- Unit tests: resolver diffs, state-machine transitions (install, resume,
  corrupt, rollback), fixture manifests.

## Phase D — M3: Android

- **D1** Manifest change: drop the
  `<uses-permission android:name="android.permission.INTERNET" tools:node="remove"/>`
  line, add INTERNET (normal permission, no runtime prompt).
- **D2** `AssetPackDownloader.kt` in the LoaderActivity flow, after the
  cgo/custom unpack stages: reads the lock (packaged in APK assets),
  fetches manifest (HttpsURLConnection — no new deps), computes the needed
  shard set (profile decided by a **native query** exposed via NativeGk —
  GLES caps need a context: use the same detection `libgk` uses, cached in
  SharedPreferences after first boot; first-ever install defaults to
  android-etc2, upgraded to astc after first boot detection),
  size-preview dialog before the first big download (spec §15), Wi-Fi-only
  preference, resumable (Range + `.part` files), progress in the existing
  loader UI.
- **D3** Landing zone: `<filesDir>/managed_assets/<game>/` — NOT
  `custom/<game>` (wiped on APK version change) and NOT external storage
  (survives "clear cache", dies with "clear data" — acceptable, spec §15
  handles app-data wipes by re-download). Kotlin writes staging +
  `state.json.pending`; the shared C++ installer (phase C) verifies and
  promotes at boot — one verification codepath for both platforms.
- **D4** APK slimming: `build_custom_pack.sh` drops `recharged_textures/`
  from the custom pack once B7 parity is signed off (−71 MB raw, the APK's
  custom zip shrinks accordingly); `assets.lock.json` added to APK assets.
  The user drop dir keeps working unchanged.
- **D5** Device matrix: Adreno 618 (the port's reference), one Mali if
  available; explicit test of the ASTC-ext detection path on both.

## Phase E — M4: offline / preloaded distributions

- **E1** assets repo: `offline-bundle.yml` (workflow_dispatch: manifest
  tag, game, profile, preset) → one zip of shards + manifest + a
  ready-made `state.json` → release asset on the same immutable release.
- **E2** PC: `package_release.sh --with-assets <bundle.zip>` stages it as
  a preinstalled `managed_assets/` (state pre-verified flag off — first
  boot verifies). Also fix the audited PC packaging gap (recharged HUD
  etc. are packaged; the *bundled textures* PC gap becomes moot once the
  bundled tier retires).
- **E3** Android: an `offlineAssets` Gradle property staging the bundle
  zip into APK assets, extracted by the existing sentinel flow into
  `managed_assets/` (reuses `.custom_pack_stamp` mechanics with its own
  stamp).

## Phase F — Step 8 + M5: presets & settings surface

- **F1** RECHARGED SETTINGS rows: `TEXTURE PACK` carousel (Off/Low/
  Default/Bonkers — Off = very-low = stock), `PACK STATUS` line (version,
  size, update available), `CHECK FOR UPDATES` / `RE-VERIFY` actions.
  Preset change triggers resolver diff + size preview. GOAL↔C++ via the
  existing `pc-set-*` pattern; persisted in `settings.ini` (and do persist
  it, unlike the audited `follow-probe` bug).
- **F2** Spec §7 separation: `gfx-anisotropy` finally wired to a real
  user setting (with the GLES ext guard the audit flagged), trilinear
  toggle honored per renderer, LOD-bias deliberately NOT exposed (spec
  §16: not a substitute for resolution variants).
- **F3** jak2/jak3 readiness: nothing hardcodes jak1 outside data — the
  cluster table and metadata are per-game; adding a game = new masters +
  new cluster table entry + manifest `games` entry.

## Phase G — hygiene & follow-ups (parallel, low priority)

- astcenc `-normal` preset evaluation vs current LDR path (bake-off rerun).
- Toksvig/normal-variance roughness mips A/B once specular is watchable
  in-engine (interacts with the shader's runtime mip-variance widening).
- `dump_keys` fixes upstreamed while touching the file (gate + cached
  `fs::exists` + doc-matching marker path).
- Basis Universal "universal profile" evaluation (spec §3) — after v1.
- LFS usage monitoring (`gh api /repos/.../lfs` not available — watch the
  billing usage API) + Actions-cache hit-rate check after 3 releases.

## Sequencing & dependencies

```
A (pipeline+CI+v0.1.0)  ──────────►  C needs a published manifest
B (M1 loader)  ───────────────────►  B7 gate: parity signed off
      A ∥ B are independent — run in parallel
C (M2 core+PC)  needs A5 + B7
D (M3 Android)  needs C (shared installer) ; D4 needs B7 parity
E (M4 offline)  needs A + C
F (presets UI)  needs C ; F2 anytime after B
G               anytime
```

Suggested order of attack: **A and B in parallel → C → D → E/F → G.**
Each engine phase = its own branch off a fresh
`origin/autoport/android-port`, small PR series, x86 + device evidence
attached.

## Risks

| Risk | Mitigation |
|---|---|
| `autoport/android-port` moves daily under us | small PRs, rebase before each; loader code is additive (new files + one seam in add_texture) |
| GLES driver quirks on compressed uploads (Adreno) | B7 device gate before any M2 work; KHR_debug clean run required |
| Shader blob reconfigure trap (new .glsl chunk) | B4 keeps changes inside existing chunks; if a new chunk is added, document `cmake` reconfigure in the PR |
| LFS bandwidth (2.3 GiB pulls) | Actions cache keyed on LFS-OID lockfile (A4); PR pulls only affected |
| Release asset bloat over versions | shard reuse across immutable releases is measured in the release job (report: % reused) |
| Stats mirror drift vs engine | B6 equality gate runs in the fork's CI on the fixture pack |
| First-boot Android without GLES caps known | default etc2 (universally safe), upgrade to astc after first boot |
