"""Deterministic entry-point and module overview generation for one snapshot."""

from __future__ import annotations

import ast
import hashlib
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable

from .contracts import (
    CodeEdge,
    Evidence,
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


RULES_VERSION = "python-entry-v5+http-shape-v2"
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
            "当前规则覆盖 __main__.py、顶层 __name__ == __main__ 条件，以及可静态确认的 FastAPI 路由。",
        ]
    else:
        notes = [
            "当前规则覆盖 __main__.py、顶层 __name__ == __main__ 条件，以及可静态确认的 FastAPI 路由。",
            (
                "FastAPI 入口显示声明路径；未解析 APIRouter、挂载前缀或动态注册，因此不能视为最终 URL。"
                if has_fastapi_routes
                else "FastAPI 入口仅接受已知 FastAPI 实例、字面量路径和静态方法；未识别路由不表示项目没有入口。"
            ),
            "插件入口和 pyproject 脚本元数据尚未纳入规则。",
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
        if "+typescript-facts-v2" in rules_version or "+typescript-facts-v3" in rules_version
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
        try:
            tree = ast.parse(source_file.text, filename=source_file.path, type_comments=True)
        except SyntaxError:
            continue
        candidates.extend(
            _find_fastapi_route_candidates(
                source_file.path,
                tree,
                function_symbols_by_location,
                deadline,
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
        self.invalidate_all = False

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.invalidate_all = True
            else:
                self.names.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.invalidate_all = True
            else:
                self.names.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.include_definition_names:
            self.names.add(node.name)
        self._visit_definition_time_expressions(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if self.include_definition_names:
            self.names.add(node.name)
        self._visit_definition_time_expressions(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self.include_definition_names:
            self.names.add(node.name)
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
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name is not None:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name is not None:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest is not None:
            self.names.add(node.rest)
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
