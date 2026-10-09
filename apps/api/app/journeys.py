"""Bounded user-action journeys derived from one persisted source snapshot."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import (
    Evidence,
    JourneyCoverage,
    JourneyStep,
    JourneySummary,
    OverviewEntryPoint,
    OverviewHttpAssociation,
    ProjectJourney,
    ProjectJourneyCatalog,
    ProjectOverview,
    Symbol,
)
from .indexing import SnapshotFile
from .relations import RelationIndex, relation_coverage_limitations


RULES_VERSION = "ui-action-v1+static-calls-v1+http-shape-v2"
_MAX_FILES = 100
_MAX_INPUT_BYTES = 5_000_000
_MAX_ACTIONS = 1_000
_MAX_BINDINGS = 2_000
_MAX_JOURNEYS = 40
_MAX_CALL_DEPTH = 8
_MAX_SOURCE_LINES = 24


@dataclass(frozen=True)
class _JourneySpec:
    summary: JourneySummary
    action: dict[str, Any]
    callback: Symbol
    callback_certainty: str
    callback_note: str
    call_path: list[Any]
    client: Symbol
    association: OverviewHttpAssociation
    route: OverviewEntryPoint


def build_journey_catalog(
    project_id: str,
    relation: RelationIndex,
    overview: ProjectOverview,
) -> tuple[ProjectJourneyCatalog, dict[str, _JourneySpec]]:
    """Build bounded summaries and lightweight specs without reading evidence excerpts."""

    if overview.project_id != project_id or overview.index.index_id != relation.snapshot.index.index_id:
        raise ValueError("journey overview and relations must use the same project index")
    actions, bindings, extraction = _extract_ui_facts(relation.snapshot.files)
    limitations = list(extraction["limitations"])
    if actions and "+typescript-facts-v5" not in (relation.snapshot.index.rules_version or ""):
        limitations.append(
            "所选固定索引早于 typescript-facts-v5；部分 HTTP request facts 可能缺失，显式更新索引后才能纳入新识别规则。"
        )
    limitations.extend(
        relation_coverage_limitations(
            truncated=relation.truncated,
            truncation_reason=relation.truncation_reason,
            unresolved_truncated=relation.unresolved_truncated,
            unresolved_truncation_reason=relation.unresolved_truncation_reason,
        )
    )
    http_coverage = overview.http_association_coverage
    if http_coverage.analysis_status == "not_analyzed":
        limitations.append("此固定索引没有可用的 HTTP method/path 分析事实；旅程不会把源码调用推断成 HTTP 关联。")
    elif http_coverage.analysis_status == "incomplete":
        limitations.append("固定索引的 HTTP method/path association 覆盖不完整；未显示的匹配不能视为不存在。")
    if http_coverage.unknown_count:
        limitations.append(
            f"{http_coverage.unknown_count} 个已观察客户端请求的 method/path 形状未能唯一关联到服务路由。"
        )
    limitations = list(dict.fromkeys(limitations))[:20]
    coverage_base = {
        "parser_status": extraction["parser_status"],
        "http_association_status": http_coverage.analysis_status,
        "scanned_file_count": extraction["scanned_file_count"],
        "action_count": len(actions),
        "action_facts_omitted_count": extraction.get("action_facts_omitted_count", 0),
        "prop_facts_omitted_count": extraction.get("prop_facts_omitted_count", 0),
        "action_facts_truncated": extraction["action_facts_truncated"],
        "prop_facts_truncated": extraction["prop_facts_truncated"],
        "relation_truncated": relation.truncated or relation.unresolved_truncated is True,
        "truncated": extraction["parser_status"] == "truncated",
        "limitations": limitations,
    }
    summaries: list[JourneySummary] = []
    specs: dict[str, _JourneySpec] = {}
    covered_action_keys: set[tuple[str, int, str]] = set()
    symbols_by_identity = {
        (symbol.path, symbol.qualified_name, symbol.start_line): symbol
        for symbol in relation.symbols
        if symbol.kind == "function"
    }
    route_by_id = {entry.entry_point_id: entry for entry in overview.entries}
    callback_paths: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for binding in bindings:
        callback_paths[(binding["prop_name"], binding["callback_property"])].append(binding)

    request_destinations: list[tuple[OverviewHttpAssociation, Symbol, OverviewEntryPoint]] = []
    for association in overview.http_associations:
        route = route_by_id.get(association.route_entry_point_id)
        if route is None:
            continue
        client = _smallest_enclosing_function(
            relation.symbols,
            association.client_evidence.path,
            association.client_evidence.start_line,
            association.client_evidence.end_line,
        )
        if client is not None:
            request_destinations.append((association, client, route))

    journey_limit_reached = False
    for action in actions:
        handlers: list[tuple[Symbol, str, str]] = []
        handler_name = action.get("handler_name")
        receiver_name = action.get("receiver_name")
        callback_property = action.get("callback_property")
        if isinstance(handler_name, str):
            qualified_name = action.get("handler_qualified_name")
            handler_start_line = action.get("handler_start_line")
            if action.get("handler_binding_static") is True and isinstance(qualified_name, str) and isinstance(handler_start_line, int):
                target = symbols_by_identity.get((action["path"], qualified_name, handler_start_line))
                if target is not None:
                    handlers.append((target, "static", "JSX 事件直接引用了同一文件、可见作用域内的唯一本地 handler。"))
        elif isinstance(receiver_name, str) and isinstance(callback_property, str):
            matches = callback_paths.get((receiver_name, callback_property), [])
            targets = {item["target_name"] for item in matches}
            if len(matches) == 1 and len(targets) == 1:
                target_name = next(iter(targets))
                candidate_symbols = [
                    symbol for symbol in relation.symbols
                    if symbol.kind == "function" and symbol.qualified_name.rsplit(".", 1)[-1] == target_name
                ]
                if len(candidate_symbols) == 1:
                    handlers.append((
                        candidate_symbols[0],
                        "unconfirmed",
                        f"事件表达式读取 {receiver_name}.{callback_property}；同快照存在唯一属性候选 {target_name}，跨组件属性绑定未确认。",
                    ))

        for callback, callback_certainty, callback_note in handlers:
            for association, client, route in request_destinations:
                call_path = _static_call_path(relation, callback.symbol_id, client.symbol_id)
                if call_path is None:
                    continue
                journey_id = _journey_id(
                    relation.snapshot.index.index_id,
                    str(action["path"]),
                    int(action["element_start_line"]),
                    str(action["event_name"]),
                    association.method,
                    association.path,
                )
                if journey_id in specs:
                    continue
                if len(summaries) >= _MAX_JOURNEYS:
                    limitations.append(f"journey summary limit of {_MAX_JOURNEYS} reached; remaining matches were omitted")
                    extraction["parser_status"] = "truncated"
                    journey_limit_reached = True
                    break
                summary = _summary(
                    journey_id,
                    relation.snapshot.index.index_id,
                    action,
                    association,
                )
                summaries.append(summary)
                covered_action_keys.add((str(action["path"]), int(action["start_line"]), str(action["event_name"])))
                specs[journey_id] = _JourneySpec(
                    summary=summary,
                    action=action,
                    callback=callback,
                    callback_certainty=callback_certainty,
                    callback_note=callback_note,
                    call_path=call_path,
                    client=client,
                    association=association,
                    route=route,
                )
            if journey_limit_reached:
                break

    is_truncated = extraction["parser_status"] == "truncated" or len(specs) < len(summaries)
    catalog_coverage = JourneyCoverage(
        **{
            **coverage_base,
            "parser_status": extraction["parser_status"],
            "truncated": is_truncated,
            "limitations": list(dict.fromkeys(limitations))[:20],
        },
        journey_count=len(summaries),
        unsupported_action_count=max(0, len(actions) - len(covered_action_keys)),
    )
    catalog = ProjectJourneyCatalog(
        project_id=project_id,
        index=relation.snapshot.index,
        rules_version=RULES_VERSION,
        journeys=summaries,
        coverage=catalog_coverage,
    )
    return catalog, specs


def build_project_journey(
    project_id: str,
    relation: RelationIndex,
    overview: ProjectOverview,
    journey_id: str,
    read_evidence: Callable[[str, str, int, int], Evidence],
) -> ProjectJourney | None:
    """Expand one selected journey into a bounded set of steps and source excerpts."""

    catalog, specs = build_journey_catalog(project_id, relation, overview)
    spec = specs.get(journey_id)
    if spec is None:
        return None
    steps, markers = _journey_steps(
        spec.action,
        spec.callback,
        spec.callback_certainty,
        spec.callback_note,
        spec.call_path,
        spec.client,
        spec.association,
        spec.route,
        relation,
        read_evidence,
    )
    return ProjectJourney(
        project_id=project_id,
        index=relation.snapshot.index,
        rules_version=RULES_VERSION,
        journey_id=journey_id,
        summary=spec.summary,
        steps=steps,
        coverage=catalog.coverage.model_copy(update={"journey_count": 1}),
        markers=markers,
    )


def _summary(
    journey_id: str,
    index_id: str,
    action: dict[str, Any],
    association: OverviewHttpAssociation,
) -> JourneySummary:
    candidates = [str(item)[:200] for item in action.get("label_candidates", []) if isinstance(item, str)]
    label = str(action.get("label") or "用户操作")
    if candidates:
        label = " / ".join(candidates)
    if len(label) > 300:
        label = label[:297] + "…"
    return JourneySummary(
        journey_id=journey_id,
        index_id=index_id,
        action_label=label,
        label_candidates=candidates[:5],
        label_dynamic=bool(action.get("label_dynamic")),
        event_name=action["event_name"],
        action_path=action["path"],
        action_start_line=action["element_start_line"],
        method=association.method,
        path=association.path,
    )


def _journey_steps(
    action: dict[str, Any],
    callback: Symbol,
    callback_certainty: str,
    callback_note: str,
    call_path: list[Any],
    client: Symbol,
    association: OverviewHttpAssociation,
    route: OverviewEntryPoint,
    relation: RelationIndex,
    read_evidence: Callable[[str, str, int, int], Evidence],
) -> tuple[list[JourneyStep], list[str]]:
    index_id = relation.snapshot.index.index_id
    markers: list[str] = []
    evidence_action = _read_evidence(
        read_evidence,
        index_id,
        action["path"],
        action["element_start_line"],
        action["element_end_line"],
    )
    if evidence_action is None:
        markers.append("操作源码证据无法从所选索引读取。")
    callback_evidence = _symbol_evidence(read_evidence, index_id, callback)
    if callback_evidence is None:
        markers.append("前端处理符号的源码证据无法从所选索引读取。")
    labels = action.get("label_candidates", [])
    trigger_label = " / ".join(labels) if labels else str(action.get("label") or "用户操作")
    dynamic_note = "标签会按状态切换；源码只给出候选文本。" if action.get("label_dynamic") else ""
    steps: list[JourneyStep] = [
        JourneyStep(
            step_id="journey-step-trigger",
            role="trigger",
            title=f"提交表单：{trigger_label}" if action.get("event_name") == "onSubmit" else trigger_label,
            relation_kind="ui_action",
            certainty="static",
            explanation=f"TSX 快照中识别到 {action.get('event_name')} 动作；{dynamic_note or '标签来自同快照 JSX 文本。'}",
            evidence=[evidence_action] if evidence_action else [],
        ),
        JourneyStep(
            step_id="journey-step-client-handler",
            role="client_processing",
            title="前端接收并处理这次提交",
            relation_kind="ui_callback",
            certainty="static" if callback_certainty == "static" else "unconfirmed",
            explanation=callback_note,
            symbols=[callback],
            evidence=[callback_evidence] if callback_evidence else [],
        ),
    ]
    markers.append("UI handler 与回调属性关系未被完整跨组件解析，按源码候选标记。" if callback_certainty != "static" else "")

    path_symbols = [callback]
    path_evidence: list[Evidence] = []
    for edge in call_path:
        target = next((symbol for symbol in relation.symbols if symbol.symbol_id == edge.target_id), None)
        if target is not None and (not path_symbols or path_symbols[-1].symbol_id != target.symbol_id):
            path_symbols.append(target)
        if edge.source_location is not None:
            evidence = _read_evidence(
                read_evidence,
                index_id,
                edge.source_location.path,
                edge.source_location.start_line,
                edge.source_location.end_line,
            )
            if evidence is not None and evidence.evidence_id not in {item.evidence_id for item in path_evidence}:
                path_evidence.append(evidence)
            elif evidence is None:
                markers.append("部分前端调用来源证据无法从所选索引读取。")
    if len(path_symbols) > 8:
        path_symbols = path_symbols[:8]
    steps.append(
        JourneyStep(
            step_id="journey-step-client-calls",
            role="client_processing",
            title="前端沿静态调用关系准备请求",
            relation_kind="calls",
            certainty="static",
            explanation="展示同一索引中的静态调用路径；它说明源码引用关系，不代表浏览器已执行。",
            symbols=path_symbols,
            evidence=path_evidence[:8],
        )
    )
    steps.append(
        JourneyStep(
            step_id="journey-step-http",
            role="request",
            title="前端提交导入请求",
            relation_kind="http_shape",
            certainty="shape",
            explanation="客户端请求声明与服务端入口的接口形状唯一匹配；这不代表浏览器实际发送过请求。",
            symbols=[client],
            evidence=[association.client_evidence, association.route_evidence],
        )
    )
    route_symbols = [route.handler_symbol] if route.handler_symbol is not None else []
    route_evidence = [route.evidence] if route.evidence is not None else [association.route_evidence]
    steps.append(
        JourneyStep(
            step_id="journey-step-route",
            role="server_processing",
            title="服务端接收请求",
            relation_kind="http_shape",
            certainty="shape",
            explanation="同一索引中的路由声明指向此处理入口；挂载前缀与运行时分派不由该静态事实证明。",
            symbols=route_symbols,
            evidence=route_evidence,
        )
    )

    handler = route.handler_symbol
    backend_edges = []
    if handler is not None:
        backend_edges = _reachable_call_edges(relation, handler.symbol_id, depth=2, limit=4)
    if backend_edges:
        backend_symbols = [handler] if handler is not None else []
        backend_evidence: list[Evidence] = []
        for edge in backend_edges:
            target = next((symbol for symbol in relation.symbols if symbol.symbol_id == edge.target_id), None)
            if target is not None and all(item.symbol_id != target.symbol_id for item in backend_symbols):
                backend_symbols.append(target)
            if edge.source_location is not None:
                evidence = _read_evidence(
                    read_evidence,
                    index_id,
                    edge.source_location.path,
                    edge.source_location.start_line,
                    edge.source_location.end_line,
                )
                if evidence is not None:
                    backend_evidence.append(evidence)
                else:
                    markers.append("部分服务端调用来源证据无法从所选索引读取。")
        steps.append(
            JourneyStep(
                step_id="journey-step-backend-calls",
                role="server_processing",
                title="服务端继续处理",
                relation_kind="calls",
                certainty="static",
                explanation="源码中可见服务端后续调用；这里只列有界样本，未列分支不代表不存在。",
                symbols=backend_symbols[:8],
                evidence=backend_evidence[:8],
            )
        )
    else:
        unresolved = [
            item for item in relation.unresolved
            if handler is not None and item.source_symbol_id == handler.symbol_id
        ][:4]
        unresolved_evidence = [
            evidence
            for item in unresolved
            if (evidence := _read_evidence(read_evidence, index_id, item.path, item.start_line, item.end_line)) is not None
        ]
        if unresolved and not unresolved_evidence:
            markers.append("后端断点的源码证据无法从所选索引读取。")
        unresolved_names = ", ".join(item.expression for item in unresolved) or "没有可确认的直接调用边"
        steps.append(
            JourneyStep(
                step_id="journey-step-backend-gap",
                role="server_processing",
                title="服务端后续处理仍有断点",
                relation_kind="unresolved",
                certainty="unconfirmed",
                explanation=f"静态调用目标没有形成已确认 CodeEdge：{unresolved_names}。请展开源码核对。",
                symbols=route_symbols,
                evidence=unresolved_evidence,
            )
        )
        markers.append("服务端路由后的部分调用目标没有静态解析；未补造调用边。")

    steps.append(
        JourneyStep(
            step_id="journey-step-result",
            role="result",
            title="请求结果如何更新页面尚未确认",
            relation_kind="result_display",
            certainty="unconfirmed",
            explanation="当前索引没有足够的静态关系证明请求结果如何更新界面，因此将结果呈现保留为未确认断点。",
        )
    )
    markers.append("当前索引未确认请求结果与界面更新之间的关系。")
    markers = [marker for marker in markers if marker]
    if len(steps) > 8:
        steps = steps[:8]
        markers.append("步骤上限 8 已应用。")
    return steps, markers


def _read_evidence(
    read_evidence: Callable[[str, str, int, int], Evidence],
    index_id: str,
    path: str,
    start: int,
    end: int,
) -> Evidence | None:
    if end < start:
        return None
    bounded_end = min(end, start + _MAX_SOURCE_LINES - 1)
    try:
        return read_evidence(index_id, path, start, bounded_end)
    except Exception:
        return None


def _symbol_evidence(
    read_evidence: Callable[[str, str, int, int], Evidence],
    index_id: str,
    symbol: Symbol,
) -> Evidence | None:
    return _read_evidence(
        read_evidence,
        index_id,
        symbol.path,
        symbol.start_line,
        symbol.end_line,
    )


def _smallest_enclosing_function(
    symbols: tuple[Symbol, ...], path: str, start_line: int, end_line: int
) -> Symbol | None:
    matches = [
        symbol for symbol in symbols
        if symbol.path == path and symbol.kind == "function"
        and symbol.start_line <= start_line and symbol.end_line >= end_line
    ]
    return min(matches, key=lambda symbol: (symbol.end_line - symbol.start_line, symbol.start_line)) if matches else None


def _static_call_path(relation: RelationIndex, start_id: str, target_id: str) -> list[Any] | None:
    if start_id == target_id:
        return []
    adjacency: dict[str, list[Any]] = defaultdict(list)
    for edge in relation.edges:
        if edge.kind == "calls":
            adjacency[edge.source_id].append(edge)
    queue = deque([(start_id, 0)])
    previous: dict[str, tuple[str, Any] | None] = {start_id: None}
    while queue:
        current, depth = queue.popleft()
        if depth >= _MAX_CALL_DEPTH:
            continue
        for edge in sorted(adjacency.get(current, []), key=lambda item: (item.source_location.start_line if item.source_location else 0, item.edge_id)):
            if edge.target_id in previous:
                continue
            previous[edge.target_id] = (current, edge)
            if edge.target_id == target_id:
                path = []
                cursor = target_id
                while previous[cursor] is not None:
                    parent, item = previous[cursor]
                    path.append(item)
                    cursor = parent
                return list(reversed(path))
            queue.append((edge.target_id, depth + 1))
    return None


def _reachable_call_edges(relation: RelationIndex, start_id: str, *, depth: int, limit: int) -> list[Any]:
    adjacency: dict[str, list[Any]] = defaultdict(list)
    for edge in relation.edges:
        if edge.kind == "calls":
            adjacency[edge.source_id].append(edge)
    result: list[Any] = []
    seen = {start_id}
    queue = deque([(start_id, 0)])
    while queue and len(result) < limit:
        current, current_depth = queue.popleft()
        if current_depth >= depth:
            continue
        for edge in sorted(adjacency.get(current, []), key=lambda item: (item.source_location.start_line if item.source_location else 0, item.edge_id)):
            result.append(edge)
            if edge.target_id not in seen:
                seen.add(edge.target_id)
                queue.append((edge.target_id, current_depth + 1))
            if len(result) >= limit:
                break
    return result


def _journey_id(index_id: str, path: str, start: int, event: str, method: str, route_path: str) -> str:
    identity = "\0".join((index_id, path, str(start), event, method, route_path))
    return f"journey-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"


def _extract_ui_facts(files: tuple[SnapshotFile, ...]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    candidates = [
        item for item in files
        if item.path.lower().endswith(".tsx") and re.search(r"\bon(?:Submit|Click)\b", item.text)
    ]
    limitations: list[str] = []
    parser_status = "ready"
    if len(candidates) > _MAX_FILES:
        candidates = candidates[:_MAX_FILES]
        parser_status = "truncated"
        limitations.append(f"TSX action scan is limited to {_MAX_FILES} source files")
    if sum(item.size for item in candidates) > _MAX_INPUT_BYTES:
        return [], [], {
            "parser_status": "truncated",
            "scanned_file_count": 0,
            "action_facts_truncated": False,
            "prop_facts_truncated": False,
            "limitations": ["TSX action source exceeded the 5 MB parser input budget"],
        }
    if not candidates:
        return [], [], {
            "parser_status": "ready",
            "scanned_file_count": 0,
            "action_facts_truncated": False,
            "prop_facts_truncated": False,
            "limitations": [],
        }
    node = shutil.which("node")
    parser = Path(__file__).with_name("typescript_parser.cjs")
    if node is None or not parser.is_file():
        return [], [], {
            "parser_status": "unavailable",
            "scanned_file_count": len(candidates),
            "action_facts_truncated": False,
            "prop_facts_truncated": False,
            "limitations": ["locked TypeScript parser is unavailable; UI action journeys were not analyzed"],
        }
    payload = json.dumps(
        {"files": [{"path": item.path, "text": item.text} for item in candidates]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(payload.encode("utf-8")) > _MAX_INPUT_BYTES:
        return [], [], {
            "parser_status": "truncated",
            "scanned_file_count": 0,
            "action_facts_truncated": False,
            "prop_facts_truncated": False,
            "limitations": ["serialized TSX action source exceeded the parser input budget"],
        }
    environment = {"PATH": os.environ.get("PATH", "")}
    if compiler_path := os.environ.get("CODETRAIL_TYPESCRIPT_COMPILER"):
        environment["CODETRAIL_TYPESCRIPT_COMPILER"] = compiler_path
    for key in ("SystemRoot", "WINDIR", "TEMP", "TMP"):
        if value := os.environ.get(key):
            environment[key] = value
    environment["NODE_OPTIONS"] = "--max-old-space-size=128"
    try:
        result = subprocess.run(
            [node, str(parser)],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            cwd=parser.parent,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [], [], {
            "parser_status": "unavailable",
            "scanned_file_count": len(candidates),
            "action_facts_truncated": False,
            "prop_facts_truncated": False,
            "limitations": ["locked TypeScript parser could not complete within its 10 second budget"],
        }
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > 8_500_000:
        return [], [], {
            "parser_status": "invalid",
            "scanned_file_count": len(candidates),
            "action_facts_truncated": False,
            "prop_facts_truncated": False,
            "limitations": ["locked TypeScript parser failed or exceeded its output budget"],
        }
    try:
        decoded = json.loads(result.stdout)
    except json.JSONDecodeError:
        return [], [], {
            "parser_status": "invalid",
            "scanned_file_count": len(candidates),
            "action_facts_truncated": False,
            "prop_facts_truncated": False,
            "limitations": ["locked TypeScript parser returned invalid output"],
        }
    if not isinstance(decoded, dict) or decoded.get("compiler_version") != "5.9.3" or not isinstance(decoded.get("files"), list):
        return [], [], {
            "parser_status": "invalid",
            "scanned_file_count": len(candidates),
            "action_facts_truncated": False,
            "prop_facts_truncated": False,
            "limitations": ["TypeScript parser output did not match the locked compiler contract"],
        }
    allowed_paths = {item.path for item in candidates}
    actions: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    action_truncated = False
    binding_truncated = False
    action_omitted_count = 0
    binding_omitted_count = 0
    line_counts = {item.path: max(1, len(item.text.splitlines())) for item in candidates}
    syntax_error_seen = False
    for item in decoded["files"]:
        if not isinstance(item, dict) or item.get("path") not in allowed_paths:
            return [], [], {
                "parser_status": "invalid",
                "scanned_file_count": len(candidates),
                "action_facts_truncated": False,
                "prop_facts_truncated": False,
                "limitations": ["TypeScript parser returned an unexpected source path"],
            }
        path = item["path"]
        if item.get("syntax_error") is not None:
            syntax_error_seen = True
            limitations.append(f"{path} has a TSX syntax error; its UI actions were not included")
            continue
        file_actions = item.get("ui_actions")
        file_bindings = item.get("ui_prop_bindings")
        if not isinstance(file_actions, list) or not isinstance(file_bindings, list):
            return [], [], {
                "parser_status": "invalid",
                "scanned_file_count": len(candidates),
                "action_facts_truncated": False,
                "prop_facts_truncated": False,
                "limitations": ["TypeScript parser omitted UI action facts"],
            }
        action_truncated = action_truncated or item.get("ui_actions_truncated") is True
        binding_truncated = binding_truncated or item.get("ui_prop_bindings_truncated") is True
        file_action_omitted = item.get("ui_actions_omitted")
        file_binding_omitted = item.get("ui_prop_bindings_omitted")
        if type(file_action_omitted) is not int or file_action_omitted < 0 or type(file_binding_omitted) is not int or file_binding_omitted < 0:
            return [], [], {
                "parser_status": "invalid",
                "scanned_file_count": len(candidates),
                "action_facts_truncated": False,
                "prop_facts_truncated": False,
                "limitations": ["TypeScript parser returned invalid UI omission counts"],
            }
        action_omitted_count += file_action_omitted
        binding_omitted_count += file_binding_omitted
        for fact in file_actions:
            if not _valid_ui_fact(fact, line_counts[path], action=True):
                return [], [], {
                    "parser_status": "invalid",
                    "scanned_file_count": len(candidates),
                    "action_facts_truncated": False,
                    "prop_facts_truncated": False,
                    "limitations": ["TypeScript parser returned an invalid JSX action fact"],
                }
            actions.append({**fact, "path": path})
        for fact in file_bindings:
            if not _valid_ui_fact(fact, line_counts[path], action=False):
                return [], [], {
                    "parser_status": "invalid",
                    "scanned_file_count": len(candidates),
                    "action_facts_truncated": False,
                    "prop_facts_truncated": False,
                    "limitations": ["TypeScript parser returned an invalid JSX property binding fact"],
                }
            bindings.append({**fact, "path": path})
    if len(actions) > _MAX_ACTIONS:
        action_truncated = True
        action_omitted_count += len(actions) - _MAX_ACTIONS
        parser_status = "truncated"
        limitations.append(f"JSX action output limit of {_MAX_ACTIONS} reached; omitted actions are not treated as absent")
    if len(bindings) > _MAX_BINDINGS:
        binding_truncated = True
        binding_omitted_count += len(bindings) - _MAX_BINDINGS
        parser_status = "truncated"
        limitations.append(f"JSX property binding output limit of {_MAX_BINDINGS} reached; omitted bindings are not treated as absent")
    if syntax_error_seen:
        parser_status = "truncated"
    if action_truncated:
        parser_status = "truncated"
        if not any("action" in item for item in limitations):
            limitations.append("JSX action fact budget reached; omitted actions are not treated as absent")
    if binding_truncated:
        parser_status = "truncated"
        if not any("property binding" in item for item in limitations):
            limitations.append("JSX property binding fact budget reached; omitted bindings are not treated as absent")
    actions.sort(key=lambda fact: (fact["path"], fact["start_line"], fact["event_name"], fact["end_line"]))
    bindings.sort(key=lambda fact: (fact["path"], fact["start_line"], fact["callback_property"], fact["target_name"]))
    return actions[:_MAX_ACTIONS], bindings[:_MAX_BINDINGS], {
        "parser_status": parser_status,
        "scanned_file_count": len(candidates),
        "action_facts_omitted_count": action_omitted_count,
        "prop_facts_omitted_count": binding_omitted_count,
        "action_facts_truncated": action_truncated or len(actions) > _MAX_ACTIONS,
        "prop_facts_truncated": binding_truncated or len(bindings) > _MAX_BINDINGS,
        "limitations": limitations[:20],
    }


def _valid_ui_fact(fact: Any, line_count: int, *, action: bool) -> bool:
    if not isinstance(fact, dict):
        return False
    fields = ("start_line", "end_line")
    if not all(isinstance(fact.get(field), int) and 1 <= fact[field] <= line_count for field in fields):
        return False
    if fact["end_line"] < fact["start_line"]:
        return False
    if action:
        return (
            fact.get("event_name") in {"onSubmit", "onClick"}
            and isinstance(fact.get("element_name"), str)
            and isinstance(fact.get("label"), str)
            and isinstance(fact.get("label_candidates"), list)
            and len(fact["label_candidates"]) <= 5
            and all(isinstance(item, str) and len(item) <= 200 for item in fact["label_candidates"])
            and isinstance(fact.get("label_dynamic"), bool)
            and isinstance(fact.get("handler_expression"), str)
            and isinstance(fact.get("handler_binding_static"), bool)
            and (fact.get("handler_qualified_name") is None or isinstance(fact.get("handler_qualified_name"), str))
            and (fact.get("handler_start_line") is None or isinstance(fact.get("handler_start_line"), int))
            and isinstance(fact.get("element_start_line"), int)
            and isinstance(fact.get("element_end_line"), int)
            and 1 <= fact["element_start_line"] <= fact["element_end_line"] <= line_count
        )
    return all(isinstance(fact.get(key), str) and len(fact[key]) <= 500 for key in ("prop_name", "callback_property", "target_name"))
