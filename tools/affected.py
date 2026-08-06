#!/usr/bin/env python3
"""PR change detection (plan A3): map changed files to affected materials.

Prints JSON: {"materials": [...], "full_sample": bool}
- raw/jak1/<tpage>/<name>/*  or  metadata/jak1/materials/<tpage>/<name>.json
  -> that material
- any change under tools/ or schemas/ -> the representative sample (the
  prototype set), because policy changes affect every output equally.
"""

from __future__ import annotations

import json
import subprocess
import sys

SAMPLE = [
    "village1-vis-tfrag/vil1-sages-stonewall-01",
    "village1-vis-tfrag/vil-beachrock",
    "village1-vis-tfrag/vil-beach-01",
    "beach-vis-tfrag/bch-outpostwall",
]


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    diff = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"],
                          capture_output=True, text=True, check=True).stdout
    mats: set[str] = set()
    full_sample = False
    for line in diff.splitlines():
        parts = line.split("/")
        if line.startswith("raw/jak1/") and len(parts) >= 4:
            mats.add(f"{parts[2]}/{parts[3]}")
        elif line.startswith("metadata/jak1/materials/") and len(parts) >= 5:
            mats.add(f"{parts[3]}/{parts[4].removesuffix('.json')}")
        elif line.startswith(("tools/", "schemas/", "toolchain/")):
            full_sample = True
    if full_sample:
        mats.update(SAMPLE)
    print(json.dumps({"materials": sorted(mats), "full_sample": full_sample}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
