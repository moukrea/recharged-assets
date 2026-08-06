"""Deterministic shard assignment (schema v1).

A shard family is (game, profile, preset, group, cluster). Cluster comes
from the frozen table schemas/shard-clusters-v1.json by level-prefix lookup;
unknown prefixes land in the overflow cluster and never rebalance existing
shards. Shard names embed the content hash (filled after packing).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"

# Map kind -> shard group. Albedo-only installs must work without PBR.
GROUP_OF_MAP = {
    "albedo": "albedo",
    "mask": "albedo",
    "normal": "material",
    "roughness": "material",
    "height": "material",
    "metallic": "material",
    "ao": "material",
    "specular": "material",
    "emissive": "material",
}


@lru_cache(maxsize=None)
def _cluster_table(game: str) -> tuple[dict[str, str], str]:
    data = json.loads((SCHEMAS_DIR / "shard-clusters-v1.json").read_text())
    game_cfg = data["games"][game]
    prefix_to_cluster = {}
    for cluster, prefixes in game_cfg["clusters"].items():
        for p in prefixes:
            if p in prefix_to_cluster:
                raise ValueError(f"prefix {p} appears in two clusters")
            prefix_to_cluster[p] = cluster
    return prefix_to_cluster, game_cfg["overflow"]


def level_prefix(tpage_name: str) -> str:
    """The level part of a tpage name: before '-vis-' or '-tpage-'."""
    for sep in ("-vis-", "-tpage-"):
        if sep in tpage_name:
            return tpage_name.split(sep, 1)[0]
    return tpage_name


def cluster_of(game: str, tpage_name: str) -> str:
    table, overflow = _cluster_table(game)
    return table.get(level_prefix(tpage_name), overflow)


def shard_family(game: str, profile: str, preset: str, map_kind: str,
                 tpage_name: str) -> tuple[str, str, str, str, str]:
    """The full family tuple a (texture, map) belongs to."""
    return (game, profile, preset, GROUP_OF_MAP[map_kind], cluster_of(game, tpage_name))


def shard_name(family: tuple[str, str, str, str, str], sha256: str) -> str:
    game, profile, preset, group, cluster = family
    return f"{game}-{profile}-{preset}-{group}-shard-{cluster}-{sha256[:12]}.rpack"
