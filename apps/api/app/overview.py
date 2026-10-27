"""Deterministic entry-point and module overview generation for one snapshot."""

from __future__ import annotations

import ast
import hashlib
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable, Mapping

from .contracts import (
    CodeEdge,
    Evidence,
    OverviewClassification,
    OverviewClassificationCounts,
    OverviewClassificationCoverage,
    OverviewEntryPoint,
    OverviewGroup,
    OverviewHttpAssociation,
    OverviewHttpAssociationCoverage,
    OverviewModule,
    OverviewModuleRelation,
    OverviewPath,
    ProjectOverview,
    OverviewCoverage,
    Symbol,
    UnresolvedReference,
)
from .indexing import (
    SUBSYSTEM_MANIFEST_PATH,
    SnapshotFile,
    SubsystemManifestError,
    SubsystemOwner,
    SubsystemSpec,
    parse_subsystem_manifest,
)
from .relations import RelationIndex, relation_coverage_limitations


RULES_VERSION = "python-entry-v7+http-shape-v2+architecture-v1"
DEFAULT_MAX_DEPTH = 4
DEFAULT_MAX_PATHS = 40
DEFAULT_MAX_MODULES = 100
DEFAULT_MAX_GROUPS = 80
DEFAULT_MAX_MODULE_RELATIONS = 200
DEFAULT_MAX_SECONDS = 2.0
_MAX_EVIDENCE_LINES = 200
_MAX_ENTRY_CANDIDATES = 200
_MAX_RELATION_EDGE_SAMPLES = 8
_MAX_HTTP_ASSOCIATIONS = 100
_MAX_CLASSIFICATION_ITEMS = 200
_MAX_CLASSIFICATION_SCAN = 2_000
_MAX_CLASSIFICATION_EVIDENCE_LINES = 400
_MAX_CLASSIFICATION_EVIDENCE_CHARS = 40_000
_FASTAPI_ROUTE_METHODS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
    "options": "OPTIONS",
    "head": "HEAD",
    "trace": "TRACE",
}
_FASTAPI_BINDING_CLASS = "fastapi_class"
_FASTAPI_BINDING_MODULE = "fastapi_module"
_FASTAPI_BINDING_INSTANCE = "fastapi_instance"
_UNKNOWN_BINDING = "unknown"


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
    http_method: str | None = None
    declared_path: str | None = None
    handler_symbol: Symbol | None = None

    @property
    def entry_point_id(self) -> str:
        identity_parts = [RULES_VERSION, self.path, self.kind, self.rule_id]
        if self.kind == "fastapi_route":
            identity_parts.extend(
                [
                    self.http_method or "",
                    self.declared_path or "",
                    self.handler_symbol.symbol_id if self.handler_symbol is not None else self.symbol_id,
                ]
            )
        else:
            identity_parts.append(str(self.start_line))
        identity = "\0".join(identity_parts).encode("utf-8")
        return f"entry-{hashlib.sha256(identity).hexdigest()[:32]}"


@dataclass(frozen=True)
class _OverviewGroupSpec:
    group_kind: str
    group_key: str
    label: str
    module_ids: tuple[str, ...]
    entry_point_ids: tuple[str, ...]
    sort_key: tuple[object, ...]


@dataclass(frozen=True)
class _ArchitectureClassificationSpec:
    category: str
    module_id: str
    symbol_id: str | None
    symbol_name: str
    label: str
    rule_id: str
    evidence_locations: tuple[tuple[str, int, int], ...] = ()
    entry_point_id: str | None = None


def build_project_overview(
    project_id: str,
    relation: RelationIndex,
    read_evidence: EvidenceReader,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_paths: int = DEFAULT_MAX_PATHS,
    max_modules: int = DEFAULT_MAX_MODULES,
    max_groups: int = DEFAULT_MAX_GROUPS,
    max_module_relations: int = DEFAULT_MAX_MODULE_RELATIONS,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> ProjectOverview:
    """Build a bounded overview from persisted symbols, edges, and source text only."""

    if (
        max_depth < 1
        or max_paths < 1
        or max_modules < 1
        or max_groups < 1
        or max_module_relations < 1
        or max_seconds <= 0
    ):
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

    candidates, entry_candidates_truncated = _find_entry_candidates(relation, deadline)
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
    unresolved_limitations = relation_coverage_limitations(
        truncated=False,
        truncation_reason=None,
        unresolved_truncated=relation.unresolved_truncated,
        unresolved_truncation_reason=relation.unresolved_truncation_reason,
    )
    if relation.unresolved_truncated is True:
        reasons.extend(f"relation index: {item}" for item in unresolved_limitations)
    if entry_candidates_truncated:
        reasons.append("max_entries reached")
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
                http_method=candidate.http_method,  # type: ignore[arg-type]
                declared_path=candidate.declared_path,
                handler_symbol=candidate.handler_symbol,
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

    has_fastapi_routes = any(candidate.kind == "fastapi_route" for candidate in candidates)
    if not candidates and not _deadline_reached(deadline):
        notes = [
            "未识别入口；这不表示项目没有入口。",
            "当前规则覆盖 __main__.py、顶层 __name__ == __main__ 条件、可静态确认的 FastAPI 路由，以及 pluggy hookimpl 标记或与快照内 hookspec 同名的插件钩子。",
        ]
    else:
        notes = [
            "当前规则覆盖 __main__.py、顶层 __name__ == __main__ 条件、可静态确认的 FastAPI 路由，以及 pluggy hookimpl 标记或与快照内 hookspec 同名的插件钩子。",
            (
                "FastAPI 入口显示声明路径；未解析 APIRouter、挂载前缀或动态注册，因此不能视为最终 URL。"
                if has_fastapi_routes
                else "FastAPI 入口仅接受已知 FastAPI 实例、字面量路径和静态方法；未识别路由不表示项目没有入口。"
            ),
            "pluggy 钩子由运行时 pm.hook 分发，入口只表示可能被插件管理器调用；pyproject/setup.cfg/setup.py entry-points 元数据和运行时 register 尚未纳入入口规则。",
        ]
    if cycle_skipped:
        notes.append("循环调用只保留首次有界路径，未继续展开循环。")
    if relation.unresolved_truncated is None:
        notes.extend(unresolved_limitations)

    for request_fact in relation.http_client_requests:
        if request_fact.unknown_reason is not None or request_fact.method is None or request_fact.request_path is None:
            notes.append("HTTP method/path 形状匹配只覆盖静态 requestJson；动态 URL/method 保留 unknown。")
            break
    if relation.http_client_requests:
        notes.append("HTTP method/path 形状关联不证明请求实际发往本地服务；apiBaseUrl 可配置为远端。")

    rules_version = index.rules_version or ""
    path_analysis = (
        "parameterized"
        if "+typescript-facts-v2" in rules_version or "+typescript-facts-v3" in rules_version or
        "+typescript-facts-v4" in rules_version or "+typescript-facts-v5" in rules_version
        else "literal_only"
        if "+typescript-facts-v1" in rules_version
        else "not_analyzed"
    )
    if path_analysis == "not_analyzed":
        notes.append("此索引未记录 TypeScript 静态分析；HTTP 关联 coverage 为 not_analyzed。")
    elif path_analysis == "literal_only":
        notes.append("此旧索引只保留字面量 HTTP 路径分析；参数化路径尚未分析。")
    elif index.status != "ready":
        notes.append("索引未完整就绪；HTTP 关联 coverage 为 incomplete。")

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

    (
        architecture_classifications,
        architecture_classification_coverage,
        classification_counts_by_module,
        classification_incomplete,
    ) = _build_architecture_classifications(
        relation=relation,
        entries=entries,
        modules=modules,
        visible_module_ids={module.symbol_id for module in visible_modules},
        read_evidence=read_evidence,
        deadline=deadline,
        entry_candidates_truncated=entry_candidates_truncated,
    )
    notes.append(
        "架构分类只说明当前固定规则识别到的入口、__all__ 公共 API 与 pluggy hookspec；"
        "未识别不表示无用、未引用或运行时不可达。"
    )
    if any(module.path.lower().endswith((".ts", ".tsx")) for module in modules):
        notes.append(
            "此索引的持久化读取不包含 TypeScript export facts；TypeScript 导出保留为未知，本规则不额外解析。"
        )
    if classification_incomplete:
        notes.append(
            "架构分类结果受固定条目、证据、扫描或模块预算限制；coverage 区分观察到的总量和可见量。"
        )

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
            classification_counts=classification_counts_by_module.get(
                module.symbol_id, OverviewClassificationCounts()
            ),
        )
        for module in visible_modules
    ]
    responsibility_status: str = "not_configured"
    responsibility_specs: tuple[SubsystemSpec, ...] = ()
    responsibility_evidence: Evidence | None = None
    responsibility_error: str | None = None
    manifest_file = next(
        (source_file for source_file in relation.snapshot.files if source_file.path == SUBSYSTEM_MANIFEST_PATH),
        None,
    )
    manifest_issue = next(
        (issue for issue in relation.snapshot.issues if issue.kind == "manifest_invalid"),
        None,
    )
    if manifest_issue is not None:
        responsibility_status = "invalid"
        responsibility_error = manifest_issue.message
    elif manifest_file is not None:
        try:
            responsibility_specs = parse_subsystem_manifest(manifest_file.text)
            responsibility_status = "ready"
        except SubsystemManifestError as exc:
            responsibility_status = "invalid"
            responsibility_error = f"manifest invalid: {exc.code}"
    if manifest_file is not None:
        responsibility_evidence = _read_manifest_evidence(
            index_id=index.index_id,
            source_file=manifest_file,
            read_evidence=read_evidence,
        )
    if responsibility_status == "invalid":
        notes.append(
            f"职责 manifest 无效（{SUBSYSTEM_MANIFEST_PATH}）：{responsibility_error or 'manifest invalid'}；"
            "本次完整回退到源码目录分组。"
        )
        if manifest_file is not None and responsibility_evidence is None:
            notes.append("manifest 来源文件已固定在快照中，但其 bounded excerpt 超出证据上限，不能生成可打开的证据片段。")
    elif responsibility_status == "ready" and responsibility_evidence is None:
        notes.append("职责 manifest 已固定在快照中，但其 bounded excerpt 超出证据上限，不能生成可打开的证据片段。")

    groups, total_groups, uncovered_module_count, unmatched_owner_count = _build_overview_groups(
        index_id=index.index_id,
        entries=entries,
        modules=modules,
        module_entry_ids=module_entry_ids,
        reachable_module_ids=reachable_module_ids,
        module_by_path=module_by_path,
        max_groups=max_groups,
        responsibility_specs=responsibility_specs,
    )
    if responsibility_specs:
        if uncovered_module_count or unmatched_owner_count:
            responsibility_status = "partial"
            notes.append(
                f"职责覆盖不完整：{uncovered_module_count} 个未归属模块保留在源码目录 fallback，"
                f"{unmatched_owner_count} 个 owner 路径未命中已索引模块。"
            )
    if total_groups > len(groups):
        reasons.append("max_groups reached")

    (
        module_relations,
        total_module_relations,
        relation_sample_truncated,
        relation_timed_out,
    ) = _build_module_relations(
        index_id=index.index_id,
        relation=relation,
        symbols_by_id=symbols_by_id,
        module_by_path=module_by_path,
        max_relations=max_module_relations,
        deadline=deadline,
    )
    if total_module_relations > len(module_relations) and not relation_timed_out:
        reasons.append("max_module_relations reached")
    if relation_sample_truncated:
        reasons.append("module relation edge samples bounded")
    if _deadline_reached(deadline):
        reasons.append("max_seconds reached")
    http_associations, http_association_coverage, http_associations_truncated = _build_http_associations(
        index=index,
        relation=relation,
        entries=entries,
        module_by_path=module_by_path,
        read_evidence=read_evidence,
        deadline=deadline,
        route_candidates_truncated=entry_candidates_truncated,
        analysis_status=(
            "not_analyzed"
            if path_analysis == "not_analyzed"
            else "incomplete"
            if index.status != "ready" or relation.truncated or relation.unresolved_truncated is True or entry_candidates_truncated
            else "complete"
        ),
        path_analysis=path_analysis,
    )
    if http_associations_truncated:
        reasons.append("max_http_associations reached")
    if _deadline_reached(deadline):
        reasons.append("max_seconds reached")
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
        responsibility_status=responsibility_status,  # type: ignore[arg-type]
        responsibility_evidence=responsibility_evidence,
        entries=entries,
        groups=groups,
        modules=module_models,
        classifications=architecture_classifications,
        classification_coverage=architecture_classification_coverage,
        module_relations=module_relations,
        http_associations=http_associations,
        http_association_coverage=http_association_coverage,
        paths=paths,
        unresolved=visible_unresolved,
        coverage=OverviewCoverage(
            total_modules=total_modules,
            visible_modules=len(visible_modules),
            reachable_modules=total_reachable_modules,
            unreachable_modules=total_modules - total_reachable_modules,
            entry_point_count=len(entries),
            unresolved_count=len(visible_unresolved),
            total_groups=total_groups,
            visible_groups=len(groups),
            total_module_relations=total_module_relations,
            visible_module_relations=len(module_relations),
        ),
        notes=notes,
        truncated=truncated,
        truncation_reason=truncation_reason,
    )


def _build_http_associations(
    *,
    index,
    relation: RelationIndex,
    entries: list[OverviewEntryPoint],
    module_by_path: dict[str, Symbol],
    read_evidence: EvidenceReader,
    deadline: float,
    route_candidates_truncated: bool,
    analysis_status: str,
    path_analysis: str,
) -> tuple[list[OverviewHttpAssociation], OverviewHttpAssociationCoverage, bool]:
    routes = [entry for entry in entries if entry.kind == "fastapi_route"]
    requests = sorted(
        relation.http_client_requests,
        key=lambda item: (item.source_path, item.start_line, item.end_line, item.request_id),
    )
    associations: list[OverviewHttpAssociation] = []
    matched = unmatched = ambiguous = unknown = 0
    timed_out = False
    for request in requests:
        if _deadline_reached(deadline):
            unknown += len(requests) - matched - unmatched - ambiguous - unknown
            timed_out = True
            break
        if request.unknown_reason or request.method is None or (
            request.path_kind == "literal" and request.request_path is None
        ) or (
            request.path_kind == "parameterized" and request.request_path_template is None
        ):
            unknown += 1
            continue
        source_module = module_by_path.get(request.source_path)
        if source_module is None:
            unknown += 1
            continue
        if route_candidates_truncated:
            # An omitted route may duplicate this path/method, so a visible
            # single candidate is not proof of a unique association.
            unknown += 1
            continue
        candidates = [entry for entry in routes if entry.http_method == request.method]
        if path_analysis == "literal_only":
            candidates = [
                entry for entry in candidates
                if entry.declared_path == request.request_path
            ]
            full_candidates = candidates
            unsupported_overlap = False
        else:
            client_segments = _http_client_path_segments(request)
            if client_segments is None:
                unknown += 1
                continue
            overlaps: list[OverviewEntryPoint] = []
            full_candidates = []
            unsupported_overlap = False
            for candidate in candidates:
                route_segments = _http_route_path_segments(candidate.declared_path or "")
                if route_segments is None:
                    unsupported_overlap = unsupported_overlap or _http_unsupported_route_overlaps(
                        client_segments,
                        candidate.declared_path or "",
                    )
                    continue
                if not _http_paths_overlap(client_segments, route_segments):
                    continue
                overlaps.append(candidate)
                if _http_path_shape_is_full_match(client_segments, route_segments, request.path_kind):
                    full_candidates.append(candidate)
            if unsupported_overlap:
                unknown += 1
                continue
            candidates = overlaps
            if len(candidates) > 1:
                ambiguous += 1
                continue
            if request.path_kind == "parameterized" and candidates and not full_candidates:
                # A route may overlap only one value of the client's template.
                # That cannot represent the full static client path shape.
                unknown += 1
                continue
        if len(candidates) > 1 or len(full_candidates) > 1:
            ambiguous += 1
            continue
        if not candidates:
            unmatched += 1
            continue
        if len(full_candidates) != 1:
            unknown += 1
            continue
        route = full_candidates[0]
        handler = route.handler_symbol
        server_module = module_by_path.get(handler.path) if handler is not None else None
        if server_module is None:
            unknown += 1
            continue
        if len(associations) >= _MAX_HTTP_ASSOCIATIONS:
            matched += 1
            continue
        try:
            client_evidence = read_evidence(
                index.index_id,
                request.source_path,
                request.start_line,
                request.end_line,
            )
            route_evidence = route.evidence or read_evidence(
                index.index_id,
                route.path,
                route.start_line,
                min(route.end_line, route.start_line + _MAX_EVIDENCE_LINES - 1),
            )
        except Exception:
            unknown += 1
            continue
        matched += 1
        association_identity = "\0".join(
            [index.index_id, request.request_id, route.entry_point_id, "method_path_shape"]
        )
        associations.append(
            OverviewHttpAssociation(
                association_id=f"http-association-{hashlib.sha256(association_identity.encode('utf-8')).hexdigest()[:32]}",
                index_id=index.index_id,
                method=request.method,
                path=route.declared_path or "",
                client_path_kind=request.path_kind,
                client_path_template=request.request_path_template,
                client_name=request.client_name,
                client_module=source_module,
                server_module=server_module,
                route_entry_point_id=route.entry_point_id,
                client_evidence=client_evidence,
                route_evidence=route_evidence,
            )
        )
    truncated = matched > len(associations)
    coverage_status = "incomplete" if timed_out or truncated else analysis_status
    return (
        associations,
        OverviewHttpAssociationCoverage(
            analysis_status=coverage_status,  # type: ignore[arg-type]
            path_analysis=path_analysis,  # type: ignore[arg-type]
            client_request_count=len(requests),
            matched_count=matched,
            visible_match_count=len(associations),
            unmatched_count=unmatched,
            ambiguous_count=ambiguous,
            unknown_count=unknown,
        ),
        truncated or timed_out,
    )


_FASTAPI_PATH_PARAMETER = re.compile(r"^\{[A-Za-z_][A-Za-z0-9_]*\}$")


def _http_client_path_segments(request) -> tuple[str | None, ...] | None:
    path = request.request_path if request.path_kind == "literal" else request.request_path_template
    if path is None or not path.startswith("/api/") or any(marker in path for marker in "?#"):
        return None
    segments = path.split("/")
    if request.path_kind == "literal":
        return tuple(segments)
    if any("{*" in segment and segment != "{*}" for segment in segments):
        return None
    if any("{" in segment or "}" in segment for segment in segments if segment != "{*}"):
        return None
    return tuple(None if segment == "{*}" else segment for segment in segments)


def _http_route_path_segments(path: str) -> tuple[str | None, ...] | None:
    if not path.startswith("/api/") or any(marker in path for marker in "?#"):
        return None
    segments = path.split("/")
    result: list[str | None] = []
    for segment in segments:
        if "{" in segment or "}" in segment:
            if not _FASTAPI_PATH_PARAMETER.fullmatch(segment):
                return None
            result.append(None)
        else:
            result.append(segment)
    return tuple(result)


def _http_unsupported_route_overlaps(
    client: tuple[str | None, ...],
    path: str,
) -> bool:
    if not path.startswith("/api/"):
        return False
    route_segments = path.split("/")
    prefix: list[str] = []
    for segment in route_segments:
        if "{" in segment or "}" in segment:
            break
        prefix.append(segment)
    if len(client) < len(prefix):
        return False
    return all(
        client_segment is None or client_segment == route_segment
        for client_segment, route_segment in zip(client, prefix, strict=False)
    )


def _http_paths_overlap(
    client: tuple[str | None, ...],
    route: tuple[str | None, ...],
) -> bool:
    return len(client) == len(route) and all(
        client_segment is None or route_segment is None or client_segment == route_segment
        for client_segment, route_segment in zip(client, route, strict=True)
    )


def _http_path_shape_is_full_match(
    client: tuple[str | None, ...],
    route: tuple[str | None, ...],
    path_kind: str,
) -> bool:
    if not _http_paths_overlap(client, route):
        return False
    if path_kind == "parameterized":
        return all(
            (client_segment is None) == (route_segment is None)
            for client_segment, route_segment in zip(client, route, strict=True)
        )
    return True


def _build_overview_groups(
    *,
    index_id: str,
    entries: list[OverviewEntryPoint],
    modules: tuple[Symbol, ...],
    module_entry_ids: dict[str, list[str]],
    reachable_module_ids: set[str],
    module_by_path: dict[str, Symbol],
    max_groups: int,
    responsibility_specs: tuple[SubsystemSpec, ...] = (),
) -> tuple[list[OverviewGroup], int, int, int]:
    http_entry_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    http_module_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    http_module_labels: dict[tuple[str, str], str] = {}
    for entry in entries:
        if entry.kind != "fastapi_route" or entry.declared_path is None:
            continue
        handler = entry.handler_symbol
        handler_module = module_by_path.get(handler.path) if handler is not None else None
        module_key = (
            handler_module.symbol_id
            if handler_module is not None
            else f"unknown:{handler.path if handler is not None else entry.path}"
        )
        group_key = _http_path_prefix(entry.declared_path)
        group_identity = (group_key, module_key)
        http_entry_ids[group_identity].add(entry.entry_point_id)
        if handler_module is not None:
            http_module_ids[group_identity].add(handler_module.symbol_id)
            http_module_labels[group_identity] = handler_module.qualified_name
        elif handler is not None:
            http_module_labels[group_identity] = handler.path
        else:
            http_module_labels[group_identity] = "unknown module"

    specs: list[_OverviewGroupSpec] = []
    for (group_key, module_key), entry_ids in http_entry_ids.items():
        module_ids = tuple(sorted(http_module_ids[(group_key, module_key)]))
        module_label = http_module_labels[(group_key, module_key)]
        specs.append(
            _OverviewGroupSpec(
                group_kind="http_path",
                group_key=group_key,
                label=f"HTTP {group_key} · {module_label}",
                module_ids=module_ids,
                entry_point_ids=tuple(sorted(entry_ids)),
                sort_key=(1 if responsibility_specs else 0, group_key, module_label, module_key),
            )
        )

    responsibility_module_ids: dict[str, list[str]] = defaultdict(list)
    module_responsibility: dict[str, str] = {}
    matched_owner_paths: set[str] = set()
    for spec in responsibility_specs:
        for module in modules:
            matching_owner = next((owner for owner in spec.owners if _owner_matches_module(owner, module.path)), None)
            if matching_owner is None:
                continue
            module_responsibility[module.symbol_id] = spec.subsystem_id
            responsibility_module_ids[spec.subsystem_id].append(module.symbol_id)
            matched_owner_paths.add(matching_owner.path)
    for spec in responsibility_specs:
        module_ids = tuple(sorted(responsibility_module_ids.get(spec.subsystem_id, [])))
        specs.append(
            _OverviewGroupSpec(
                group_kind="responsibility",
                group_key=spec.subsystem_id,
                label=spec.label,
                module_ids=module_ids,
                entry_point_ids=(),
                sort_key=(0, spec.subsystem_id),
            )
        )

    source_modules: dict[str, list[Symbol]] = defaultdict(list)
    for module in modules:
        if module.symbol_id in module_responsibility:
            continue
        source_modules[_source_directory_key(module.path)].append(module)
    for directory, directory_modules in source_modules.items():
        directory_modules.sort(
            key=lambda symbol: (
                symbol.symbol_id not in reachable_module_ids,
                symbol.path,
                symbol.qualified_name,
                symbol.symbol_id,
            )
        )
        module_ids = tuple(module.symbol_id for module in directory_modules)
        entry_ids = tuple(
            sorted(
                {
                    entry_id
                    for module in directory_modules
                    for entry_id in module_entry_ids.get(module.symbol_id, [])
                }
            )
        )
        specs.append(
            _OverviewGroupSpec(
                group_kind="source_directory",
                group_key=directory,
                label=f"SOURCE {directory}",
                module_ids=module_ids,
                entry_point_ids=entry_ids,
                sort_key=(
                    2 if responsibility_specs else 1,
                    not any(module.symbol_id in reachable_module_ids for module in directory_modules),
                    directory,
                ),
            )
        )

    specs.sort(key=lambda spec: spec.sort_key)
    groups: list[OverviewGroup] = []
    for spec in specs[:max_groups]:
        identity = "\0".join(
            (
                RULES_VERSION,
                index_id,
                spec.group_kind,
                spec.group_key,
                *spec.module_ids,
                *spec.entry_point_ids,
            )
        ).encode("utf-8")
        groups.append(
            OverviewGroup(
                group_id=f"group-{hashlib.sha256(identity).hexdigest()[:32]}",
                index_id=index_id,
                group_kind=spec.group_kind,  # type: ignore[arg-type]
                group_key=spec.group_key,
                label=spec.label,
                module_ids=list(spec.module_ids),
                entry_point_ids=list(spec.entry_point_ids),
            )
        )
    unmatched_owners = sum(
        1
        for subsystem in responsibility_specs
        for owner in subsystem.owners
        if owner.path not in matched_owner_paths
    )
    uncovered_modules = sum(1 for module in modules if module.symbol_id not in module_responsibility)
    return groups, len(specs), uncovered_modules, unmatched_owners


def _build_architecture_classifications(
    *,
    relation: RelationIndex,
    entries: list[OverviewEntryPoint],
    modules: tuple[Symbol, ...],
    visible_module_ids: set[str],
    read_evidence: EvidenceReader,
    deadline: float,
    entry_candidates_truncated: bool,
) -> tuple[
    list[OverviewClassification],
    OverviewClassificationCoverage,
    dict[str, OverviewClassificationCounts],
    bool,
]:
    index_id = relation.snapshot.index.index_id
    module_by_path = {module.path: module for module in modules}
    module_by_id = {module.symbol_id: module for module in modules}
    symbols_by_id = {symbol.symbol_id: symbol for symbol in relation.symbols}
    symbols_by_path: dict[str, list[Symbol]] = defaultdict(list)
    functions_by_location: dict[tuple[str, int, int], list[Symbol]] = defaultdict(list)
    for symbol in relation.symbols:
        symbols_by_path[symbol.path].append(symbol)
        if symbol.kind == "function":
            functions_by_location[(symbol.path, symbol.start_line, symbol.end_line)].append(symbol)
    for symbol_list in symbols_by_path.values():
        symbol_list.sort(key=lambda item: (item.qualified_name, item.symbol_id))
    for function_list in functions_by_location.values():
        function_list.sort(key=lambda item: (item.qualified_name, item.symbol_id))

    specs: list[_ArchitectureClassificationSpec] = []
    incomplete = entry_candidates_truncated
    for entry in entries:
        target = entry.handler_symbol or symbols_by_id.get(entry.symbol_id or "")
        module = module_by_path.get(target.path) if target is not None else module_by_path.get(entry.path)
        if module is None:
            continue
        specs.append(
            _ArchitectureClassificationSpec(
                category="application_entry",
                module_id=module.symbol_id,
                symbol_id=target.symbol_id if target is not None else None,
                symbol_name=target.qualified_name if target is not None else module.qualified_name,
                label=entry.label,
                rule_id=entry.rule_id,
                entry_point_id=entry.entry_point_id,
            )
        )

    import_edges: dict[tuple[str, int], list[Symbol]] = defaultdict(list)
    for edge in relation.edges:
        if edge.kind != "imports":
            continue
        target = symbols_by_id.get(edge.target_id)
        if target is not None:
            import_edges[(edge.source_id, edge.source_location.start_line)].append(target)

    files_by_path = {source_file.path: source_file for source_file in relation.snapshot.files}
    for source_file in sorted(relation.snapshot.files, key=lambda item: item.path):
        if _deadline_reached(deadline) or len(specs) >= _MAX_CLASSIFICATION_SCAN:
            incomplete = True
            break
        if not source_file.path.lower().endswith(".py"):
            continue
        module = module_by_path.get(source_file.path)
        if module is None:
            continue
        try:
            tree = ast.parse(source_file.text, filename=source_file.path, type_comments=True)
        except SyntaxError:
            incomplete = True
            continue

        exports = _static_all_exports(tree)
        if exports is not None:
            bound_names = _module_bound_names(tree)
            direct_symbols: dict[str, list[Symbol]] = defaultdict(list)
            module_prefix = f"{module.qualified_name}."
            for symbol in symbols_by_path.get(source_file.path, []):
                if symbol.kind not in {"function", "class", "module"}:
                    continue
                suffix = symbol.qualified_name.removeprefix(module_prefix)
                if suffix and "." not in suffix:
                    direct_symbols[suffix].append(symbol)
            import_targets: dict[str, list[tuple[Symbol, int]]] = defaultdict(list)
            for statement in tree.body:
                if not isinstance(statement, ast.ImportFrom):
                    continue
                for alias in statement.names:
                    if alias.name == "*":
                        continue
                    local_name = alias.asname or alias.name
                    targets = [
                        target
                        for target in import_edges.get((module.symbol_id, statement.lineno), [])
                        if target.qualified_name.rsplit(".", 1)[-1] == alias.name
                    ]
                    for target in targets:
                        import_targets[local_name].append((target, statement.lineno))

            for export_name, export_line in exports if not bound_names.get("*", 0) else ():
                if _deadline_reached(deadline):
                    incomplete = True
                    break
                if bound_names.get(export_name, 0) != 1:
                    continue
                direct = direct_symbols.get(export_name, [])
                imported = import_targets.get(export_name, [])
                if len(direct) == 1 and not imported:
                    target = direct[0]
                    locations = ((source_file.path, export_line, export_line),)
                elif len(imported) == 1 and not direct:
                    target, import_line = imported[0]
                    locations = (
                        (source_file.path, export_line, export_line),
                        (source_file.path, import_line, import_line),
                    )
                else:
                    continue
                specs.append(
                    _ArchitectureClassificationSpec(
                        category="public_api",
                        module_id=module.symbol_id,
                        symbol_id=target.symbol_id,
                        symbol_name=target.qualified_name,
                        label=export_name,
                        rule_id=(
                            "python.module.__all__.direct"
                            if len(direct) == 1
                            else "python.module.__all__.import_alias"
                        ),
                        evidence_locations=locations,
                    )
                )
                if len(specs) >= _MAX_CLASSIFICATION_SCAN:
                    incomplete = True
                    break

        specs.extend(
            _find_hookspec_classifications(
                source_file.path,
                module,
                tree,
                functions_by_location,
                deadline,
            )
        )
        if len(specs) >= _MAX_CLASSIFICATION_SCAN:
            incomplete = True
            break

    known_module_ids = {
        item.module_id
        for item in specs
        if item.category in {"application_entry", "public_api", "extension_point"}
    }
    for module in modules:
        if _deadline_reached(deadline) or len(specs) >= _MAX_CLASSIFICATION_SCAN:
            incomplete = True
            break
        if module.symbol_id in known_module_ids:
            continue
        source_file = files_by_path.get(module.path)
        first_line = 1
        if source_file is not None:
            first_line = next(
                (line_number for line_number, line in enumerate(source_file.text.splitlines(), start=1) if line.strip()),
                1,
            )
        specs.append(
            _ArchitectureClassificationSpec(
                category="unknown",
                module_id=module.symbol_id,
                symbol_id=None,
                symbol_name=module.qualified_name,
                label="当前规则未识别公开 API 或扩展点",
                rule_id="architecture.unmatched_module_rules",
                evidence_locations=((module.path, first_line, first_line),),
            )
        )

    category_order = {"extension_point": 0, "public_api": 1, "application_entry": 2, "unknown": 3}
    specs.sort(
        key=lambda item: (
            category_order[item.category],
            module_by_id[item.module_id].path,
            item.label,
            item.entry_point_id or "",
            item.evidence_locations,
        )
    )
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"application_entry": 0, "public_api": 0, "extension_point": 0, "unknown": 0}
    )
    category_counts = {"application_entry": 0, "public_api": 0, "extension_point": 0, "unknown": 0}
    for item in specs:
        counts[item.module_id][item.category] += 1
        category_counts[item.category] += 1
    visible_specs = [item for item in specs if item.module_id in visible_module_ids]
    if len(visible_specs) != len(specs):
        incomplete = True
    if len(visible_specs) > _MAX_CLASSIFICATION_ITEMS:
        visible_specs = visible_specs[:_MAX_CLASSIFICATION_ITEMS]
        incomplete = True

    visible_category_counts = {"application_entry": 0, "public_api": 0, "extension_point": 0, "unknown": 0}
    for item in visible_specs:
        visible_category_counts[item.category] += 1
    classification_counts_by_module = {
        module_id: OverviewClassificationCounts(**values)
        for module_id, values in counts.items()
    }

    result: list[OverviewClassification] = []
    evidence_lines_used = 0
    evidence_chars_used = 0
    for item in visible_specs:
        if _deadline_reached(deadline):
            incomplete = True
            break
        evidence: list[Evidence] = []
        evidence_error: str | None = None
        for path, start_line, end_line in item.evidence_locations[:4]:
            line_count = end_line - start_line + 1
            if evidence_lines_used + line_count > _MAX_CLASSIFICATION_EVIDENCE_LINES:
                evidence_error = "classification evidence line budget reached"
                incomplete = True
                break
            try:
                source_evidence = read_evidence(index_id, path, start_line, end_line)
                if evidence_chars_used + len(source_evidence.excerpt) > _MAX_CLASSIFICATION_EVIDENCE_CHARS:
                    evidence_error = "classification evidence character budget reached"
                    incomplete = True
                    break
                evidence.append(source_evidence)
                evidence_lines_used += line_count
                evidence_chars_used += len(source_evidence.excerpt)
            except Exception:
                evidence_error = "classification source evidence is unavailable in the persisted snapshot"
        if item.evidence_locations and not evidence and evidence_error is None:
            evidence_error = "classification source evidence is unavailable in the persisted snapshot"
        if evidence_error is not None:
            incomplete = True
        identity_parts = [
            RULES_VERSION,
            index_id,
            item.category,
            item.module_id,
            item.symbol_id or "",
            item.rule_id,
            item.entry_point_id or "",
            item.label,
            *(f"{path}:{start}:{end}" for path, start, end in item.evidence_locations),
        ]
        classification_id = "classification-" + hashlib.sha256(
            "\0".join(identity_parts).encode("utf-8")
        ).hexdigest()[:32]
        result.append(
            OverviewClassification(
                classification_id=classification_id,
                index_id=index_id,
                category=item.category,  # type: ignore[arg-type]
                module_id=item.module_id,
                symbol_id=item.symbol_id,
                symbol_name=item.symbol_name,
                label=item.label,
                rule_id=item.rule_id,
                entry_point_id=item.entry_point_id,
                evidence=evidence,
                evidence_error=evidence_error,
            )
        )
    if len(result) != len(visible_specs):
        incomplete = True

    coverage = OverviewClassificationCoverage(
        total=len(specs),
        visible=len(result),
        category_counts=OverviewClassificationCounts(**category_counts),
        visible_category_counts=OverviewClassificationCounts(
            **{
                category: sum(1 for item in result if item.category == category)
                for category in category_order
            }
        ),
        truncated=incomplete,
    )
    return result, coverage, classification_counts_by_module, incomplete


def _static_all_exports(tree: ast.Module) -> list[tuple[str, int]] | None:
    class AllWrites(ast.NodeVisitor):
        def __init__(self) -> None:
            self.nodes: list[ast.Name] = []
            self.loads: list[ast.Name] = []

        def visit_Name(self, node: ast.Name) -> None:
            if node.id == "__all__" and isinstance(node.ctx, (ast.Store, ast.Del)):
                self.nodes.append(node)
            elif node.id == "__all__" and isinstance(node.ctx, ast.Load):
                self.loads.append(node)

    writes = AllWrites()
    writes.visit(tree)
    assignments: list[tuple[ast.Name, ast.expr]] = []
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            if len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name) and statement.targets[0].id == "__all__":
                assignments.append((statement.targets[0], statement.value))
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name) and statement.target.id == "__all__":
            if statement.value is not None:
                assignments.append((statement.target, statement.value))
    if (
        len(assignments) != 1
        or len(writes.nodes) != 1
        or writes.nodes[0] is not assignments[0][0]
        or writes.loads
    ):
        return None
    value = assignments[0][1]
    if not isinstance(value, (ast.List, ast.Tuple)):
        return None
    names: list[tuple[str, int]] = []
    seen: set[str] = set()
    for element in value.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str) or not element.value:
            return None
        if element.value not in seen:
            names.append((element.value, element.lineno))
            seen.add(element.value)
    return names


def _module_bound_names(tree: ast.Module) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for statement in tree.body:
        writes = _BindingWriteCollector()
        writes.visit(statement)
        for name, count in writes.write_counts.items():
            counts[name] += count
        if writes.invalidate_all:
            counts["*"] += 1
    return counts


def _find_hookspec_classifications(
    path: str,
    module: Symbol,
    tree: ast.Module,
    functions_by_location: dict[tuple[str, int, int], list[Symbol]],
    deadline: float,
) -> list[_ArchitectureClassificationSpec]:
    marker_classes: dict[str, tuple[tuple[str, int, int], ...]] = {}
    marker_modules: dict[str, tuple[str, tuple[str, int, int]]] = {}
    marker_instances: dict[str, tuple[tuple[str, int, int], ...]] = {}
    result: list[_ArchitectureClassificationSpec] = []

    def clear(name: str) -> None:
        marker_classes.pop(name, None)
        marker_modules.pop(name, None)
        marker_instances.pop(name, None)

    def clear_writes(statement: ast.stmt) -> None:
        writes = _BindingWriteCollector()
        writes.visit(statement)
        if writes.invalidate_all:
            marker_classes.clear()
            marker_modules.clear()
            marker_instances.clear()
        for name in writes.names:
            clear(name)

    for statement in tree.body:
        if _deadline_reached(deadline):
            break
        if isinstance(statement, ast.ImportFrom):
            resolved_module = _resolved_python_import_module(module, path, statement.level, statement.module)
            for alias in statement.names:
                if alias.name == "*":
                    marker_classes.clear()
                    marker_modules.clear()
                    marker_instances.clear()
                    continue
                local_name = alias.asname or alias.name
                clear(local_name)
                if resolved_module in {"pluggy", "pluggy._hooks"} and alias.name == "HookspecMarker":
                    marker_classes[local_name] = ((path, statement.lineno, statement.lineno),)
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                local_name = alias.asname or alias.name.split(".", 1)[0]
                clear(local_name)
                if alias.name in {"pluggy", "pluggy._hooks"}:
                    marker_modules[local_name] = (alias.name, (path, statement.lineno, statement.lineno))
        elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
            value = statement.value
            target_nodes = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            simple_target = (
                len(target_nodes) == 1 and isinstance(target_nodes[0], ast.Name)
            )
            writes = _BindingWriteCollector()
            writes.visit(statement)
            if writes.invalidate_all:
                marker_classes.clear()
                marker_modules.clear()
                marker_instances.clear()
            names = writes.names
            binding_kind = "unknown"
            trace: tuple[tuple[str, int, int], ...] = ()
            value_name = value.id if isinstance(value, ast.Name) else None
            if simple_target and value_name in marker_instances:
                binding_kind = "instance"
                trace = (*marker_instances[value_name], (path, statement.lineno, statement.lineno))
            elif simple_target and value_name in marker_classes:
                binding_kind = "class"
                trace = (*marker_classes[value_name], (path, statement.lineno, statement.lineno))
            elif simple_target and isinstance(value, ast.Call):
                function_name = value.func.id if isinstance(value.func, ast.Name) else None
                if function_name in marker_classes:
                    binding_kind = "instance"
                    trace = (*marker_classes[function_name], (path, statement.lineno, statement.lineno))
                elif isinstance(value.func, ast.Attribute) and value.func.attr == "HookspecMarker":
                    base_name = value.func.value.id if isinstance(value.func.value, ast.Name) else None
                    module_binding = marker_modules.get(base_name or "")
                    if module_binding is not None:
                        binding_kind = "instance"
                        trace = (module_binding[1], (path, statement.lineno, statement.lineno))
            for name in names:
                clear(name)
                if binding_kind == "instance":
                    marker_instances[name] = _compact_hookspec_trace(trace)
                elif binding_kind == "class":
                    marker_classes[name] = _compact_hookspec_trace(trace)
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end_line = getattr(statement, "end_lineno", None) or statement.lineno
            symbols = functions_by_location.get((path, statement.lineno, end_line), [])
            for decorator in statement.decorator_list:
                decorated_name = (
                    decorator.id
                    if isinstance(decorator, ast.Name)
                    else decorator.func.id
                    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name)
                    else None
                )
                trace = marker_instances.get(decorated_name or "")
                if trace is None or len(symbols) != 1:
                    continue
                locations = tuple(
                    dict.fromkeys((*trace, (path, decorator.lineno, decorator.lineno)))
                )
                symbol = symbols[0]
                result.append(
                    _ArchitectureClassificationSpec(
                        category="extension_point",
                        module_id=module.symbol_id,
                        symbol_id=symbol.symbol_id,
                        symbol_name=symbol.qualified_name,
                        label=symbol.qualified_name.rsplit(".", 1)[-1],
                        rule_id="python.pluggy.hookspec_marker",
                        evidence_locations=locations,
                    )
                )
                break
            clear_writes(statement)
        elif isinstance(statement, ast.ClassDef):
            clear_writes(statement)
        else:
            clear_writes(statement)
    return result


def _compact_hookspec_trace(
    locations: tuple[tuple[str, int, int], ...],
) -> tuple[tuple[str, int, int], ...]:
    if len(locations) <= 3:
        return locations
    return (locations[0], *locations[-2:])


def _resolved_python_import_module(
    module: Symbol,
    path: str,
    level: int,
    imported_module: str | None,
) -> str | None:
    if level == 0:
        return imported_module
    package = module.qualified_name if path.rsplit("/", 1)[-1] == "__init__.py" else module.qualified_name.rpartition(".")[0]
    parts = package.split(".") if package else []
    remove = level - 1
    if remove > len(parts):
        return None
    base = ".".join(parts[: len(parts) - remove] if remove else parts)
    return f"{base}.{imported_module}" if base and imported_module else base or imported_module


def _owner_matches_module(owner: SubsystemOwner, module_path: str) -> bool:
    return module_path.startswith(owner.path) if owner.directory_prefix else module_path == owner.path


def _read_manifest_evidence(
    *,
    index_id: str,
    source_file: SnapshotFile,
    read_evidence: EvidenceReader,
) -> Evidence | None:
    """Return a bounded prefix excerpt while preserving the full captured file hash."""

    lines = source_file.text.splitlines()
    selected_lines = 0
    excerpt_bytes = 0
    for line in lines[:_MAX_EVIDENCE_LINES]:
        line_bytes = len(line.encode("utf-8"))
        candidate_bytes = excerpt_bytes + line_bytes + (1 if selected_lines else 0)
        if candidate_bytes > 50_000:
            break
        selected_lines += 1
        excerpt_bytes = candidate_bytes
    if selected_lines == 0 or not "\n".join(lines[:selected_lines]).strip():
        return None
    return read_evidence(index_id, SUBSYSTEM_MANIFEST_PATH, 1, selected_lines)


def _build_module_relations(
    *,
    index_id: str,
    relation: RelationIndex,
    symbols_by_id: dict[str, Symbol],
    module_by_path: dict[str, Symbol],
    max_relations: int,
    deadline: float,
) -> tuple[list[OverviewModuleRelation], int, bool, bool]:
    edges_by_module_pair: dict[tuple[str, str], list[CodeEdge]] = defaultdict(list)
    module_by_id = {module.symbol_id: module for module in module_by_path.values()}
    timed_out = False
    for edge in relation.edges:
        if _deadline_reached(deadline):
            timed_out = True
            break
        if edge.kind not in {"imports", "calls"}:
            continue
        source = symbols_by_id.get(edge.source_id)
        target = symbols_by_id.get(edge.target_id)
        if source is None or target is None:
            continue
        source_module = module_by_path.get(source.path)
        target_module = module_by_path.get(target.path)
        if source_module is None or target_module is None or source_module.symbol_id == target_module.symbol_id:
            continue
        edges_by_module_pair[(source_module.symbol_id, target_module.symbol_id)].append(edge)

    module_pairs = sorted(
        edges_by_module_pair,
        key=lambda pair: (
            module_by_id[pair[0]].qualified_name,
            module_by_id[pair[1]].qualified_name,
            pair,
        ),
    )
    relations: list[OverviewModuleRelation] = []
    sample_truncated = False
    for source_module_id, target_module_id in module_pairs:
        if len(relations) >= max_relations:
            break
        if _deadline_reached(deadline):
            timed_out = True
            break
        source_module = module_by_id[source_module_id]
        target_module = module_by_id[target_module_id]
        pair_edges = sorted(
            edges_by_module_pair[(source_module_id, target_module_id)],
            key=lambda edge: (
                edge.source_location.path,
                edge.source_location.start_line,
                edge.kind,
                edge.target_id,
                edge.edge_id,
            ),
        )
        if len(pair_edges) > _MAX_RELATION_EDGE_SAMPLES:
            sample_truncated = True
        identity = "\0".join(
            (RULES_VERSION, index_id, source_module_id, target_module_id)
        ).encode("utf-8")
        relations.append(
            OverviewModuleRelation(
                relation_id=f"module-relation-{hashlib.sha256(identity).hexdigest()[:32]}",
                index_id=index_id,
                source_module_id=source_module_id,
                target_module_id=target_module_id,
                source_module=source_module,
                target_module=target_module,
                relation_kinds=sorted({edge.kind for edge in pair_edges}),  # type: ignore[arg-type]
                edge_count=len(pair_edges),
                edges=pair_edges[:_MAX_RELATION_EDGE_SAMPLES],
            )
        )
    return relations, len(module_pairs), sample_truncated, timed_out


def _http_path_prefix(path: str) -> str:
    prefix: list[str] = []
    for segment in path.split("/"):
        if not segment:
            continue
        if segment.startswith("{"):
            break
        prefix.append(segment)
    return "/" + "/".join(prefix) if prefix else "/"


def _source_directory_key(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else "/"


def _find_entry_candidates(
    relation: RelationIndex,
    deadline: float,
) -> tuple[list[_EntryCandidate], bool]:
    candidates: list[_EntryCandidate] = []
    symbols_by_path = {
        symbol.path: symbol
        for symbol in relation.symbols
        if symbol.kind == "module"
    }
    function_symbols_by_location: dict[tuple[str, int, int], list[Symbol]] = defaultdict(list)
    for symbol in relation.symbols:
        if symbol.kind == "function":
            function_symbols_by_location[(symbol.path, symbol.start_line, symbol.end_line)].append(symbol)
    for symbols in function_symbols_by_location.values():
        symbols.sort(key=lambda symbol: (symbol.qualified_name, symbol.symbol_id))
    trees: dict[str, ast.Module] = {}
    for source_file in sorted(relation.snapshot.files, key=lambda item: item.path):
        if _deadline_reached(deadline):
            break
        if not source_file.path.lower().endswith(".py") or source_file.path not in symbols_by_path:
            continue
        try:
            trees[source_file.path] = ast.parse(source_file.text, filename=source_file.path, type_comments=True)
        except SyntaxError:
            continue
    hookimpl_markers = _hookimpl_marker_exports(trees, symbols_by_path, deadline)
    hookspec_names, hookspec_symbol_ids = _hookspec_names(trees, symbols_by_path, function_symbols_by_location, deadline)
    for source_file in sorted(relation.snapshot.files, key=lambda item: item.path):
        if _deadline_reached(deadline):
            break
        if not source_file.path.lower().endswith(".py"):
            continue
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
        tree = trees.get(source_file.path)
        if tree is None:
            continue
        candidates.extend(
            _find_fastapi_route_candidates(
                source_file.path,
                tree,
                function_symbols_by_location,
                deadline,
            )
        )
        candidates.extend(
            _find_pluggy_hookimpl_candidates(
                source_file.path,
                module,
                tree,
                function_symbols_by_location,
                hookimpl_markers,
                hookspec_names,
                hookspec_symbol_ids,
            )
        )
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
    candidates.sort(
        key=lambda item: (
            item.path,
            item.start_line,
            item.end_line,
            item.kind,
            item.rule_id,
            item.http_method or "",
            item.declared_path or "",
            item.symbol_id,
        )
    )
    unique_candidates: list[_EntryCandidate] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key = (
            candidate.path,
            candidate.kind,
            candidate.rule_id,
            candidate.http_method or "",
            candidate.declared_path or "",
            candidate.handler_symbol.symbol_id if candidate.handler_symbol is not None else candidate.symbol_id,
            str(candidate.start_line),
        )
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)
    return unique_candidates[:_MAX_ENTRY_CANDIDATES], len(unique_candidates) > _MAX_ENTRY_CANDIDATES


def _find_fastapi_route_candidates(
    path: str,
    tree: ast.Module,
    function_symbols_by_location: dict[tuple[str, int, int], list[Symbol]],
    deadline: float,
) -> list[_EntryCandidate]:
    scanner = _FastApiRouteScanner(path, function_symbols_by_location, deadline)
    scanner.scan(tree.body)
    return scanner.candidates


_PLUGGY_MODULES = frozenset({"pluggy", "pluggy._hooks"})
_PYTEST_HOOKIMPL_ATTRIBUTES = frozenset({"hookimpl"})
_MAX_HOOKIMPL_MARKER_PASSES = 4


class _HookimplBindings:
    """Module-level names that are known to hold a pluggy ``HookimplMarker`` instance."""

    def __init__(self) -> None:
        self.marker_classes: set[str] = set()
        self.pluggy_modules: set[str] = set()
        self.instances: set[str] = set()
        self.namespaces: dict[str, frozenset[str]] = {}

    def clear(self, name: str) -> None:
        self.marker_classes.discard(name)
        self.pluggy_modules.discard(name)
        self.instances.discard(name)
        self.namespaces.pop(name, None)

    def clear_all(self) -> None:
        self.marker_classes.clear()
        self.pluggy_modules.clear()
        self.instances.clear()
        self.namespaces.clear()

    def clear_writes(self, statement: ast.stmt) -> None:
        writes = _BindingWriteCollector()
        writes.visit(statement)
        if writes.invalidate_all:
            self.clear_all()
        for name in writes.names:
            self.clear(name)

    def is_marker(self, decorator: ast.expr) -> bool:
        expression = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(expression, ast.Name):
            return expression.id in self.instances
        if isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
            return expression.attr in self.namespaces.get(expression.value.id, frozenset())
        return False


def _scan_hookimpl_module(
    path: str,
    module: Symbol,
    tree: ast.Module,
    exported: Mapping[str, frozenset[str]],
) -> tuple[_HookimplBindings, list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.expr]]]:
    """Track module-level hookimpl markers in statement order and collect decorated functions.

    Recognized markers are ``pluggy.HookimplMarker(...)`` instances bound in this
    module or imported from a snapshot module that binds one, and ``pytest.hookimpl``.
    Any later rebinding of a name drops it, so shadowed markers are not trusted.
    """

    bindings = _HookimplBindings()
    decorated: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.expr]] = []

    def record(function: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        marker = next((item for item in function.decorator_list if bindings.is_marker(item)), None)
        if marker is not None:
            decorated.append((function, marker))

    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom):
            resolved = _resolved_python_import_module(module, path, statement.level, statement.module)
            for alias in statement.names:
                if alias.name == "*":
                    bindings.clear_all()
                    continue
                local_name = alias.asname or alias.name
                bindings.clear(local_name)
                if resolved is None:
                    continue
                if resolved in _PLUGGY_MODULES and alias.name == "HookimplMarker":
                    bindings.marker_classes.add(local_name)
                elif resolved == "pytest" and alias.name in _PYTEST_HOOKIMPL_ATTRIBUTES:
                    bindings.instances.add(local_name)
                elif alias.name in exported.get(resolved, frozenset()):
                    bindings.instances.add(local_name)
                elif f"{resolved}.{alias.name}" in exported:
                    bindings.namespaces[local_name] = exported[f"{resolved}.{alias.name}"]
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                local_name = alias.asname or alias.name.split(".", 1)[0]
                bindings.clear(local_name)
                if alias.name in _PLUGGY_MODULES and (alias.asname or alias.name == "pluggy"):
                    bindings.pluggy_modules.add(local_name)
                elif alias.name == "pytest":
                    bindings.namespaces[local_name] = _PYTEST_HOOKIMPL_ATTRIBUTES
                elif alias.asname and alias.name in exported:
                    bindings.namespaces[local_name] = exported[alias.name]
        elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            value = statement.value
            is_marker = False
            if len(targets) == 1 and isinstance(targets[0], ast.Name) and value is not None:
                if isinstance(value, ast.Name):
                    is_marker = value.id in bindings.instances
                elif isinstance(value, ast.Call):
                    func = value.func
                    is_marker = (
                        isinstance(func, ast.Name) and func.id in bindings.marker_classes
                    ) or (
                        isinstance(func, ast.Attribute)
                        and func.attr == "HookimplMarker"
                        and isinstance(func.value, ast.Name)
                        and func.value.id in bindings.pluggy_modules
                    )
            bindings.clear_writes(statement)
            if is_marker:
                bindings.instances.add(targets[0].id)  # type: ignore[union-attr]
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            record(statement)
            bindings.clear_writes(statement)
        elif isinstance(statement, ast.ClassDef):
            for item in statement.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    record(item)
            bindings.clear_writes(statement)
        else:
            bindings.clear_writes(statement)
    return bindings, decorated


def _hookimpl_marker_exports(
    trees: Mapping[str, ast.Module],
    modules_by_path: Mapping[str, Symbol],
    deadline: float,
) -> dict[str, frozenset[str]]:
    """Map snapshot module names to the module-level hookimpl marker names they export."""

    exported: dict[str, frozenset[str]] = {}
    for _ in range(_MAX_HOOKIMPL_MARKER_PASSES):
        changed = False
        for path, tree in trees.items():
            if _deadline_reached(deadline):
                return exported
            module = modules_by_path[path]
            bindings, _decorated = _scan_hookimpl_module(path, module, tree, exported)
            names = frozenset(bindings.instances)
            if names and exported.get(module.qualified_name) != names:
                exported[module.qualified_name] = names
                changed = True
        if not changed:
            break
    return exported


def _hookspec_names(
    trees: Mapping[str, ast.Module],
    modules_by_path: Mapping[str, Symbol],
    function_symbols_by_location: dict[tuple[str, int, int], list[Symbol]],
    deadline: float,
) -> tuple[frozenset[str], frozenset[str]]:
    names: set[str] = set()
    symbol_ids: set[str] = set()
    for path, tree in trees.items():
        for spec in _find_hookspec_classifications(
            path, modules_by_path[path], tree, function_symbols_by_location, deadline
        ):
            if spec.symbol_id is not None:
                symbol_ids.add(spec.symbol_id)
                names.add(spec.symbol_name.rsplit(".", 1)[-1])
    return frozenset(names), frozenset(symbol_ids)


def _find_pluggy_hookimpl_candidates(
    path: str,
    module: Symbol,
    tree: ast.Module,
    function_symbols_by_location: dict[tuple[str, int, int], list[Symbol]],
    exported_markers: Mapping[str, frozenset[str]],
    hookspec_names: frozenset[str],
    hookspec_symbol_ids: frozenset[str],
) -> list[_EntryCandidate]:
    """Pluggy hook implementations are called by ``pm.hook.<name>(...)`` at runtime, not statically."""

    _bindings, decorated = _scan_hookimpl_module(path, module, tree, exported_markers)
    decorated_nodes = {id(function) for function, _marker in decorated}
    functions: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, int]] = [
        (function, "python.pluggy.hookimpl_marker", min(item.lineno for item in function.decorator_list))
        for function, _marker in decorated
    ]
    if hookspec_names:
        for statement in tree.body:
            members = statement.body if isinstance(statement, ast.ClassDef) else [statement]
            for item in members:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and id(item) not in decorated_nodes
                    and item.name in hookspec_names
                ):
                    start_line = min([item.lineno, *(decorator.lineno for decorator in item.decorator_list)])
                    functions.append((item, "python.pluggy.hookspec_name", start_line))
    candidates: list[_EntryCandidate] = []
    for function, rule_id, start_line in functions:
        end_line = getattr(function, "end_lineno", None) or function.lineno
        symbols = function_symbols_by_location.get((path, function.lineno, end_line), [])
        if len(symbols) != 1 or symbols[0].symbol_id in hookspec_symbol_ids:
            continue
        symbol = symbols[0]
        suffix = "@hookimpl" if rule_id == "python.pluggy.hookimpl_marker" else "名称匹配 hookspec"
        candidates.append(
            _EntryCandidate(
                path=path,
                start_line=start_line,
                end_line=end_line,
                kind="pluggy_hookimpl",
                rule_id=rule_id,
                label=f"pluggy hook {symbol.qualified_name} ({suffix})"[:500],
                symbol_id=symbol.symbol_id,
                guard=False,
            )
        )
    return candidates


class _LocalBindingCollector(ast.NodeVisitor):
    """Collect names that are local to one function without descending into child scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.escaped: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.names.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.names.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Global(self, node: ast.Global) -> None:
        self.escaped.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.escaped.update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if isinstance(node.name, str):
            self.names.add(node.name)
        self.generic_visit(node)


class _FastApiRouteScanner:
    """Conservatively identify FastAPI decorators under lexical binding rules."""

    def __init__(
        self,
        path: str,
        function_symbols_by_location: dict[tuple[str, int, int], list[Symbol]],
        deadline: float,
    ) -> None:
        self.path = path
        self.function_symbols_by_location = function_symbols_by_location
        self.deadline = deadline
        self.candidates: list[_EntryCandidate] = []

    def scan(self, statements: list[ast.stmt]) -> None:
        self._scan_statements(statements, {})

    def _scan_statements(self, statements: list[ast.stmt], bindings: dict[str, str]) -> None:
        for statement in statements:
            if _deadline_reached(self.deadline):
                return
            if isinstance(statement, ast.Import | ast.ImportFrom):
                self._apply_import(statement, bindings)
            elif isinstance(statement, ast.Assign):
                self._invalidate_statement(statement, bindings)
                binding = self._binding_for_expression(statement.value, bindings)
                for target in statement.targets:
                    for name in _target_names(target):
                        bindings[name] = binding
            elif isinstance(statement, ast.AnnAssign):
                self._invalidate_statement(statement, bindings)
                binding = (
                    self._binding_for_expression(statement.value, bindings)
                    if statement.value is not None
                    else _UNKNOWN_BINDING
                )
                for name in _target_names(statement.target):
                    bindings[name] = binding
            elif isinstance(statement, ast.NamedExpr):
                self._invalidate_statement(statement, bindings)
                binding = self._binding_for_expression(statement.value, bindings)
                for name in _target_names(statement.target):
                    bindings[name] = binding
            elif isinstance(statement, ast.Delete):
                self._invalidate_statement(statement, bindings)
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._invalidate_definition_time_effects(statement, bindings)
                route_found = self._append_route_candidates(statement, bindings)
                if not route_found:
                    self._scan_function(statement, bindings)
                bindings[statement.name] = _UNKNOWN_BINDING
            elif isinstance(statement, ast.ClassDef):
                self._invalidate_definition_time_effects(statement, bindings)
                bindings[statement.name] = _UNKNOWN_BINDING
            else:
                self._invalidate_statement(statement, bindings)

    @staticmethod
    def _invalidate_statement(statement: ast.AST, bindings: dict[str, str]) -> None:
        collector = _BindingWriteCollector()
        collector.visit(statement)
        if collector.invalidate_all:
            bindings.clear()
            return
        for name in collector.names:
            bindings[name] = _UNKNOWN_BINDING

    @staticmethod
    def _invalidate_definition_time_effects(
        statement: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        bindings: dict[str, str],
    ) -> None:
        collector = _BindingWriteCollector(include_definition_names=False)
        collector.visit(statement)
        if collector.invalidate_all:
            bindings.clear()
            return
        for name in collector.names:
            bindings[name] = _UNKNOWN_BINDING

    def _scan_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, bindings: dict[str, str]) -> None:
        collector = _LocalBindingCollector()
        for statement in node.body:
            collector.visit(statement)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            collector.names.add(argument.arg)
        if node.args.vararg is not None:
            collector.names.add(node.args.vararg.arg)
        if node.args.kwarg is not None:
            collector.names.add(node.args.kwarg.arg)
        child_bindings = dict(bindings)
        for name in collector.names - collector.escaped:
            child_bindings[name] = _UNKNOWN_BINDING
        self._scan_statements(node.body, child_bindings)

    def _append_route_candidates(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        bindings: dict[str, str],
    ) -> bool:
        recognized = False
        end_line = getattr(node, "end_lineno", None) or node.lineno
        handler_symbols = self.function_symbols_by_location.get(
            (self.path, node.lineno, end_line),
            [],
        )
        handler = handler_symbols[0] if handler_symbols else None
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            if call is None or not isinstance(call.func, ast.Attribute):
                continue
            receiver = call.func.value
            if not isinstance(receiver, ast.Name) or bindings.get(receiver.id) != _FASTAPI_BINDING_INSTANCE:
                continue
            methods = self._route_methods(call.func.attr, call)
            path_value = self._literal_route_path(call)
            if not methods or path_value is None:
                continue
            recognized = True
            if handler is None:
                continue
            start_line = min(
                getattr(item, "lineno", node.lineno)
                for item in node.decorator_list
            )
            for method in methods:
                self.candidates.append(
                    _EntryCandidate(
                        path=self.path,
                        start_line=start_line,
                        end_line=end_line,
                        kind="fastapi_route",
                        rule_id="fastapi.decorator.route",
                        label=f"{method} {path_value} → {handler.qualified_name}",
                        symbol_id=handler.symbol_id,
                        guard=False,
                        http_method=method,
                        declared_path=path_value,
                        handler_symbol=handler,
                    )
                )
        return recognized

    def _route_methods(self, decorator_name: str, call: ast.Call) -> list[str]:
        if decorator_name in _FASTAPI_ROUTE_METHODS:
            return [_FASTAPI_ROUTE_METHODS[decorator_name]]
        if decorator_name != "api_route":
            return []
        methods_node = next(
            (keyword.value for keyword in call.keywords if keyword.arg == "methods"),
            None,
        )
        if not isinstance(methods_node, (ast.List, ast.Set, ast.Tuple)):
            return []
        methods: set[str] = set()
        for item in methods_node.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return []
            method = item.value.upper()
            if method not in _FASTAPI_ROUTE_METHODS.values():
                return []
            methods.add(method)
        return sorted(methods)

    @staticmethod
    def _literal_route_path(call: ast.Call) -> str | None:
        path_node = call.args[0] if call.args else next(
            (keyword.value for keyword in call.keywords if keyword.arg == "path"),
            None,
        )
        if not isinstance(path_node, ast.Constant) or not isinstance(path_node.value, str):
            return None
        if not path_node.value.startswith("/"):
            return None
        return path_node.value

    @staticmethod
    def _binding_for_expression(expression: ast.expr, bindings: dict[str, str]) -> str:
        if isinstance(expression, ast.Call) and _is_fastapi_constructor(expression.func, bindings):
            return _FASTAPI_BINDING_INSTANCE
        if isinstance(expression, ast.Name):
            return bindings.get(expression.id, _UNKNOWN_BINDING)
        return _UNKNOWN_BINDING

    @staticmethod
    def _apply_import(statement: ast.Import | ast.ImportFrom, bindings: dict[str, str]) -> None:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.name == "*":
                    bindings.clear()
                    continue
                local_name = alias.asname or alias.name.split(".", 1)[0]
                bindings[local_name] = (
                    _FASTAPI_BINDING_MODULE if alias.name == "fastapi" else _UNKNOWN_BINDING
                )
            return
        for alias in statement.names:
            if alias.name == "*":
                bindings.clear()
                continue
            local_name = alias.asname or alias.name
            bindings[local_name] = (
                _FASTAPI_BINDING_CLASS
                if statement.level == 0 and statement.module == "fastapi" and alias.name == "FastAPI"
                else _UNKNOWN_BINDING
            )


class _BindingWriteCollector(ast.NodeVisitor):
    """Collect possible binding writes without entering child function/class bodies."""

    def __init__(self, *, include_definition_names: bool = True) -> None:
        self.include_definition_names = include_definition_names
        self.names: set[str] = set()
        self.write_counts: dict[str, int] = defaultdict(int)
        self.invalidate_all = False

    def _record(self, name: str) -> None:
        self.names.add(name)
        self.write_counts[name] += 1

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._record(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.invalidate_all = True
            else:
                self._record(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.invalidate_all = True
            else:
                self._record(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.include_definition_names:
            self._record(node.name)
        self._visit_definition_time_expressions(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if self.include_definition_names:
            self._record(node.name)
        self._visit_definition_time_expressions(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self.include_definition_names:
            self._record(node.name)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if isinstance(node.name, str):
            self._record(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name is not None:
            self._record(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name is not None:
            self._record(node.name)
        self.generic_visit(node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest is not None:
            self._record(node.rest)
        self.generic_visit(node)

    def _visit_definition_time_expressions(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.List, ast.Tuple)):
        names: list[str] = []
        for item in target.elts:
            names.extend(_target_names(item))
        return names
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


def _is_fastapi_constructor(expression: ast.expr, bindings: dict[str, str]) -> bool:
    if isinstance(expression, ast.Name):
        return bindings.get(expression.id) == _FASTAPI_BINDING_CLASS
    return (
        isinstance(expression, ast.Attribute)
        and expression.attr == "FastAPI"
        and isinstance(expression.value, ast.Name)
        and bindings.get(expression.value.id) == _FASTAPI_BINDING_MODULE
    )


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
