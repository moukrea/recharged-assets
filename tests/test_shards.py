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
                              "village1-vis-tfrag", "village1-vis-tfrag/vil-beach-01")
    # default/albedo is unsplit -> part 0 and the original, suffix-free name,
    # so those shards stay byte-identical across releases
    assert fam == ("jak1", "pc-bc", "default", "albedo", "c1-village1-beach", 0)
    name = shards.shard_name(fam, "a" * 64)
    assert name == "jak1-pc-bc-default-albedo-shard-c1-village1-beach-aaaaaaaaaaaa.rpack"


def test_split_families_get_a_part_suffix():
    fam = shards.shard_family("jak1", "pc-bc", "bonkers", "height",
                              "village1-vis-tfrag", "village1-vis-tfrag/vil-beach-01")
    assert fam[3] == "material"
    assert 0 <= fam[5] < shards.split_count("bonkers", "material")
    assert f"-p{fam[5]}-" in shards.shard_name(fam, "b" * 64)


def test_part_assignment_is_stable_and_spread():
    import json as _json
    plc = _json.loads((REPO / "metadata" / "jak1" / "placements.json").read_text())
    mats = sorted(set(plc["placements"].values()))
    n = shards.split_count("bonkers", "material")
    parts = [shards.part_of(m, n) for m in mats]
    # deterministic
    assert parts == [shards.part_of(m, n) for m in mats]
    # every part actually used, none wildly oversized (hash spread sanity)
    counts = [parts.count(i) for i in range(n)]
    assert min(counts) > 0, counts
    assert max(counts) < 2 * (len(mats) / n), counts


def test_adding_a_material_never_moves_the_others():
    """The insertion-stability property the frozen-cluster rule depends on."""
    n = shards.split_count("bonkers", "material")
    before = {m: shards.part_of(m, n) for m in ("a/b", "c/d", "e/f")}
    _ = shards.part_of("brand/new-texture", n)
    after = {m: shards.part_of(m, n) for m in before}
    assert before == after


def test_determinism():
    a = shards.shard_family("jak1", "android-etc2", "low", "height",
                            "snow-vis-shrub")
    b = shards.shard_family("jak1", "android-etc2", "low", "height",
                            "snow-vis-shrub")
    assert a == b == ("jak1", "android-etc2", "low", "material", "c4-snow-canyon", 0)
