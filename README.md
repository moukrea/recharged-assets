# Recharged Assets

Source of truth for the remastered graphical assets of **Jak and Daxter:
Recharged Collection** (the [moukrea/jak-project](https://github.com/moukrea/jak-project)
fork of OpenGOAL), kept separate from the engine/game repository.

This repository holds:

- the high-resolution texture **masters** (albedo, normal, roughness, height)
  under `raw/`, stored in **Git LFS**;
- their **semantic metadata** under `metadata/`;
- the **schemas** for metadata, manifests and the `RPACK` container under
  `schemas/`;
- the **pipeline tools** (resolution variants, semantic mipmaps, GPU
  compression to KTX2, packing, validation) under `tools/`;
- the **CI workflows** that build, validate and publish immutable releases
  under `.github/workflows/`;
- the published **manifests** consumed by Recharged builds under `manifests/`.

Generated artifacts (KTX2 textures, RPACK shards, manifests) are **never
committed**: they are rebuilt by CI and published as GitHub Release assets,
addressed by content hash.

## Layout

```text
raw/jak1/<group>/<material>/     albedo.png, normal.png, roughness.png, height.png
metadata/jak1/                   placements.json, materials.json, per-material overrides
schemas/                         JSON Schemas (metadata, manifest, rpack, assets.lock)
tools/                           pipeline scripts
tests/                           pipeline and parser tests
manifests/                       manifest history / pointers
docs/                            design docs and the implementation plan
```

## Jak 1 masters

Imported from the ESRGAN Edition v1.0.2 rework chain (`pack-complet` export):

- **294** in-game texture placements, deduplicated to
- **172** physical material sets (158 canonical masters + 14 "recale"
  pixel-shifted variants), each with 4 maps at 2048×2048
  (2 sets at 2048×1024);
- `metadata/jak1/placements.json` maps every in-game
  `texture_replacements` path to its material directory;
- `metadata/jak1/materials.json` records canonical/variant relations and
  content hashes.

Import tool: `tools/import_pack_complet.py` (idempotent).

## Status

Bootstrap phase — see `docs/` for the implementation plan (contracts,
compression profiles, mipmap policies, sharding, versioning, asset manager).
