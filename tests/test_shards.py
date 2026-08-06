import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import shards  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def test_every_known_tpage_prefix_is_clustered():
    """Every tpage in the real placements table must resolve to a NON-overflow
    cluster — overflow is for the future, not for known content."""
    placements = json.loads(
        (REPO / "metadata" / "jak1" / "placements.json").read_text())["placements"]
    tpages = {k.split("/")[0] for k in placements}
    for tpage in tpages:
        c = shards.cluster_of("jak1", tpage)
        assert c != "overflow", f"{tpage} fell into overflow"


def test_unknown_prefix_goes_to_overflow():
    assert shards.cluster_of("jak1", "newlevel-vis-tfrag") == "overflow"


def test_prefix_extraction():
    assert shards.level_prefix("beach-vis-tfrag") == "beach"
    assert shards.level_prefix("village1-tpage-2") == "village1"
    assert shards.level_prefix("weird") == "weird"


def test_group_mapping_complete():
    import rpack
    assert set(shards.GROUP_OF_MAP) == set(rpack.MAP_KINDS)
    assert shards.GROUP_OF_MAP["albedo"] == "albedo"
    assert shards.GROUP_OF_MAP["normal"] == "material"


def test_family_and_name():
    fam = shards.shard_family("jak1", "pc-bc", "default", "albedo",
                              "village1-vis-tfrag")
    assert fam == ("jak1", "pc-bc", "default", "albedo", "c1-village1-beach")
    name = shards.shard_name(fam, "a" * 64)
    assert name == "jak1-pc-bc-default-albedo-shard-c1-village1-beach-aaaaaaaaaaaa.rpack"


def test_determinism():
    a = shards.shard_family("jak1", "android-etc2", "low", "height",
                            "snow-vis-shrub")
    b = shards.shard_family("jak1", "android-etc2", "low", "height",
                            "snow-vis-shrub")
    assert a == b == ("jak1", "android-etc2", "low", "material", "c4-snow-canyon")
