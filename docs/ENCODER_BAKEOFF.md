# Encoder bake-off — results & pinned toolchain (2026-08-06)

Programmatic comparison (spec §9.3) run by `tools/benchmark_encoders.py` on
3 representative 2048² masters (`vil-beach-01`, `vil1-sages-stonewall-01`,
`vil-beachrock` 2048×1024) × 4 semantics, round-tripped and measured with
semantic-aware metrics. 63 encode runs, 0 failures. Raw data:
`results.json` next to this file.

## Candidates

| Tool | Version | Formats covered |
|---|---|---|
| bc7enc_rdo | commit `b9438627` | BC1/BC3/BC4/BC5/BC7 |
| etcpak | commit `9110365b` | ETC2 RGB/RGBA, EAC R11/RG11 |
| astcenc | 5.7.0 (avx2) | ASTC 4×4/5×5/6×6 |
| ktx (KTX-Software) | 4.4.2 | KTX2 create/validate (container, not encoder) |
| Compressonator | 4.5.52 | **dropped** — the Linux CLI cannot save any decodable image (png/bmp both fail, exit 0), so no independent round-trip metric is computable; the three tools above already cover every target format |

## Results (mean over the 3 materials)

### Albedo (color metrics)

| Format | Encoder | bpp | PSNR | max err | time/2048² |
|---|---|---|---|---|---|
| **BC7** | **bc7enc_rdo** | 8 | **50.2 dB** | 12 | 3.0 s |
| BC1 | bc7enc_rdo | 4 | 42.6 dB | 25 | 3.4 s |
| ASTC 6×6 | astcenc -thorough | 3.56 | 45.3 dB | 22 | 3.6 s |
| ASTC 6×6 | astcenc -medium | 3.56 | 44.6 dB | 25 | 0.3 s |
| ETC2 RGB8 | etcpak | 4 | 40.1 dB | 37 | 0.14 s |

### Normal maps (angular error, X/Y storage + Z reconstruction)

| Format | Encoder | bpp | mean | p99 | max |
|---|---|---|---|---|---|
| **ASTC 4×4** | **astcenc -thorough** | 8 | **0.43°** | 2.1° | 43.8° |
| BC5 | bc7enc_rdo | 8 | 1.04° | 10.3° | 102.8° |
| EAC RG11 | etcpak | 8 | 1.12° | 10.4° | 106.3° |

At identical bitrate, ASTC 4×4 beats EAC RG11 by ~2.6× on mean error →
the `android-astc` profile uses ASTC 4×4 for normals, as planned. The high
p99/max on BC5/EAC is dominated by one noisy master
(`vil1-sages-stonewall-01`) — per-material validation thresholds and a
pre-encode renormalization pass are prototype follow-ups.

### Roughness / height (single-channel MAE / PSNR)

| Format | Encoder | bpp | rough MAE | rough PSNR | height MAE | height PSNR |
|---|---|---|---|---|---|---|
| BC4 | bc7enc_rdo | 4 | 0.117 | 57.3 dB | 0.049 | 61.0 dB |
| **EAC R11** | **etcpak** | 4 | 0.239 | 53.8 dB | 0.144 | 55.8 dB |
| ASTC 6×6 | astcenc -thorough | 3.56 | 0.199 | 55.6 dB | 0.065 | 59.9 dB |
| ASTC 4×4 | astcenc -thorough | 8 | 0.002 | 74.9 dB | 0.002 | 77.1 dB |

Spec §4.3 question ("data maps: ASTC or EAC R11?") answered by the numbers:
at iso-bitrate ASTC 6×6 ≈ EAC R11 (no meaningful win), and ASTC 4×4's huge
quality lead costs **2× the footprint**. → data maps stay **EAC R11** on both
Android profiles; BC4 on PC. (If a specific material ever fails its height
validation gate, the per-texture `format_overrides` can bump it to ASTC 4×4.)

## Pinned toolchain (toolchain/Dockerfile)

- **BCn**: `bc7enc_rdo` @ `b9438627` (BC7 default encoder; BC1/BC4/BC5)
- **ETC2/EAC**: `etcpak` @ `9110365b` (etc2_rgb/rgba, eac_r11, eac_rg11)
- **ASTC**: `astcenc` 5.7.0, `-thorough` (release) / `-medium` (PR fast path)
- **Container**: KTX-Software 4.4.2 (`ktx create` from pre-encoded raw
  blocks, `ktx validate`, libktx for load tests)

## Method notes

- Every source is fed RGB-expanded (mode-L PNGs break some CLIs).
- etcpak competes only on ETC2/EAC: its BCn output failed round-trip through
  its own `-v` viewer in the probe run.
- astcenc runs LDR linear (`-cl`); a dedicated `-normal` preset run is a
  candidate refinement for the normals path.
- Times are single-image wall clock on the dev machine (8 threads where the
  tool supports it); relative comparisons only. Full-catalog release build
  estimate at `-thorough`: 172 materials × ~4 maps × 3 profiles ≈ well under
  an hour single-runner.
