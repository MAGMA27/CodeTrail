"""Deterministic entry-point and module overview generation for one snapshot."""

from __future__ import annotations

import ast
import hashlib
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable

from .contracts import (
    CodeEdge,
    Evidence,
    OverviewEntryPoint,
    OverviewModule,
    OverviewPath,
    ProjectOverview,
    OverviewCoverage,
    UnresolvedReference,
)
from .relations import RelationIndex


RULES_VERSION = "python-entry-v1"
DEFAULT_MAX_DEPTH = 4
DEFAULT_MAX_PATHS = 40
DEFAULT_MAX_MODULES = 100
DEFAULT_MAX_SECONDS = 2.0
_MAX_EVIDENCE_LINES = 200


EvidenceReader = Callable[[str, str, int, int], Evidence]


@dataclass(frozen=True)
class _EntryCandidate:
    path: str
    start_line: int
    end_line: int
    kind: str
    rule_id: str
    label: str
    symbol_id: str
    guard: bool

    @property
    def entry_point_id(self) -> str:
        identity = "\0".join(
            [RULES_VERSION, self.path, self.kind, self.rule_id, str(self.start_line)]
        ).encode("utf-8")
        return f"entry-{hashlib.sha256(identity).hexdigest()[:32]}"


def build_project_overview(
    project_id: str,
    relation: RelationIndex,
    read_evidence: EvidenceReader,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_paths: int = DEFAULT_MAX_PATHS,
    max_modules: int = DEFAULT_MAX_MODULES,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> ProjectOverview:
    """Build a bounded overview from persisted symbols, edges, and source text only."""

    if max_depth < 1 or max_paths < 1 or max_modules < 1 or max_seconds <= 0:
        raise ValueError("overview budgets must be positive")

    index = relation.snapshot.index
    deadline = time.monotonic() + max_seconds
    symbols_by_id = {symbol.symbol_id: symbol for symbol in relation.symbols}
    modules = tuple(
        sorted(
            (symbol for symbol in relation.symbols if symbol.kind == "module"),
            key=lambda symbol: (symbol.path, symbol.qualified_name, symbol.symbol_id),
        )
    )
    module_by_path = {module.path: module for module in modules}
    edges_by_source: dict[str, list[CodeEdge]] = defaultdict(list)
    for edge in relation.edges:
        if edge.kind in {"calls", "imports"}:
            edges_by_source[edge.source_id].append(edge)
    for source_edges in edges_by_source.values():
        source_edges.sort(
            key=lambda edge: (
                edge.source_location.start_line,
                edge.kind,
                edge.target_id,
                edge.edge_id,
            )
        )
    unresolved_by_source: dict[str, list[UnresolvedReference]] = defaultdict(list)
    for unresolved in relation.unresolved:
        unresolved_by_source[unresolved.source_symbol_id].append(unresolved)
    for references in unresolved_by_source.values():
        references.sort(key=lambda item: (item.start_line, item.end_line, item.unresolved_id))

    candidates = _find_entry_candidates(relation, deadline)
    entries: list[OverviewEntryPoint] = []
    paths: list[OverviewPath] = []
    reachable_symbol_ids: set[str] = set()
    reachable_module_ids: set[str] = set()
    module_entry_ids: dict[str, list[str]] = defaultdict(list)
    unresolved_ids_by_entry: dict[str, list[str]] = {}
    direct_symbol_ids_by_entry: dict[str, list[str]] = {}
    reasons: list[str] = []

    if relation.truncated and relation.truncation_reason:
        reasons.append(f"relation index: {relation.truncation_reason}")
    if _deadline_reached(deadline):
        reasons.append("max_seconds reached")

    cycle_skipped = False
    for candidate in candidates:
        if _deadline_reached(deadline):
            reasons.append("max_seconds reached")
            break
        root_id = candidate.symbol_id
        root = symbols_by_id.get(root_id)
        if root is None:
            continue
        entry_id = candidate.entry_point_id
        entry_unresolved = [
            reference
            for reference in unresolved_by_source.get(root_id, [])
            if not candidate.guard
            or candidate.start_line <= reference.start_line <= candidate.end_line
        ]
        unresolved_ids_by_entry[entry_id] = [reference.unresolved_id for reference in entry_unresolved]

        try:
            evidence = read_evidence(
                index.index_id,
                candidate.path,
                candidate.start_line,
                min(candidate.end_line, candidate.start_line + _MAX_EVIDENCE_LINES - 1),
            )
            evidence_error = None
        except Exception:
            evidence = None
            evidence_error = "entry source evidence is unavailable in the persisted snapshot"

        entries.append(
            OverviewEntryPoint(
                entry_point_id=entry_id,
                index_id=index.index_id,
                path=candidate.path,
                start_line=candidate.start_line,
                end_line=candidate.end_line,
                kind=candidate.kind,  # type: ignore[arg-type]
                rule_id=candidate.rule_id,
                label=candidate.label,
                symbol_id=root_id,
                evidence=evidence,
                evidence_error=evidence_error,
                direct_symbol_ids=[],
                unresolved_ids=unresolved_ids_by_entry[entry_id],
            )
        )
        reachable_symbol_ids.add(root_id)
        root_module = module_by_path.get(root.path)
        if root_module is not None:
            reachable_module_ids.add(root_module.symbol_id)
            module_entry_ids[root_module.symbol_id].append(entry_id)

        queue: deque[tuple[str, tuple[str, ...], tuple[CodeEdge, ...]]] = deque(
            [(root_id, (root_id,), ())]
        )
        best_depth: dict[str, int] = {root_id: 0}
        direct_symbols: list[str] = []
        while queue:
            if _deadline_reached(deadline):
                reasons.append("max_seconds reached")
                break
            current_id, symbol_path, edge_path = queue.popleft()
            current_depth = len(edge_path)
            if current_depth >= max_depth:
                continue
            for edge in edges_by_source.get(current_id, []):
                if not _edge_allowed(candidate, root_id, edge):
                    continue
                target = symbols_by_id.get(edge.target_id)
                if target is None or target.symbol_id in symbol_path:
                    if target is not None and target.symbol_id in symbol_path:
                        cycle_skipped = True
                    continue
                next_depth = current_depth + 1
                if best_depth.get(target.symbol_id, max_depth + 1) <= next_depth:
                    continue
                if len(paths) >= max_paths:
                    reasons.append("max_paths reached")
                    queue.clear()
                    break
                next_symbols = (*symbol_path, target.symbol_id)
                next_edges = (*edge_path, edge)
                path_identity = "\0".join(
                    [index.index_id, entry_id, *(item.edge_id for item in next_edges)]
                ).encode("utf-8")
                paths.append(
                    OverviewPath(
                        path_id=f"overview-path-{hashlib.sha256(path_identity).hexdigest()[:32]}",
                        index_id=index.index_id,
                        entry_point_id=entry_id,
                        symbol_ids=list(next_symbols),
                        edges=list(next_edges),
                        depth=next_depth,
                    )
                )
                best_depth[target.symbol_id] = next_depth
                reachable_symbol_ids.add(target.symbol_id)
                target_module = module_by_path.get(target.path)
                if target_module is not None:
                    reachable_module_ids.add(target_module.symbol_id)
                    if entry_id not in module_entry_ids[target_module.symbol_id]:
                        module_entry_ids[target_module.symbol_id].append(entry_id)
                if current_id == root_id and target.symbol_id not in direct_symbols:
                    direct_symbols.append(target.symbol_id)
                queue.append((target.symbol_id, next_symbols, next_edges))
        direct_symbol_ids_by_entry[entry_id] = direct_symbols

    if not candidates and not _deadline_reached(deadline):
        notes = [
            "未识别入口；这不表示项目没有入口。",
            "当前规则只覆盖 __main__.py 和顶层 __name__ == __main__ 条件。",
        ]
    else:
        notes = [
            "当前规则只覆盖 __main__.py 和顶层 __name__ == __main__ 条件。",
            "框架路由、插件入口和 pyproject 脚本元数据尚未纳入规则。",
        ]
    if cycle_skipped:
        notes.append("循环调用只保留首次有界路径，未继续展开循环。")

    for entry in entries:
        entry.direct_symbol_ids = direct_symbol_ids_by_entry.get(entry.entry_point_id, [])

    total_reachable_modules = len(reachable_module_ids)
    total_modules = len(modules)
    visible_modules = list(
        sorted(
            modules,
            key=lambda module: (
                module.symbol_id not in reachable_module_ids,
                module.path,
                module.qualified_name,
                module.symbol_id,
            ),
        )[:max_modules]
    )
    if len(visible_modules) < total_modules:
        reasons.append("max_modules reached")

    visible_unresolved = [
        unresolved
        for unresolved in relation.unresolved
        if unresolved.source_symbol_id in reachable_symbol_ids
    ]
    max_unresolved = max(1, max_paths * 2)
    if len(visible_unresolved) > max_unresolved:
        visible_unresolved = visible_unresolved[:max_unresolved]
        reasons.append("max_unresolved reached")

    module_models = [
        OverviewModule(
            module_id=module.symbol_id,
            index_id=index.index_id,
            symbol=module,
            entry_point_ids=module_entry_ids.get(module.symbol_id, []),
            entry_reachable=module.symbol_id in reachable_module_ids,
        )
        for module in visible_modules
    ]
    entry_status = (
        "not_identified"
        if not entries
        else "partial"
        if reasons
        else "identified"
    )
    unique_reasons = list(dict.fromkeys(reasons))
    truncated = bool(unique_reasons)
    truncation_reason = "; ".join(unique_reasons)[:1_000] if truncated else None
    return ProjectOverview(
        project_id=project_id,
        index=index,
        rules_version=RULES_VERSION,
        entry_status=entry_status,  # type: ignore[arg-type]
        entries=entries,
        modules=module_models,
        paths=paths,
        unresolved=visible_unresolved,
        coverage=OverviewCoverage(
            total_modules=total_modules,
            visible_modules=len(visible_modules),
            reachable_modules=total_reachable_modules,
            unreachable_modules=total_modules - total_reachable_modules,
            entry_point_count=len(entries),
            unresolved_count=len(visible_unresolved),
        ),
        notes=notes,
        truncated=truncated,
        truncation_reason=truncation_reason,
    )


def _find_entry_candidates(
    relation: RelationIndex,
    deadline: float,
) -> list[_EntryCandidate]:
    candidates: list[_EntryCandidate] = []
    symbols_by_path = {
        symbol.path: symbol
        for symbol in relation.symbols
        if symbol.kind == "module"
    }
    for source_file in sorted(relation.snapshot.files, key=lambda item: item.path):
        if _deadline_reached(deadline):
            break
        module = symbols_by_path.get(source_file.path)
        if module is None:
            continue
        if source_file.path.rsplit("/", 1)[-1] == "__main__.py":
            line_count = max(1, len(source_file.text.splitlines()))
            candidates.append(
                _EntryCandidate(
                    path=source_file.path,
                    start_line=1,
                    end_line=line_count,
                    kind="python_main_module",
                    rule_id="python.file_name.__main__",
                    label="Python __main__.py module",
                    symbol_id=module.symbol_id,
                    guard=False,
                )
            )
        try:
            tree = ast.parse(source_file.text, filename=source_file.path, type_comments=True)
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.If) or not _is_main_guard(node.test):
                continue
            end_line = getattr(node, "end_lineno", None) or node.lineno
            candidates.append(
                _EntryCandidate(
                    path=source_file.path,
                    start_line=node.lineno,
                    end_line=end_line,
                    kind="python_main_guard",
                    rule_id="python.ast.main_guard",
                    label='Top-level if __name__ == "__main__"',
                    symbol_id=module.symbol_id,
                    guard=True,
                )
            )
    candidates.sort(key=lambda item: (item.path, item.start_line, item.kind, item.rule_id))
    return candidates


def _is_main_guard(test: ast.expr) -> bool:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(right, ast.Constant)
        and right.value == "__main__"
    ) or (
        isinstance(right, ast.Name)
        and right.id == "__name__"
        and isinstance(left, ast.Constant)
        and left.value == "__main__"
    )


def _edge_allowed(candidate: _EntryCandidate, root_id: str, edge: CodeEdge) -> bool:
    if edge.kind not in {"calls", "imports"}:
        return False
    if edge.source_id != root_id:
        return True
    if not candidate.guard:
        return True
    return edge.kind == "calls" and candidate.start_line <= edge.source_location.start_line <= candidate.end_line


def _deadline_reached(deadline: float) -> bool:
    return time.monotonic() >= deadline
