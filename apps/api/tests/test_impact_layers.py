"""Fixture tests for bounded Issue impact layers."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from typing import Literal

from app.contracts import (
    CodeEdge,
    ImpactMappingStatus,
    ImpactPath,
    RunImpactResponse,
    SourceLocation,
    Symbol,
    UnresolvedReference,
)
from app.impact_layers import build_impact_layers
from app.indexing import build_source_snapshot, extract_definitions
from app.relations import extract_relations
from app.storage import IndexStore


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "fixtures" / "repos" / "timeout-demo"
INDEX_ID = "index-impact-layers"


def _symbol(symbol_id: str, name: str) -> Symbol:
    return Symbol(
        symbol_id=symbol_id,
        index_id=INDEX_ID,
        path="pkg/app.py",
        qualified_name=name,
        kind="function",
        start_line=1,
        end_line=2,
    )


def _edge(edge_id: str, source_id: str, target_id: str) -> CodeEdge:
    return CodeEdge(
        edge_id=edge_id,
        index_id=INDEX_ID,
        source_id=source_id,
        target_id=target_id,
        kind="calls",
        extraction_method="fixture",
        source_location=SourceLocation(path="pkg/app.py", start_line=1, end_line=1),
    )


def _path(path_id: str, symbol_ids: list[str], edges: list[CodeEdge]) -> ImpactPath:
    return ImpactPath(
        path_id=path_id,
        index_id=INDEX_ID,
        symbol_ids=symbol_ids,
        edges=edges,
        depth=len(edges),
    )


def _response(
    *,
    mapping_status: ImpactMappingStatus = "mapped",
    root: Symbol | None = None,
    nodes: list[Symbol] | None = None,
    edges: list[CodeEdge] | None = None,
    paths: list[ImpactPath] | None = None,
    unresolved: list[UnresolvedReference] | None = None,
    max_depth: int = 2,
    truncated: bool = False,
) -> RunImpactResponse:
    nodes = list(nodes or ([] if root is None else [root]))
    paths = list(paths or [])
    unresolved = list(unresolved or [])
    layers = build_impact_layers(
        mapping_status=mapping_status,
        root_symbol_id=None if root is None else root.symbol_id,
        nodes=nodes,
        paths=paths,
        unresolved=unresolved,
        max_depth=max_depth,
    )
    return RunImpactResponse(
        project_id="project-impact-layers",
        session_id="session-impact-layers",
        run_id="run-impact-layers",
        index_id=INDEX_ID,
        candidate_id="candidate-impact-layers" if mapping_status == "mapped" else None,
        finding_id=None if mapping_status == "mapped" else "finding-impact-layers",
        mapping_status=mapping_status,
        direction="callees",
        max_depth=max_depth,
        root_symbol_id=None if root is None else root.symbol_id,
        root=root,
        nodes=nodes,
        edges=list(edges or []),
        paths=paths,
        unresolved=unresolved,
        layers=layers,
        reachable_node_count=max(0, len(nodes) - (0 if root is None else 1)),
        returned_node_count=len(nodes),
        scope_note="静态可达不等同于已确认影响。",
        truncated=truncated,
        truncation_reason="max_nodes reached" if truncated else None,
    )


def test_shortest_static_path_assigns_each_symbol_to_one_layer() -> None:
    root = _symbol("symbol-root", "pkg.app.root")
    direct = _symbol("symbol-direct", "pkg.app.direct")
    both = _symbol("symbol-both", "pkg.app.both")
    indirect = _symbol("symbol-indirect", "pkg.app.indirect")
    root_direct = _edge("edge-root-direct", root.symbol_id, direct.symbol_id)
    root_both = _edge("edge-root-both", root.symbol_id, both.symbol_id)
    direct_indirect = _edge("edge-direct-indirect", direct.symbol_id, indirect.symbol_id)
    direct_both = _edge("edge-direct-both", direct.symbol_id, both.symbol_id)
    unresolved = UnresolvedReference(
        unresolved_id="unresolved-dynamic",
        index_id=INDEX_ID,
        source_symbol_id=indirect.symbol_id,
        path="pkg/app.py",
        expression="globals()[name]",
        reason="dynamic_call_target",
        start_line=2,
        end_line=2,
    )
    response = _response(
        root=root,
        nodes=[root, both, direct, indirect],
        edges=[root_direct, root_both, direct_indirect, direct_both],
        paths=[
            _path("path-direct", [root.symbol_id, direct.symbol_id], [root_direct]),
            _path("path-both", [root.symbol_id, both.symbol_id], [root_both]),
            _path(
                "path-indirect",
                [root.symbol_id, direct.symbol_id, indirect.symbol_id],
                [root_direct, direct_indirect],
            ),
            _path(
                "path-both-longer",
                [root.symbol_id, direct.symbol_id, both.symbol_id],
                [root_direct, direct_both],
            ),
        ],
        unresolved=[unresolved],
    )

    anchor, direct_layer, indirect_layer, unknown = response.layers
    assert anchor.symbol_ids == [root.symbol_id]
    assert direct_layer.symbol_ids == [both.symbol_id, direct.symbol_id]
    assert indirect_layer.symbol_ids == [indirect.symbol_id]
    assert unknown.symbol_ids == []
    assert unknown.unresolved_ids == [unresolved.unresolved_id]
    assert "不是已确认影响" in anchor.summary
    assert "不等于已确认影响" in direct_layer.summary
    assert "不等于已确认影响" in indirect_layer.summary
    assert "保持未知" in unknown.summary
    assert indirect.symbol_id not in direct_layer.symbol_ids
    assert both.symbol_id not in indirect_layer.symbol_ids


def test_depth_one_leaves_the_indirect_layer_empty() -> None:
    root = _symbol("symbol-root", "pkg.app.root")
    direct = _symbol("symbol-direct", "pkg.app.direct")
    edge = _edge("edge-root-direct", root.symbol_id, direct.symbol_id)
    response = _response(
        root=root,
        nodes=[root, direct],
        edges=[edge],
        paths=[_path("path-direct", [root.symbol_id, direct.symbol_id], [edge])],
        max_depth=1,
    )

    assert response.layers[1].symbol_ids == [direct.symbol_id]
    assert response.layers[2].symbol_ids == []
    assert response.layers[3].unresolved_ids == []


def test_unmapped_layers_are_empty_and_contracts_reject_misclassification() -> None:
    unmapped = _response(mapping_status="unmapped")
    assert unmapped.root is None
    assert all(not layer.symbol_ids and not layer.unresolved_ids for layer in unmapped.layers)

    root = _symbol("symbol-root", "pkg.app.root")
    direct = _symbol("symbol-direct", "pkg.app.direct")
    edge = _edge("edge-root-direct", root.symbol_id, direct.symbol_id)
    mapped = _response(
        root=root,
        nodes=[root, direct],
        edges=[edge],
        paths=[_path("path-direct", [root.symbol_id, direct.symbol_id], [edge])],
    )
    forged_layers = [layer.model_copy(deep=True) for layer in mapped.layers]
    forged_layers[1] = forged_layers[1].model_copy(update={"symbol_ids": []})
    forged_layers[2] = forged_layers[2].model_copy(update={"symbol_ids": [direct.symbol_id]})
    forged = mapped.model_dump()
    forged["layers"] = [layer.model_dump() for layer in forged_layers]
    with pytest.raises(ValidationError, match="shortest calls depth 2"):
        RunImpactResponse.model_validate(forged)

    occupied = [layer.model_copy(deep=True) for layer in unmapped.layers]
    occupied[0] = occupied[0].model_copy(update={"symbol_ids": [root.symbol_id]})
    occupied_payload = unmapped.model_dump()
    occupied_payload["layers"] = [layer.model_dump() for layer in occupied]
    with pytest.raises(ValidationError, match="unmapped impact layers must be empty"):
        RunImpactResponse.model_validate(occupied_payload)


def test_builder_rejects_a_returned_node_without_a_static_path() -> None:
    root = _symbol("symbol-root", "pkg.app.root")
    orphan = _symbol("symbol-orphan", "pkg.app.orphan")
    with pytest.raises(ValueError, match="not on a returned static path"):
        build_impact_layers(
            mapping_status="mapped",
            root_symbol_id=root.symbol_id,
            nodes=(root, orphan),
            paths=(),
            unresolved=(),
            max_depth=2,
        )


def test_static_impact_layers_ignore_imports_cycles_and_unreturned_nodes(tmp_path: Path) -> None:
    snapshot = build_source_snapshot(
        FIXTURE_ROOT,
        repository_id="timeout-demo",
        revision="impact-layers",
        dirty=False,
        index_id="impact-layer-sample",
    )
    relation = extract_relations(extract_definitions(snapshot))
    by_name = {symbol.qualified_name: symbol for symbol in relation.symbols}
    root = by_name["timeout_demo.__main__.main"]
    branch = by_name["timeout_demo.app.start"]
    target = by_name["timeout_demo.config.load_settings"]
    dynamic_symbol = by_name["timeout_demo.dispatch.dispatch"]

    def edge(
        edge_id: str,
        source,
        target_symbol,
        kind: Literal["calls", "imports"] = "calls",
    ) -> CodeEdge:
        return CodeEdge(
            edge_id=edge_id,
            index_id=relation.snapshot.index.index_id,
            source_id=source.symbol_id,
            target_id=target_symbol.symbol_id,
            kind=kind,
            extraction_method="fixture",
            source_location=SourceLocation(
                path=source.path,
                start_line=source.start_line,
                end_line=source.start_line,
            ),
        )

    relation = replace(
        relation,
        edges=(
            edge("impact-root-branch", root, branch),
            edge("impact-root-target", root, target),
            edge("impact-branch-target", branch, target),
            edge("impact-branch-root", branch, root),
            edge("impact-root-import", root, dynamic_symbol, "imports"),
        ),
    )
    with IndexStore(tmp_path / "impact-layers.sqlite3") as store:
        store.write_index(relation)
        result = store.get_static_impact(
            relation.snapshot.index.index_id,
            root.symbol_id,
            "callees",
            depth=2,
        )
        layers = build_impact_layers(
            mapping_status="mapped",
            root_symbol_id=result.root.symbol_id,
            nodes=result.nodes,
            paths=result.paths,
            unresolved=result.unresolved,
            max_depth=2,
        )
        by_kind = {layer.kind: layer for layer in layers}
        assert by_kind["report_anchor"].symbol_ids == [root.symbol_id]
        assert set(by_kind["static_direct"].symbol_ids) == {branch.symbol_id, target.symbol_id}
        assert by_kind["static_indirect"].symbol_ids == []
        assert dynamic_symbol.symbol_id not in {
            symbol_id for layer in layers for symbol_id in layer.symbol_ids
        }
        assert "impact-branch-root" in {item.edge_id for item in result.edges}
        assert root.symbol_id not in by_kind["static_direct"].symbol_ids
        assert target.symbol_id not in by_kind["static_indirect"].symbol_ids

        limited = store.get_static_impact(
            relation.snapshot.index.index_id,
            root.symbol_id,
            "callees",
            depth=2,
            limit=2,
        )
        limited_layers = build_impact_layers(
            mapping_status="mapped",
            root_symbol_id=limited.root.symbol_id,
            nodes=limited.nodes,
            paths=limited.paths,
            unresolved=limited.unresolved,
            max_depth=2,
        )
        returned = {symbol.symbol_id for symbol in limited.nodes}
        classified = {
            symbol_id
            for layer in limited_layers
            if layer.kind != "unknown"
            for symbol_id in layer.symbol_ids
        }
        assert limited.truncated is True
        assert classified == returned
        assert len(returned) == 2
        full_ids = {symbol.symbol_id for symbol in result.nodes}
        assert full_ids - returned
        assert not (full_ids - returned) & classified

        dynamic = store.get_static_impact(
            relation.snapshot.index.index_id,
            dynamic_symbol.symbol_id,
            "callees",
            depth=2,
        )
        dynamic_layers = build_impact_layers(
            mapping_status="mapped",
            root_symbol_id=dynamic.root.symbol_id,
            nodes=dynamic.nodes,
            paths=dynamic.paths,
            unresolved=dynamic.unresolved,
            max_depth=2,
        )
        dynamic_by_kind = {layer.kind: layer for layer in dynamic_layers}
        assert dynamic_by_kind["report_anchor"].symbol_ids == [dynamic_symbol.symbol_id]
        assert dynamic_by_kind["static_direct"].symbol_ids == []
        assert dynamic_by_kind["static_indirect"].symbol_ids == []
        assert dynamic_by_kind["unknown"].unresolved_ids == [
            item.unresolved_id for item in dynamic.unresolved
        ]
        assert any(item.reason == "dynamic_call_target" for item in dynamic.unresolved)
        assert not any(
            symbol.qualified_name in {"timeout_demo.handlers.handle", "timeout_demo.other_handlers.handle"}
            for symbol in dynamic.nodes
        )
