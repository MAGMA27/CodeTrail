"""Derive the four read-only layers of one bounded Issue impact query.

Layers classify an existing static-calls result. They do not walk imports,
contextual calls, or repository execution, and they do not confirm impact.
"""

from __future__ import annotations

from collections.abc import Sequence

from .contracts import (
    ImpactLayer,
    ImpactLayerDetermination,
    ImpactLayerKind,
    ImpactPath,
    Symbol,
    UnresolvedReference,
)

_LAYER_SPECS: tuple[tuple[ImpactLayerKind, ImpactLayerDetermination, str], ...] = (
    (
        "report_anchor",
        "saved_report_symbol",
        "报告锚点是已保存报告里唯一映射到的符号，只定位调查起点，不是已确认影响。",
    ),
    (
        "static_direct",
        "static_calls",
        "直接静态可达是与锚点相距一条同快照 calls 边的符号。调用可达不等于已确认影响。",
    ),
    (
        "static_indirect",
        "static_calls",
        "间接静态可达是最短简单 calls 路径深度为 2 的符号。调用可达不等于已确认影响。",
    ),
    (
        "unknown",
        "unresolved_reference",
        "未知边界是返回节点上的 dynamic 或 unresolved 引用。它们保持未知，不会画成确定影响边。",
    ),
)


def build_impact_layers(
    *,
    mapping_status: str,
    root_symbol_id: str | None,
    nodes: Sequence[Symbol],
    paths: Sequence[ImpactPath],
    unresolved: Sequence[UnresolvedReference],
    max_depth: int,
) -> list[ImpactLayer]:
    """Classify one impact response into the four fixed layers.

    Mapped symbols use the shortest simple ``calls`` path. A symbol that is
    also reachable by a longer returned path stays in the shallower layer.
    Unresolved references stay in ``unknown`` and are never promoted to a
    symbol layer. Truncation is not repaired here: only returned nodes and
    references are classified.
    """

    if mapping_status not in {"mapped", "unmapped"}:
        raise ValueError("impact mapping status must be mapped or unmapped")
    if max_depth not in {1, 2}:
        raise ValueError("impact layers only support depth 1 or 2")

    anchor_ids: list[str] = []
    direct_ids: list[str] = []
    indirect_ids: list[str] = []
    unknown_ids: list[str] = []
    if mapping_status == "unmapped":
        if root_symbol_id is not None or nodes or paths or unresolved:
            raise ValueError("unmapped impact cannot carry symbols, paths, or unresolved references")
    else:
        if root_symbol_id is None:
            raise ValueError("mapped impact requires a root symbol")
        depths = {root_symbol_id: 0}
        for path in paths:
            for index, symbol_id in enumerate(path.symbol_ids):
                if index > max_depth:
                    raise ValueError("impact path exceeds the requested depth")
                current = depths.get(symbol_id)
                if current is None or index < current:
                    depths[symbol_id] = index
        returned_ids = {node.symbol_id for node in nodes}
        missing = [node.symbol_id for node in nodes if node.symbol_id not in depths]
        if missing:
            raise ValueError("impact node is not on a returned static path")
        if any(symbol_id not in returned_ids for symbol_id in depths):
            raise ValueError("impact path references a symbol that was not returned")
        direct_ids = sorted(symbol_id for symbol_id, depth in depths.items() if depth == 1)
        indirect_ids = sorted(symbol_id for symbol_id, depth in depths.items() if depth == 2)
        if max_depth == 1 and indirect_ids:
            raise ValueError("depth 1 impact cannot include an indirect layer")
        anchor_ids = [root_symbol_id]
        unknown_ids = [item.unresolved_id for item in unresolved]

    membership = {
        "report_anchor": (anchor_ids, []),
        "static_direct": (direct_ids, []),
        "static_indirect": (indirect_ids, []),
        "unknown": ([], unknown_ids),
    }
    return [
        ImpactLayer(
            kind=kind,
            determination=determination,
            symbol_ids=membership[kind][0],
            unresolved_ids=membership[kind][1],
            summary=summary,
        )
        for kind, determination, summary in _LAYER_SPECS
    ]
