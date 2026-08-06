"""Validate the schemas themselves, the fixtures, and the real metadata."""
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parent.parent
SCHEMAS = REPO / "schemas"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(p: Path):
    return json.loads(p.read_text())


@pytest.mark.parametrize("name", [
    "material-metadata.schema.json",
    "manifest.schema.json",
    "assets-lock.schema.json",
    "rpack-index.schema.json",
])
def test_schema_is_valid_draft2020(name):
    schema = load(SCHEMAS / name)
    jsonschema.Draft202012Validator.check_schema(schema)


def test_manifest_fixture_validates():
    jsonschema.validate(load(FIXTURES / "manifest.example.json"),
                        load(SCHEMAS / "manifest.schema.json"))


def test_lock_fixture_validates():
    jsonschema.validate(load(FIXTURES / "assets.lock.example.json"),
                        load(SCHEMAS / "assets-lock.schema.json"))


def test_material_fixture_validates():
    jsonschema.validate(load(FIXTURES / "material.example.json"),
                        load(SCHEMAS / "material-metadata.schema.json"))


def test_material_fixture_rejects_bad_alpha():
    doc = load(FIXTURES / "material.example.json")
    doc["alpha_mode"] = "weird"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, load(SCHEMAS / "material-metadata.schema.json"))


def test_cluster_table_shape():
    table = load(SCHEMAS / "shard-clusters-v1.json")
    jak1 = table["games"]["jak1"]
    seen = set()
    for prefixes in jak1["clusters"].values():
        for p in prefixes:
            assert p not in seen, f"duplicate prefix {p}"
            seen.add(p)
    assert jak1["overflow"] == "overflow"


def test_all_committed_material_metadata_validates():
    schema = load(SCHEMAS / "material-metadata.schema.json")
    files = sorted((REPO / "metadata" / "jak1" / "materials").rglob("*.json"))
    assert len(files) == 172
    validator = jsonschema.Draft202012Validator(schema)
    for f in files:
        doc = load(f)
        validator.validate(doc)
        # the id must match the file's location
        tpage, name = f.parent.name, f.stem
        assert doc["id"] == f"jak1/{tpage}/{name}"


def test_real_placements_and_materials_parse():
    placements = load(REPO / "metadata" / "jak1" / "placements.json")
    materials = load(REPO / "metadata" / "jak1" / "materials.json")
    assert len(placements["placements"]) == 294
    assert len(materials) == 172
    # every placement points at a known material dir
    for key, mat in placements["placements"].items():
        assert (REPO / "raw" / "jak1" / mat).is_dir(), f"{key} -> {mat} missing"
