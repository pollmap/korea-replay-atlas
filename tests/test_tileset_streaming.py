import copy
import hashlib
import json

import numpy as np
import pytest

from pipeline.tileset_streaming import (
    encoded, plan_streaming_tileset, verify_streaming_plan, write_streaming_plan,
)


def source_tree(tmp_path, *, depth=4, width=4):
    data = tmp_path / "data"
    old = data / "old"
    old.mkdir(parents=True)
    detail = old / "detail"
    detail.mkdir()
    count = 0
    binaries = {}

    def tile(level):
        nonlocal count
        identity = count
        count += 1
        # Noncommuting rotation, anisotropic scale and translation detect a
        # duplicated transform much more reliably than identity-only fixtures.
        angle = (identity % 9) * .17
        transform = np.array([[np.cos(angle), -np.sin(angle), 0, identity * 13],
                              [np.sin(angle), np.cos(angle), 0, -identity * 3],
                              [0, 0, 1 + level / 10, level * 2], [0, 0, 0, 1]])
        path = detail / f"건물-{identity}.glb"
        path.write_bytes(f"immutable fixture content {identity}".encode())
        binaries[path] = path.read_bytes()
        result = {"boundingVolume": {"box": [0, 0, 0, 1000, 0, 0, 0, 1000, 0, 0, 0, 200]},
                  "geometricError": (depth - level) * 12,
                  "transform": transform.flatten(order="F").tolist(),
                  "content": {"uri": f"detail/{path.name}", "boundingVolume": {"sphere": [0, 0, 0, 10]}},
                  "extras": {"source_node_id": identity, "bytes": path.stat().st_size,
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
        if identity % 3 == 0:
            result["refine"] = "ADD" if identity % 2 else "REPLACE"
        if identity % 5 == 0:
            result["viewerRequestVolume"] = {"sphere": [0, 0, 0, 10000]}
        if level < depth:
            result["children"] = [tile(level + 1) for _ in range(width)]
        return result

    document = {"asset": {"version": "1.1"}, "geometricError": 10000,
                "root": tile(0), "extras": {"source": "immutable-fixture"}}
    source = old / "tileset.json"
    source.write_bytes(encoded(document))
    return data, source, document, binaries


def plan_fixture(tmp_path, **options):
    data, source, document, binaries = source_tree(tmp_path)
    plan = plan_streaming_tileset(source, data / "new", data_root=data,
                                 existing_file_count=17401, **options)
    return plan, document, binaries


@pytest.mark.parametrize("max_nodes", [64, 96, 128])
def test_bounded_json_preserves_every_node_world_frame_content_and_lod(tmp_path, max_nodes):
    plan, original, binaries = plan_fixture(tmp_path, max_nodes=max_nodes)
    before = plan.source.read_bytes()
    assert len(plan.documents) > 1
    assert plan.summary()["largest_document_nodes"] <= max_nodes
    assert plan.summary()["projected_file_count"] <= 18000
    assert plan.summary()["root_nodes"] < plan.source_node_count
    assert not plan.output.exists()
    proof = verify_streaming_plan(plan)
    assert proof["source_nodes_preserved"] == 341
    assert proof["max_world_matrix_element_difference"] == 0
    assert json.loads(before) == original
    assert all(path.read_bytes() == content for path, content in binaries.items())
    result = write_streaming_plan(plan)
    assert result["geometry_files_rewritten"] == 0
    assert plan.source.read_bytes() == before
    assert all(path.read_bytes() == content for path, content in binaries.items())
    assert len(list(plan.output.rglob("*.json"))) == len(plan.documents)
    for name, document in plan.documents.items():
        assert json.loads((plan.output / name).read_bytes()) == document
        if name != "tileset.json":
            assert "transform" not in document["root"]
            assert document["root"]["refine"] in ("ADD", "REPLACE")


def test_external_json_closure_has_no_children_on_proxy_and_valid_hashes(tmp_path):
    plan, _, _ = plan_fixture(tmp_path)
    write_streaming_plan(plan)
    visited = set()

    def visit_document(path):
        assert path not in visited
        visited.add(path)
        document = json.loads(path.read_bytes())
        stack = [document["root"]]
        while stack:
            tile = stack.pop()
            content = tile.get("content")
            if content:
                target = (path.parent / content["uri"]).resolve()
                assert target.is_relative_to(plan.data_root)
                if target.suffix == ".json":
                    assert "children" not in tile
                    assert content["extras"] == {"bytes": target.stat().st_size,
                                                "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
                    visit_document(target)
            stack.extend(tile.get("children", []))

    visit_document(plan.root_path)
    assert len(visited) == len(plan.documents)


def test_content_metadata_multiple_contents_and_schema_uri_survive_relocation(tmp_path):
    data, source, document, _ = source_tree(tmp_path)
    schema = data / "schema.json"
    schema.write_text('{"classes":{}}')
    document.update({"schemaUri": "../schema.json", "groups": [{"class": "fixture"}],
                     "metadata": {"class": "fixture", "properties": {"name": "source"}}})
    leaf = document["root"]["children"][0]["children"][0]
    leaf["contents"] = [leaf.pop("content"), {"url": document["root"]["content"]["uri"], "group": 0}]
    source.write_bytes(encoded(document))
    plan = plan_streaming_tileset(source, data / "candidate" / "nested", data_root=data,
                                  existing_file_count=17401)
    assert verify_streaming_plan(plan)["passed"]
    for name, result in plan.documents.items():
        assert (plan.output / name).parent.joinpath(result["schemaUri"]).resolve() == schema
        assert result["groups"] == document["groups"]
        assert result["metadata"] == document["metadata"]


def test_writer_never_overwrites_or_resumes_existing_directory(tmp_path):
    plan, _, _ = plan_fixture(tmp_path)
    plan.output.mkdir()
    marker = plan.output / "tileset.json"
    marker.write_bytes(b"existing user data")
    with pytest.raises(ValueError, match="already exists"):
        write_streaming_plan(plan)
    assert marker.read_bytes() == b"existing user data"
    assert list(plan.output.iterdir()) == [marker]


def test_file_budget_fails_before_writing_and_includes_old_files(tmp_path):
    data, source, _, _ = source_tree(tmp_path)
    output = data / "new"
    with pytest.raises(ValueError, match="file budget"):
        plan_streaming_tileset(source, output, data_root=data, existing_file_count=17999)
    assert not output.exists()


@pytest.mark.parametrize("change", ["error", "refine", "metadata", "missing", "transform", "unreachable"])
def test_verifier_rejects_semantic_loss_and_unreachable_documents(tmp_path, change):
    plan, _, _ = plan_fixture(tmp_path)
    root = plan.documents["tileset.json"]["root"]
    if change == "error":
        root["geometricError"] += 1
    elif change == "refine":
        root["refine"] = "ADD"
    elif change == "metadata":
        root["extras"]["source_node_id"] = -1
    elif change == "missing":
        root["children"].pop()
    elif change == "transform":
        root["transform"][12] += .01
    else:
        plan.documents["unreachable.json"] = copy.deepcopy(plan.documents["tileset.json"])
    with pytest.raises(ValueError):
        verify_streaming_plan(plan)


def test_changed_source_is_rejected_before_creating_output(tmp_path):
    plan, _, _ = plan_fixture(tmp_path)
    plan.source.write_bytes(plan.source.read_bytes() + b" ")
    with pytest.raises(ValueError, match="Source tileset changed"):
        write_streaming_plan(plan)
    assert not plan.output.exists()


@pytest.mark.parametrize("change", ["proxy-bound", "proxy-error", "header", "path"])
def test_verifier_rejects_changed_reference_culling_headers_and_write_paths(tmp_path, change):
    plan, _, _ = plan_fixture(tmp_path)
    if change == "path":
        plan.documents["../outside.json"] = copy.deepcopy(plan.documents["tileset.json"])
    elif change == "header":
        plan.documents["tileset.json"]["geometricError"] = 0
    else:
        stack = [plan.documents["tileset.json"]["root"]]
        while stack:
            tile = stack.pop()
            if tile.get("content", {}).get("uri", "").endswith(".json"):
                if change == "proxy-bound":
                    tile["boundingVolume"]["box"][0] += 1
                else:
                    tile["geometricError"] += 1
                break
            stack.extend(tile.get("children", []))
    with pytest.raises(ValueError):
        write_streaming_plan(plan)
    assert not plan.output.exists()
    assert not (plan.data_root / "outside.json").exists()


@pytest.mark.parametrize("uri", ["https://example.com/a.glb", "/outside.glb", "../../outside.glb",
                                  "detail/%2e%2e/a.glb", "detail\\a.glb", "detail/a.glb?key=fixture"])
def test_unsafe_content_uri_is_rejected_without_creating_files(tmp_path, uri):
    data, source, document, _ = source_tree(tmp_path, depth=1)
    document["root"]["content"]["uri"] = uri
    source.write_bytes(encoded(document))
    with pytest.raises(ValueError):
        plan_streaming_tileset(source, data / "new", data_root=data, existing_file_count=10)
    assert not (data / "new").exists()


def test_symlink_source_or_content_is_rejected(tmp_path):
    data, source, document, _ = source_tree(tmp_path, depth=1)
    link = data / "linked.glb"
    try:
        link.symlink_to(source.parent / document["root"]["content"]["uri"])
    except OSError:
        pytest.skip("Symlink creation is unavailable in this Windows fixture environment")
    document["root"]["content"]["uri"] = "../linked.glb"
    source.write_bytes(encoded(document))
    with pytest.raises(ValueError, match="Symlink"):
        plan_streaming_tileset(source, data / "new", data_root=data, existing_file_count=10)


@pytest.mark.parametrize("kind", ["implicit", "extension", "external", "wide"])
def test_unsupported_semantics_fail_closed(tmp_path, kind):
    data, source, document, _ = source_tree(tmp_path, depth=1)
    if kind == "implicit":
        document["root"]["implicitTiling"] = {"subdivisionScheme": "QUADTREE"}
    elif kind == "extension":
        document["root"]["extensions"] = {"unknown_transform": {"scale": 3}}
    elif kind == "external":
        child = source.parent / "external.json"
        child.write_bytes(encoded(document))
        document["root"]["content"]["uri"] = child.name
    else:
        document["root"]["children"] = [copy.deepcopy(document["root"]["children"][0]) for _ in range(96)]
    source.write_bytes(encoded(document))
    with pytest.raises(ValueError):
        plan_streaming_tileset(source, data / "new", data_root=data, existing_file_count=10)


def test_identical_plans_are_deterministic_without_writing(tmp_path):
    plan, _, _ = plan_fixture(tmp_path)
    repeated = plan_streaming_tileset(plan.source, plan.output, data_root=plan.data_root,
                                      existing_file_count=plan.existing_file_count)
    assert repeated.documents == plan.documents
    assert repeated.summary() == plan.summary()
    assert not plan.output.exists()


def test_finite_source_matrices_cannot_overflow_composed_world_frame(tmp_path):
    data, source, document, _ = source_tree(tmp_path, depth=1)
    document["root"]["transform"][0] = 1e308
    document["root"]["children"][0]["transform"][0] = 1e308
    source.write_bytes(encoded(document))
    with pytest.raises(ValueError, match="world transform is not finite"):
        plan_streaming_tileset(source, data / "new", data_root=data, existing_file_count=10)
    assert not (data / "new").exists()
