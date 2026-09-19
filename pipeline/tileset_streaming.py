"""Split an explicit inline tileset into bounded, immutable external JSONs.

Geometry files, catalog pointers and source manifests are never rewritten.
An external-content tile has no children. Its transform is retained on the
reference tile and removed from the external root, so it is applied only once.
The external root explicitly declares the original inherited refinement.
Reference-only nodes add a runtime tree level: Cesium skip-LOD request order
still needs browser validation even when the logical source tree is identical.

Primary contracts:
https://github.com/CesiumGS/3d-tiles/blob/main/specification/README.adoc#external-tilesets
https://cesium.com/learn/cesiumjs/ref-doc/Cesium3DTile.html#parent

This deliberately handles explicit, inline trees with ordinary local content.
Implicit tiling, pre-existing external tilesets and extension-specific URI or
transform semantics require their own implementation and are rejected.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any


VERSION = "external-json-streaming-1"
DEFAULT_MAX_NODES = 96
MAX_JSON_BYTES = 24 * 1024 * 1024
IDENTITY = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.)
EXTERNAL_HEADER_KEYS = {"asset", "properties", "schema", "schemaUri", "groups", "metadata"}


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON property")
        value[key] = item
    return value


def _read_json(path: Path) -> dict:
    def invalid_constant(_):
        raise ValueError("Non-finite JSON number")
    value = json.loads(path.read_bytes(), object_pairs_hook=_unique_pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("Tileset JSON must be an object")
    return value


def _confined(path: Path, data_root: Path, *, file: bool = False) -> Path:
    """Check lexical and resolved confinement; reject symlinks and Windows junctions."""
    path = Path(os.path.abspath(path))
    base = Path(os.path.abspath(data_root))
    if not path.is_relative_to(base):
        raise ValueError("Path escapes data root")
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Symlink or junction is not an immutable data path")
    resolved = path.resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise ValueError("Resolved path escapes data root")
    if file and not resolved.is_file():
        raise ValueError("Missing regular data file")
    return resolved


def _uri_path(uri: Any, context: Path, data_root: Path, *, file: bool = True) -> Path:
    # Relative ../ references are normal in this dataset; confinement, not a
    # substring blacklist, determines whether their resolved target is safe.
    if not isinstance(uri, str) or not uri or uri.startswith("/") or any(c in uri for c in ("\\", ":", "?", "#", "%", "\0")):
        raise ValueError("Unsafe content URI")
    return _confined(context.parent / uri, data_root, file=file)


def _relative(path: Path, context: Path) -> str:
    return os.path.relpath(path, context.parent).replace("\\", "/")


def _output_path(output: Path, name: str, data_root: Path) -> Path:
    if not isinstance(name, str) or not name or name.startswith("/") or any(c in name for c in ("\\", ":", "?", "#", "%")) or any(part in ("", ".", "..") for part in name.split("/")):
        raise ValueError("Unsafe output JSON path")
    result = _confined(output / name, data_root)
    if not result.is_relative_to(output):
        raise ValueError("Output JSON escapes the independent directory")
    return result


def _contents(tile: dict) -> list[dict]:
    if "content" in tile and "contents" in tile:
        raise ValueError("A tile cannot contain both content and contents")
    value = [tile["content"]] if "content" in tile else tile.get("contents", [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("Invalid tile content")
    return value


def _uri_key(content: dict) -> str:
    keys = [key for key in ("uri", "url") if key in content]
    if len(keys) != 1:
        raise ValueError("Content needs one unambiguous URI")
    return keys[0]


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _transform(tile: dict) -> tuple:
    value = tile.get("transform", IDENTITY)
    if not isinstance(value, (list, tuple)) or len(value) != 16 or not all(_finite_number(v) for v in value):
        raise ValueError("Invalid tile transform")
    if any(value[i] != expected for i, expected in ((3, 0), (7, 0), (11, 0), (15, 1))):
        raise ValueError("Tile transform must be affine")
    return tuple(value)


def _multiply(left: tuple, right: tuple) -> tuple:
    # Column-major matrices, as specified by 3D Tiles.
    result = tuple(sum(left[k * 4 + row] * right[column * 4 + k] for k in range(4))
                   for column in range(4) for row in range(4))
    if not all(math.isfinite(value) for value in result):
        raise ValueError("Composed world transform is not finite")
    return result


def _node_count(root: dict) -> int:
    stack, count = [root], 0
    while stack:
        tile = stack.pop()
        count += 1
        stack.extend(tile.get("children", []))
    return count


def _reject_extensions(value: Any) -> None:
    if isinstance(value, dict):
        if value.get("extensions") or value.get("extensionsUsed") or value.get("extensionsRequired") or "implicitTiling" in value:
            raise ValueError("Extension and implicit tiling semantics are not supported")
        for key, child in value.items():
            if key != "extras":
                _reject_extensions(child)
    elif isinstance(value, list):
        for child in value:
            _reject_extensions(child)


def _validate_source(document: dict, source: Path, data_root: Path, max_nodes: int) -> int:
    if document.get("asset", {}).get("version") not in ("1.0", "1.1") or not isinstance(document.get("root"), dict):
        raise ValueError("Expected an explicit 3D Tiles 1.0 or 1.1 tileset")
    if not _finite_number(document.get("geometricError")) or document["geometricError"] < 0:
        raise ValueError("Invalid tileset geometric error")
    _reject_extensions(document)
    if "schemaUri" in document:
        _uri_path(document["schemaUri"], source, data_root)
    if document["root"].get("refine") not in ("ADD", "REPLACE"):
        raise ValueError("The source root must define refinement")
    stack, count = [document["root"]], 0
    while stack:
        tile = stack.pop()
        count += 1
        if not isinstance(tile, dict) or not isinstance(tile.get("boundingVolume"), dict):
            raise ValueError("Invalid tile bounding volume")
        error = tile.get("geometricError")
        if not _finite_number(error) or error < 0:
            raise ValueError("Invalid tile geometric error")
        _transform(tile)
        if "refine" in tile and tile["refine"] not in ("ADD", "REPLACE"):
            raise ValueError("Invalid tile refinement")
        children = tile.get("children", [])
        if not isinstance(children, list) or any(not isinstance(child, dict) for child in children):
            raise ValueError("Invalid tile children")
        # No artificial grouping or alteration of the original LOD tree.
        if len(children) >= max_nodes:
            raise ValueError("Direct child count cannot fit the node budget without changing the LOD tree")
        for content in _contents(tile):
            target = _uri_path(content[_uri_key(content)], source, data_root)
            if target.suffix.lower() == ".json":
                raise ValueError("Pre-existing external tilesets are not supported")
        stack.extend(children)
    return count


@dataclass
class StreamingPlan:
    source: Path
    output: Path
    data_root: Path
    source_sha256: str
    max_nodes: int
    existing_file_count: int
    target_file_count: int
    documents: dict[str, dict]
    source_node_count: int

    @property
    def root_path(self) -> Path:
        return self.output / "tileset.json"

    def summary(self) -> dict:
        payloads = {name: encoded(document) for name, document in self.documents.items()}
        return {"version": VERSION, "source_sha256": self.source_sha256,
                "source_node_count": self.source_node_count, "max_nodes": self.max_nodes,
                "root_nodes": _node_count(self.documents["tileset.json"]["root"]),
                "largest_document_nodes": max(_node_count(doc["root"]) for doc in self.documents.values()),
                "new_json_files": len(payloads), "projected_file_count": self.existing_file_count + len(payloads),
                "target_file_count": self.target_file_count,
                "root_bytes": len(payloads["tileset.json"]), "json_bytes": sum(map(len, payloads.values())),
                "root_sha256": _sha(payloads["tileset.json"]), "geometry_files_rewritten": 0}


def plan_streaming_tileset(source: Path, output: Path, *, data_root: Path,
                           existing_file_count: int, max_nodes: int = DEFAULT_MAX_NODES,
                           target_file_count: int = 18000) -> StreamingPlan:
    """Plan only; the returned documents contain no absolute filesystem paths.

    The conservative file check retains every existing file, including the old
    root. A caller must supply the actual prospective deployment file count.
    No geometry bytes are read or hashed here; the deployment audit still
    validates every referenced immutable asset before publication.
    """
    if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or not 64 <= max_nodes <= 128:
        raise ValueError("Node budget must be an integer between 64 and 128")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (existing_file_count, target_file_count)) or target_file_count > 20000:
        raise ValueError("Invalid free static file budget")
    data_root = _confined(data_root, data_root)
    source = _confined(source, data_root, file=True)
    output = _confined(output, data_root)
    if output == data_root or output.exists() or source.is_relative_to(output):
        raise ValueError("Output must be a new independent directory")
    payload = source.read_bytes()
    document = _read_json(source)
    count = _validate_source(document, source, data_root, max_nodes)
    documents: dict[str, dict] = {}
    serial = 0

    def move_tile(tile: dict, destination: Path) -> dict:
        result = copy.deepcopy({key: value for key, value in tile.items() if key != "children"})
        for content in _contents(result):
            key = _uri_key(content)
            content[key] = _relative(_uri_path(content[key], source, data_root), destination)
        return result

    def emit(root: dict, name: str, inherited_refine: str, *, external: bool) -> dict:
        nonlocal serial
        destination = output / name
        header = copy.deepcopy({key: value for key, value in document.items()
                                if key != "root" and (not external or key in EXTERNAL_HEADER_KEYS)})
        if "schemaUri" in header:
            header["schemaUri"] = _relative(_uri_path(header["schemaUri"], source, data_root), destination)
        copied = move_tile(root, destination)
        if external:
            copied.pop("transform", None)
            copied["refine"] = root.get("refine", inherited_refine)
            header["geometricError"] = root["geometricError"]
        header["root"] = copied
        documents[name] = header
        pending = [(root, copied, root.get("refine", inherited_refine))]
        used = 1
        while pending:
            original, current, refine = pending.pop()
            children = original.get("children", [])
            if not children:
                # Preserve a pre-existing empty children property on real nodes.
                if "children" in original:
                    current["children"] = []
                continue
            if used + len(children) <= max_nodes:
                copies = [move_tile(child, destination) for child in children]
                current["children"] = copies
                used += len(children)
                # Reserve every sibling's slot, then fill complete subtrees in
                # source order. Breadth-first expansion leaves many tiny leaf
                # JSONs at its frontier and wastes the static file allowance.
                pending.extend(reversed([(child, item, child.get("refine", refine)) for child, item in zip(children, copies)]))
                continue
            serial += 1
            child_name = f"subtrees/subtree-{serial:06d}.json"
            external_doc = emit(original, child_name, refine, external=True)
            content_bytes = encoded(external_doc)
            # Keep spatial/request culling on the proxy. Renderable content and
            # source metadata belong solely to the original node in the JSON.
            proxy = {key: copy.deepcopy(original[key]) for key in
                     ("boundingVolume", "geometricError", "transform", "viewerRequestVolume") if key in original}
            proxy["refine"] = refine
            proxy["content"] = {"uri": _relative(output / child_name, destination),
                                "extras": {"bytes": len(content_bytes), "sha256": _sha(content_bytes)}}
            current.clear()
            current.update(proxy)
        return header

    emit(document["root"], "tileset.json", document["root"]["refine"], external=False)
    plan = StreamingPlan(source, output, data_root, _sha(payload), max_nodes,
                         existing_file_count, target_file_count, documents, count)
    verify_streaming_plan(plan)
    return plan


def verify_streaming_plan(plan: StreamingPlan) -> dict:
    """Independently collapse reference-only nodes and compare every source node.

    Child order, content targets, metadata, bounding/request volumes, effective
    refinement, raw geometric error and composed world transforms must match.
    This is structural/source equivalence, not an FPS or ground-truth audit.
    """
    if _sha(plan.source.read_bytes()) != plan.source_sha256:
        raise ValueError("Source tileset changed after planning")
    if plan.existing_file_count + len(plan.documents) > plan.target_file_count:
        raise ValueError("Streaming JSONs exceed the static file budget")
    source_doc = _read_json(plan.source)
    paths = {_output_path(plan.output, name, plan.data_root): (name, doc) for name, doc in plan.documents.items()}
    seen = set()
    for name, doc in plan.documents.items():
        if _node_count(doc["root"]) > plan.max_nodes or len(encoded(doc)) > MAX_JSON_BYTES:
            raise ValueError("Streaming document exceeds its node or byte budget")
        if doc["root"].get("refine") not in ("ADD", "REPLACE"):
            raise ValueError("External root refinement is missing")
        expected_header = copy.deepcopy({key: value for key, value in source_doc.items()
                                         if key != "root" and (name == "tileset.json" or key in EXTERNAL_HEADER_KEYS)})
        if name != "tileset.json":
            expected_header["geometricError"] = doc["root"]["geometricError"]
        if "schemaUri" in expected_header:
            expected_header["schemaUri"] = _relative(_uri_path(expected_header["schemaUri"], plan.source, plan.data_root), plan.output / name)
        if {key: value for key, value in doc.items() if key != "root"} != expected_header:
            raise ValueError("Streaming tileset header or metadata changed")

    def records(tile, context, parent, inherited, logical_path, *, streamed, trail=frozenset()):
        transform = _multiply(parent, _transform(tile))
        refine = tile.get("refine", inherited)
        contents = _contents(tile)
        if streamed and len(contents) == 1:
            target = _uri_path(contents[0][_uri_key(contents[0])], context, plan.data_root, file=False)
            if target in paths:
                if "children" in tile or target in trail:
                    raise ValueError("External tileset has children or forms a cycle")
                name, child_doc = paths[target]
                child_bytes = encoded(child_doc)
                if contents[0].get("extras") != {"bytes": len(child_bytes), "sha256": _sha(child_bytes)}:
                    raise ValueError("External tileset integrity reference differs")
                if "transform" in child_doc["root"]:
                    raise ValueError("External root would apply its transform twice")
                for key in ("boundingVolume", "geometricError", "viewerRequestVolume", "refine"):
                    if tile.get(key) != child_doc["root"].get(key):
                        raise ValueError("External reference culling or LOD semantics changed")
                seen.add(name)
                return records(child_doc["root"], target, transform, refine, logical_path,
                               streamed=True, trail=trail | {target})
        normalized = copy.deepcopy({key: value for key, value in tile.items() if key not in ("children", "transform", "refine")})
        for content in _contents(normalized):
            key = _uri_key(content)
            content[key] = str(_uri_path(content[key], context, plan.data_root))
        result = [(logical_path, normalized, refine, transform)]
        for index, child in enumerate(tile.get("children", [])):
            result.extend(records(child, context, transform, refine, logical_path + (index,), streamed=streamed, trail=trail))
        return result

    seen.add("tileset.json")
    old = records(source_doc["root"], plan.source, IDENTITY, source_doc["root"]["refine"], (), streamed=False)
    new = records(plan.documents["tileset.json"]["root"], plan.root_path, IDENTITY,
                  source_doc["root"]["refine"], (), streamed=True, trail=frozenset({plan.root_path}))
    if len(old) != len(new) or len(old) != plan.source_node_count:
        raise ValueError("Streaming tree omitted or duplicated source nodes")
    max_error = 0.
    for previous, current in zip(old, new):
        if previous[:3] != current[:3]:
            raise ValueError("Streaming content, order, metadata or LOD semantics changed")
        error = max(abs(a - b) for a, b in zip(previous[3], current[3]))
        if error > 1e-9:
            raise ValueError("Streaming world transform differs")
        max_error = max(max_error, error)
    if seen != set(plan.documents):
        raise ValueError("Streaming plan contains unreachable JSONs")
    return {"passed": True, "source_nodes_preserved": len(old), "json_files_reachable": len(seen),
            "max_world_matrix_element_difference": max_error}


def write_streaming_plan(plan: StreamingPlan) -> dict:
    """Write a new directory only. Failed partial output is left for inspection."""
    proof = verify_streaming_plan(plan)
    output = _confined(plan.output, plan.data_root)
    if output.exists():
        raise ValueError("Output already exists; no files will be overwritten")
    output.mkdir(parents=True, exist_ok=False)
    # Children first; the entrypoint is created only after every external JSON.
    names = sorted(name for name in plan.documents if name != "tileset.json") + ["tileset.json"]
    for name in names:
        target = _output_path(output, name, plan.data_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(encoded(plan.documents[name]))
    return {**plan.summary(), **proof, "root_path": str(plan.root_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--existing-file-count", type=int, required=True)
    parser.add_argument("--max-nodes", type=int, default=DEFAULT_MAX_NODES)
    parser.add_argument("--target-file-count", type=int, default=18000)
    parser.add_argument("--write", action="store_true", help="Create the new directory; default only plans")
    args = parser.parse_args()
    plan = plan_streaming_tileset(args.source, args.output, data_root=args.data_root,
                                  existing_file_count=args.existing_file_count, max_nodes=args.max_nodes,
                                  target_file_count=args.target_file_count)
    print(json.dumps(write_streaming_plan(plan) if args.write else plan.summary(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
