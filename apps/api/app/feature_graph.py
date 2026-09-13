"""Build a bounded graph from one saved semantic feature result and its snapshot."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from .contracts import (
    Feature,
    FeatureGeneration,
    FeatureGraphEdge,
    FeatureGraphNode,
    FeatureGraphSnapshot,
    OverviewEntryPoint,
    ProjectOverview,
    Symbol,
)
from .relations import RelationIndex, relation_coverage_limitations


DEFAULT_MAX_NODES = 40
DEFAULT_MAX_EDGES = 80
MAX_NODES = 40
MAX_EDGES = 120


def build_feature_graph(
    project_id: str,
    generation: FeatureGeneration,
    relation: RelationIndex,
    overview: ProjectOverview,
    *,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_edges: int = DEFAULT_MAX_EDGES,
) -> FeatureGraphSnapshot:
    """Overlay saved feature mappings with static edges from the same immutable index."""

    if max_nodes < 1 or max_nodes > MAX_NODES or max_edges < 1 or max_edges > MAX_EDGES:
        raise ValueError("feature graph budgets are outside the allowed range")
    index_id = generation.index.index_id
    if generation.project_id != project_id or relation.snapshot.index.index_id != index_id:
        raise ValueError("feature graph inputs must use one project and index")
    if overview.project_id != project_id or overview.index.index_id != index_id:
        raise ValueError("feature graph overview must use the generation project and index")

    limitations = list(generation.limitations)
    reasons: list[str] = []
    if relation.truncated and relation.truncation_reason:
        reasons.append(f"static relation graph: {relation.truncation_reason}")
    limitations.extend(
        relation_coverage_limitations(
            truncated=False,
            truncation_reason=None,
            unresolved_truncated=relation.unresolved_truncated,
            unresolved_truncation_reason=relation.unresolved_truncation_reason,
        )
    )
    if generation.status == "failed":
        limitations.append("失败版本没有可展开的功能节点。")
    elif generation.status == "insufficient_evidence":
        limitations.append("证据不足版本没有可展开的功能节点。")
    if generation.rules_version != overview.rules_version:
        limitations.append(
            f"保存结果规则 {generation.rules_version} 与当前入口规则 {overview.rules_version} 不一致，入口映射不可用。"
        )

    features = tuple(sorted(generation.features, key=lambda item: (item.name, item.feature_id)))
    overview_entries = (
        {entry.entry_point_id: entry for entry in overview.entries}
        if generation.rules_version == overview.rules_version
        else {}
    )
    symbols = {symbol.symbol_id: symbol for symbol in relation.symbols}
    features_by_id = {feature.feature_id: feature for feature in features}
    target_order: list[tuple[str, str]] = []
    target_evidence: dict[tuple[str, str], set[str]] = defaultdict(set)

    def remember_target(kind: str, target_id: str, evidence_ids: list[str]) -> None:
        key = (kind, target_id)
        if key not in target_evidence:
            target_order.append(key)
        target_evidence[key].update(evidence_ids)

    for feature in features:
        for entry_id in feature.entry_point_ids:
            remember_target("entry_point", entry_id, feature.evidence_ids)
        for module_id in feature.module_ids:
            remember_target("module", module_id, feature.evidence_ids)
        for symbol_id in feature.symbol_ids:
            remember_target("symbol", symbol_id, feature.evidence_ids)
    for association in sorted(generation.associations, key=lambda item: item.association_id):
        remember_target(association.target_kind, association.target_id, association.evidence_ids)

    nodes: list[FeatureGraphNode] = []
    nodes_by_id: dict[str, FeatureGraphNode] = {}
    unavailable: list[str] = []
    resolved_targets: dict[tuple[str, str], str] = {}

    for feature in features:
        if len(nodes) >= max_nodes:
            unavailable.append(feature.feature_id)
            reasons.append("max_nodes reached")
            continue
        node = FeatureGraphNode(
            node_id=f"feature:{feature.feature_id}",
            index_id=index_id,
            kind="feature",
            label=feature.name,
            detail=feature.description,
            feature_id=feature.feature_id,
            evidence_ids=list(feature.evidence_ids),
        )
        nodes.append(node)
        nodes_by_id[node.node_id] = node

    for target_kind, target_id in target_order:
        node_id, node = _target_node(
            index_id,
            target_kind,
            target_id,
            overview_entries,
            symbols,
            sorted(target_evidence[(target_kind, target_id)]),
        )
        if node is None or node_id is None:
            if target_id not in unavailable:
                unavailable.append(target_id)
            continue
        existing = nodes_by_id.get(node_id)
        if existing is not None:
            existing.evidence_ids = sorted(set(existing.evidence_ids) | set(node.evidence_ids))
            resolved_targets[(target_kind, target_id)] = node_id
            continue
        if len(nodes) >= max_nodes:
            if target_id not in unavailable:
                unavailable.append(target_id)
            reasons.append("max_nodes reached")
            continue
        nodes.append(node)
        nodes_by_id[node.node_id] = node
        resolved_targets[(target_kind, target_id)] = node_id

    semantic_edges: dict[tuple[str, str, str], set[str]] = defaultdict(set)

    def add_semantic_edge(
        feature: Feature,
        relation: str,
        target_kind: str,
        target_id: str,
        evidence_ids: list[str],
    ) -> None:
        source_id = f"feature:{feature.feature_id}"
        target_node_id = resolved_targets.get((target_kind, target_id))
        if source_id not in nodes_by_id or target_node_id is None:
            return
        edge_key = (source_id, target_node_id, relation)
        semantic_edges[edge_key].update(evidence_ids)

    for feature in features:
        for entry_id in feature.entry_point_ids:
            add_semantic_edge(feature, "triggered_by", "entry_point", entry_id, feature.evidence_ids)
        for module_id in feature.module_ids:
            add_semantic_edge(feature, "implemented_by", "module", module_id, feature.evidence_ids)
        for symbol_id in feature.symbol_ids:
            add_semantic_edge(feature, "implemented_by", "symbol", symbol_id, feature.evidence_ids)
    for association in sorted(generation.associations, key=lambda item: item.association_id):
        feature = features_by_id.get(association.feature_id)
        if feature is None:
            continue
        add_semantic_edge(
            feature,
            association.relation,
            association.target_kind,
            association.target_id,
            association.evidence_ids,
        )

    edges: list[FeatureGraphEdge] = []
    for (source_id, target_id, semantic_relation), evidence_ids in sorted(semantic_edges.items()):
        if len(edges) >= max_edges:
            reasons.append("max_edges reached")
            break
        edges.append(
            FeatureGraphEdge(
                edge_id=_graph_edge_id(generation.generation_id, "semantic", semantic_relation, source_id, target_id),
                index_id=index_id,
                source_id=source_id,
                target_id=target_id,
                origin="semantic",
                relation=semantic_relation,  # type: ignore[arg-type]
                evidence_ids=sorted(evidence_ids),
            )
        )

    symbol_node_ids = {
        node.symbol.symbol_id
        for node in nodes
        if node.symbol is not None
    }
    for static_edge in sorted(
        relation.edges,
        key=lambda edge: (
            edge.source_location.path,
            edge.source_location.start_line,
            edge.kind,
            edge.edge_id,
        ),
    ):
        if static_edge.source_id not in symbol_node_ids or static_edge.target_id not in symbol_node_ids:
            continue
        if len(edges) >= max_edges:
            reasons.append("max_edges reached")
            break
        edges.append(
            FeatureGraphEdge(
                edge_id=_graph_edge_id(
                    generation.generation_id,
                    "static",
                    static_edge.kind,
                    static_edge.source_id,
                    static_edge.target_id,
                ),
                index_id=index_id,
                source_id=static_edge.source_id,
                target_id=static_edge.target_id,
                origin="static",
                relation=static_edge.kind,
            )
        )

    if reasons:
        reasons = list(dict.fromkeys(reasons))
    if unavailable:
        limitations.append("部分保存结果目标无法映射到当前规则或快照，已在图中明确列出。")
    limitations = list(dict.fromkeys(limitations))[:30]
    return FeatureGraphSnapshot(
        project_id=project_id,
        index_id=index_id,
        generation_id=generation.generation_id,
        status=generation.status,
        nodes=nodes,
        edges=edges,
        evidence=list(generation.evidence),
        unavailable_target_ids=list(dict.fromkeys(unavailable)),
        limitations=limitations,
        truncated=bool(reasons),
        truncation_reason="; ".join(reasons)[:1_000] if reasons else None,
    )


def _target_node(
    index_id: str,
    target_kind: str,
    target_id: str,
    overview_entries: dict[str, OverviewEntryPoint],
    symbols: dict[str, Symbol],
    evidence_ids: list[str],
) -> tuple[str | None, FeatureGraphNode | None]:
    if target_kind == "entry_point":
        entry = overview_entries.get(target_id)
        if entry is None:
            return None, None
        return (
            f"entry:{entry.entry_point_id}",
            FeatureGraphNode(
                node_id=f"entry:{entry.entry_point_id}",
                index_id=index_id,
                kind="entry_point",
                label=entry.label,
                detail=f"{entry.kind} · {entry.rule_id}",
                entry_point_id=entry.entry_point_id,
                path=entry.path,
                start_line=entry.start_line,
                end_line=entry.end_line,
                evidence_ids=evidence_ids,
            ),
        )
    symbol = symbols.get(target_id)
    if symbol is None:
        return None, None
    if target_kind == "module" and symbol.kind != "module":
        return None, None
    if target_kind == "symbol" and symbol.kind == "module":
        return None, None
    kind = "module" if symbol.kind == "module" else "symbol"
    return (
        symbol.symbol_id,
        FeatureGraphNode(
            node_id=symbol.symbol_id,
            index_id=index_id,
            kind=kind,
            label=symbol.qualified_name,
            detail=f"{symbol.path}:L{symbol.start_line}–L{symbol.end_line}",
            symbol=symbol,
            evidence_ids=evidence_ids,
        ),
    )


def _graph_edge_id(
    generation_id: str,
    origin: str,
    relation: str,
    source_id: str,
    target_id: str,
) -> str:
    identity = "\0".join([generation_id, origin, relation, source_id, target_id]).encode("utf-8")
    return f"feature-graph-edge-{hashlib.sha256(identity).hexdigest()[:32]}"
