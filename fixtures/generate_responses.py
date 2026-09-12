"""Generate deterministic, validated fixture response bundles.

Run with::

    uv run --project apps/api python fixtures/generate_responses.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app import contracts as c  # noqa: E402


FIXTURES = ROOT / "fixtures"
REPOSITORY = FIXTURES / "repos" / "timeout-demo"
SCENARIOS = FIXTURES / "scenarios"
RESPONSES = FIXTURES / "responses"
MANIFEST_PATH = SCENARIOS / "source-manifest.json"
UTC = timezone.utc
BASE_TIME = datetime(2026, 9, 10, 0, 0, 0, tzinfo=UTC)
FIXTURE_PROJECT_ID = "project-fixture-timeout-demo"
FIXTURE_SESSION_ID = "session-fixture-timeout-demo"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_issue(name: str) -> c.Issue:
    return c.Issue.model_validate(load_json(SCENARIOS / "issues" / name))


def load_manifest() -> dict:
    return load_json(MANIFEST_PATH)


def snapshot_hash(manifest: dict) -> str:
    canonical = json.dumps(manifest["files"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def content_hash(manifest: dict, path: str) -> str:
    for entry in manifest["files"]:
        if entry["path"] == path:
            return entry["sha256"]
    raise KeyError(path)


def source_excerpt(path: str, start_line: int, end_line: int) -> str:
    lines = (REPOSITORY / path).read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[start_line - 1 : end_line])


def evidence(
    evidence_id: str,
    index_id: str,
    manifest: dict,
    path: str,
    start_line: int,
    end_line: int,
) -> c.Evidence:
    return c.Evidence(
        evidence_id=evidence_id,
        index_id=index_id,
        path=path,
        start_line=start_line,
        end_line=end_line,
        excerpt=source_excerpt(path, start_line, end_line),
        content_hash=content_hash(manifest, path),
        kind="source",
    )


def symbol(
    symbol_id: str,
    index_id: str,
    path: str,
    qualified_name: str,
    kind: c.SymbolKind,
    start_line: int,
    end_line: int,
) -> c.Symbol:
    return c.Symbol(
        symbol_id=symbol_id,
        index_id=index_id,
        path=path,
        qualified_name=qualified_name,
        kind=kind,
        start_line=start_line,
        end_line=end_line,
    )


def build_graph(index_id: str, candidate_links: list[c.CandidateLink] | None = None) -> c.GraphSnapshot:
    nodes = [
        symbol("sym-module-main", index_id, "timeout_demo/__main__.py", "timeout_demo.__main__", "module", 1, 18),
        symbol("sym-main", index_id, "timeout_demo/__main__.py", "timeout_demo.__main__.main", "function", 11, 14),
        symbol("sym-module-app", index_id, "timeout_demo/app.py", "timeout_demo.app", "module", 1, 20),
        symbol("sym-start", index_id, "timeout_demo/app.py", "timeout_demo.app.start", "function", 12, 20),
        symbol("sym-module-config", index_id, "timeout_demo/config.py", "timeout_demo.config", "module", 1, 25),
        symbol("sym-load-settings", index_id, "timeout_demo/config.py", "timeout_demo.config.load_settings", "function", 12, 25),
        symbol("sym-module-client", index_id, "timeout_demo/client.py", "timeout_demo.client", "module", 1, 19),
        symbol("sym-client", index_id, "timeout_demo/client.py", "timeout_demo.client.Client", "class", 8, 13),
        symbol("sym-initialize-client", index_id, "timeout_demo/client.py", "timeout_demo.client.initialize_client", "function", 16, 19),
        symbol("sym-module-dispatch", index_id, "timeout_demo/dispatch.py", "timeout_demo.dispatch", "module", 1, 20),
        symbol("sym-dispatch", index_id, "timeout_demo/dispatch.py", "timeout_demo.dispatch.dispatch", "function", 15, 20),
        symbol("sym-module-handlers", index_id, "timeout_demo/handlers.py", "timeout_demo.handlers", "module", 1, 15),
        symbol("sym-primary-handle", index_id, "timeout_demo/handlers.py", "timeout_demo.handlers.handle", "function", 8, 15),
        symbol("sym-module-other-handlers", index_id, "timeout_demo/other_handlers.py", "timeout_demo.other_handlers", "module", 1, 15),
        symbol("sym-secondary-handle", index_id, "timeout_demo/other_handlers.py", "timeout_demo.other_handlers.handle", "function", 8, 15),
    ]

    def edge(
        edge_id: str,
        source_id: str,
        target_id: str,
        kind: c.EdgeKind,
        path: str,
        start_line: int,
        end_line: int,
        extraction_method: str = "fixture",
    ) -> c.CodeEdge:
        return c.CodeEdge(
            edge_id=edge_id,
            index_id=index_id,
            source_id=source_id,
            target_id=target_id,
            kind=kind,
            extraction_method=extraction_method,  # type: ignore[arg-type]
            source_location=c.SourceLocation(path=path, start_line=start_line, end_line=end_line),
        )

    static_edges = [
        edge("edge-contains-main", "sym-module-main", "sym-main", "contains", "timeout_demo/__main__.py", 11, 14),
        edge("edge-contains-start", "sym-module-app", "sym-start", "contains", "timeout_demo/app.py", 12, 20),
        edge("edge-contains-load", "sym-module-config", "sym-load-settings", "contains", "timeout_demo/config.py", 12, 25),
        edge("edge-contains-client", "sym-module-client", "sym-client", "contains", "timeout_demo/client.py", 8, 13),
        edge("edge-contains-initialize", "sym-module-client", "sym-initialize-client", "contains", "timeout_demo/client.py", 16, 19),
        edge("edge-contains-dispatch", "sym-module-dispatch", "sym-dispatch", "contains", "timeout_demo/dispatch.py", 15, 20),
        edge("edge-contains-primary", "sym-module-handlers", "sym-primary-handle", "contains", "timeout_demo/handlers.py", 8, 15),
        edge("edge-contains-secondary", "sym-module-other-handlers", "sym-secondary-handle", "contains", "timeout_demo/other_handlers.py", 8, 15),
        edge("edge-import-main-app", "sym-module-main", "sym-module-app", "imports", "timeout_demo/__main__.py", 8, 8, "explicit_import"),
        edge("edge-import-app-client", "sym-module-app", "sym-module-client", "imports", "timeout_demo/app.py", 7, 7, "explicit_import"),
        edge("edge-import-app-config", "sym-module-app", "sym-module-config", "imports", "timeout_demo/app.py", 8, 8, "explicit_import"),
        edge("edge-import-app-dispatch", "sym-module-app", "sym-module-dispatch", "imports", "timeout_demo/app.py", 9, 9, "explicit_import"),
        edge("edge-import-dispatch-handlers", "sym-module-dispatch", "sym-module-handlers", "imports", "timeout_demo/dispatch.py", 5, 5, "explicit_import"),
        edge("edge-import-dispatch-other", "sym-module-dispatch", "sym-module-other-handlers", "imports", "timeout_demo/dispatch.py", 5, 5, "explicit_import"),
        edge("edge-import-dispatch-client", "sym-module-dispatch", "sym-module-client", "imports", "timeout_demo/dispatch.py", 6, 6, "explicit_import"),
        edge("edge-call-main-start", "sym-main", "sym-start", "calls", "timeout_demo/__main__.py", 14, 14),
        edge("edge-call-start-load", "sym-start", "sym-load-settings", "calls", "timeout_demo/app.py", 18, 18),
        edge("edge-call-start-initialize", "sym-start", "sym-initialize-client", "calls", "timeout_demo/app.py", 19, 19),
        edge("edge-call-start-dispatch", "sym-start", "sym-dispatch", "calls", "timeout_demo/app.py", 20, 20),
    ]
    unresolved = [
        c.UnresolvedReference(
            unresolved_id="unresolved-dispatch-handle",
            index_id=index_id,
            source_symbol_id="sym-dispatch",
            path="timeout_demo/dispatch.py",
            start_line=19,
            end_line=19,
            expression='getattr(module, "handle")',
            reason="The module is selected through HANDLER_MODULES at runtime",
            possible_target_ids=["sym-primary-handle", "sym-secondary-handle"],
        )
    ]
    return c.GraphSnapshot(
        index_id=index_id,
        nodes=nodes,
        static_edges=static_edges,
        unresolved=unresolved,
        candidate_links=candidate_links or [],
    )


def index_snapshot(index_id: str, manifest: dict) -> c.IndexSnapshot:
    return c.IndexSnapshot(
        index_id=index_id,
        repository_id="repository-timeout-demo",
        revision="fixture-timeout-demo-v1",
        dirty=False,
        snapshot_hash=snapshot_hash(manifest),
        status="ready",
    )


def budget(tool_calls_used: int, input_chars_used: int) -> c.RunBudget:
    return c.RunBudget(
        max_tool_calls=12,
        max_seconds=120,
        max_source_lines=200,
        max_total_source_lines=800,
        max_input_chars=60_000,
        max_output_chars=50_000,
        max_events=100,
        tool_calls_used=tool_calls_used,
        input_chars_used=input_chars_used,
    )


def timestamp(offset: int) -> datetime:
    return BASE_TIME + timedelta(seconds=offset)


def frozen_input(index_id: str, issue: c.Issue) -> c.RunInput:
    message = c.SessionMessage(
        message_id="message-fixture-timeout-demo",
        session_id=FIXTURE_SESSION_ID,
        sequence=1,
        source="human",
        content=f"{issue.title}\n{issue.body}",
        created_at=timestamp(0),
    )
    serialized = json.dumps(
        [message.model_dump(mode="json")],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return c.RunInput(
        project_id=FIXTURE_PROJECT_ID,
        session_id=FIXTURE_SESSION_ID,
        index_id=index_id,
        message_end_sequence=1,
        messages=[message],
        input_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        input_chars=len(message.content),
        frozen_at=timestamp(0),
    )


def run(
    run_id: str,
    index_id: str,
    issue: c.Issue,
    run_input: c.RunInput,
    fixture_scenario: c.FixtureScenario,
    status: c.RunStatus,
    tool_calls_used: int,
    end_offset: int,
) -> c.Run:
    return c.Run(
        run_id=run_id,
        project_id=FIXTURE_PROJECT_ID,
        session_id=FIXTURE_SESSION_ID,
        index_id=index_id,
        feature_generation_id=None,
        issue=issue,
        mode="fixture",
        fixture_scenario=fixture_scenario,
        model="fixture:timeout-demo",
        prompt_version="fixture-v1",
        execution_version="run-fixture-v1",
        status=status,
        budget=budget(tool_calls_used, run_input.input_chars),
        message_end_sequence=run_input.message_end_sequence,
        input_hash=run_input.input_hash,
        input_chars=run_input.input_chars,
        created_at=timestamp(0),
        started_at=timestamp(0),
        ended_at=timestamp(end_offset),
    )


def search_request(request_id: str, run_id: str, index_id: str, query: str) -> c.ToolRequest:
    return c.ToolRequest(
        request_id=request_id,
        run_id=run_id,
        index_id=index_id,
        params=c.SearchCodeParams(query=query, limit=10),
    )


def read_request(
    request_id: str,
    run_id: str,
    index_id: str,
    path: str,
    start_line: int,
    end_line: int,
) -> c.ToolRequest:
    return c.ToolRequest(
        request_id=request_id,
        run_id=run_id,
        index_id=index_id,
        params=c.ReadSourceParams(path=path, start_line=start_line, end_line=end_line),
    )


def neighbors_request(request_id: str, run_id: str, index_id: str) -> c.ToolRequest:
    return c.ToolRequest(
        request_id=request_id,
        run_id=run_id,
        index_id=index_id,
        params=c.GetNeighborsParams(symbol_id="sym-start", direction="callees", depth=2, limit=10),
    )


def tool_base(request_id: str, run_id: str, index_id: str, completed_sequence: int) -> dict:
    return {
        "request_id": request_id,
        "run_id": run_id,
        "index_id": index_id,
        "completed_sequence": completed_sequence,
    }


def make_events(events: Iterable[c.Event], run_id: str) -> c.EventSequence:
    return c.EventSequence(run_id=run_id, events=list(events))


def run_started(run_id: str, run_input: c.RunInput, run_model: c.Run) -> c.RunStartedEvent:
    return c.RunStartedEvent(
        run_id=run_id,
        sequence=1,
        timestamp=timestamp(0),
        payload=c.RunStartedPayload(
            project_id=run_input.project_id,
            session_id=run_input.session_id,
            index_id=run_input.index_id,
            issue_id=run_model.issue.issue_id,
            mode="fixture",
            message_end_sequence=run_input.message_end_sequence,
            input_hash=run_input.input_hash,
            feature_generation_id=None,
            execution_version=run_model.execution_version,
            budget=run_model.budget.model_copy(update={"tool_calls_used": 0}),
        ),
    )


def stage_event(run_id: str, sequence: int, stage: str) -> c.StageStartedEvent:
    return c.StageStartedEvent(
        run_id=run_id,
        sequence=sequence,
        timestamp=timestamp(sequence),
        payload=c.StageStartedPayload(stage=stage),  # type: ignore[arg-type]
    )


def tool_started(run_id: str, sequence: int, request: c.ToolRequest) -> c.ToolStartedEvent:
    return c.ToolStartedEvent(
        run_id=run_id,
        sequence=sequence,
        timestamp=timestamp(sequence),
        payload=c.ToolStartedPayload(request=request),
    )


def tool_completed(run_id: str, sequence: int, result: c.ToolSuccessResult) -> c.ToolCompletedEvent:
    return c.ToolCompletedEvent(
        run_id=run_id,
        sequence=sequence,
        timestamp=timestamp(sequence),
        payload=c.ToolCompletedPayload(result=result),
    )


def tool_failed(run_id: str, sequence: int, result: c.ToolFailureResult) -> c.ToolFailedEvent:
    return c.ToolFailedEvent(
        run_id=run_id,
        sequence=sequence,
        timestamp=timestamp(sequence),
        payload=c.ToolFailedPayload(result=result),
    )


def finding_event(run_id: str, sequence: int, finding: c.Finding) -> c.FindingCreatedEvent:
    return c.FindingCreatedEvent(
        run_id=run_id,
        sequence=sequence,
        timestamp=timestamp(sequence),
        payload=c.FindingCreatedPayload(finding=finding),
    )


def graph_event(run_id: str, sequence: int, graph: c.GraphSnapshot) -> c.GraphUpdatedEvent:
    return c.GraphUpdatedEvent(
        run_id=run_id,
        sequence=sequence,
        timestamp=timestamp(sequence),
        payload=c.GraphUpdatedPayload(graph=graph),
    )


def build_success(manifest: dict) -> c.FixtureResponseSet:
    scenario_id = "success-timeout-missing"
    index_id = "index-timeout-demo-v1"
    run_id = "run-success-timeout-missing"
    issue = load_issue("timeout-missing.json")
    index = index_snapshot(index_id, manifest)
    run_input = frozen_input(index_id, issue)
    run_model = run(
        run_id,
        index_id,
        issue,
        run_input,
        scenario_id,
        "succeeded",
        4,
        14,
    )
    current_graph = build_graph(index_id)
    config_evidence = evidence("evidence-timeout-config", index_id, manifest, "timeout_demo/config.py", 9, 25)
    client_evidence = evidence("evidence-timeout-client", index_id, manifest, "timeout_demo/client.py", 16, 19)
    candidate = c.CandidateLink(
        candidate_id="candidate-timeout-flow",
        index_id=index_id,
        source_symbol_id="sym-load-settings",
        target_symbol_id="sym-initialize-client",
        relation="Missing timeout setting flows into client initialization",
        evidence_ids=[config_evidence.evidence_id, client_evidence.evidence_id],
    )
    graph = build_graph(index_id, [candidate])
    finding_config = c.Finding(
        finding_id="finding-timeout-default",
        kind="observation",
        text="load_settings declares DEFAULT_TIMEOUT but does not add it when TIMEOUT_DEMO_TIMEOUT is absent.",
        symbol_ids=["sym-load-settings"],
        evidence_ids=[config_evidence.evidence_id],
    )
    finding_client = c.Finding(
        finding_id="finding-timeout-read",
        kind="observation",
        text="initialize_client reads settings['timeout'], which explains the observed KeyError.",
        symbol_ids=["sym-initialize-client"],
        evidence_ids=[client_evidence.evidence_id],
    )

    search = search_request("request-success-search", run_id, index_id, "timeout default initialize_client")
    read_config = read_request("request-success-config", run_id, index_id, "timeout_demo/config.py", 9, 25)
    neighbors = neighbors_request("request-success-neighbors", run_id, index_id)
    read_client = read_request("request-success-client", run_id, index_id, "timeout_demo/client.py", 16, 19)
    symbols = {node.symbol_id: node for node in current_graph.nodes}
    edges = {edge.edge_id: edge for edge in current_graph.static_edges}
    results: list[c.ToolResult] = [
        c.SearchCodeSuccess(
            **tool_base(search.request_id, run_id, index_id, 4),
            matches=[
                c.SearchMatch(rank=1, symbol=symbols["sym-load-settings"], matched_terms=["timeout", "default"]),
                c.SearchMatch(rank=2, symbol=symbols["sym-initialize-client"], matched_terms=["timeout", "initialize"]),
            ],
        ),
        c.ReadSourceSuccess(
            **tool_base(read_config.request_id, run_id, index_id, 6),
            evidence=config_evidence,
        ),
        c.GetNeighborsSuccess(
            **tool_base(neighbors.request_id, run_id, index_id, 8),
            neighbors=[c.Neighbor(symbol=symbols["sym-initialize-client"], edge=edges["edge-call-start-initialize"])],
        ),
        c.ReadSourceSuccess(
            **tool_base(read_client.request_id, run_id, index_id, 10),
            evidence=client_evidence,
        ),
    ]
    output = c.ModelOutput(
        output_id="model-output-success-timeout-missing",
        run_id=run_id,
        index_id=index_id,
        generated_after_sequence=11,
        outcome="candidate_found",
        summary="定位到 timeout 默认值缺失与客户端初始化读取之间的候选关联。",
        findings=[finding_config, finding_client],
        candidate_links=[candidate],
        evidence_ids=[config_evidence.evidence_id, client_evidence.evidence_id],
    )
    report = c.Report(
        report_id="report-success-timeout-missing",
        run_id=run_id,
        project_id=FIXTURE_PROJECT_ID,
        session_id=FIXTURE_SESSION_ID,
        index_id=index_id,
        status="succeeded",
        outcome="candidate_found",
        summary="固定样本显示缺少 timeout 会在客户端初始化时抛出 KeyError；候选位置和证据已绑定当前快照。",
        graph=graph,
        evidence=[config_evidence, client_evidence],
        findings=[finding_config, finding_client],
        candidate_links=[candidate],
        terminal_sequence=14,
    )
    events = make_events(
        [
            run_started(run_id, run_input, run_model),
            stage_event(run_id, 2, "retrieval"),
            tool_started(run_id, 3, search),
            tool_completed(run_id, 4, results[0]),
            tool_started(run_id, 5, read_config),
            tool_completed(run_id, 6, results[1]),
            tool_started(run_id, 7, neighbors),
            tool_completed(run_id, 8, results[2]),
            tool_started(run_id, 9, read_client),
            tool_completed(run_id, 10, results[3]),
            stage_event(run_id, 11, "analysis"),
            graph_event(run_id, 12, graph),
            finding_event(run_id, 13, finding_client),
            c.RunCompletedEvent(
                run_id=run_id,
                sequence=14,
                timestamp=timestamp(14),
                payload=c.RunCompletedPayload(
                    status="succeeded",
                    outcome="candidate_found",
                    report_id=report.report_id,
                ),
            ),
        ],
        run_id,
    )
    return c.FixtureResponseSet(
        scenario_id=scenario_id,
        mode="fixture",
        index=index,
        run=run_model,
        input=run_input,
        tool_results=results,
        model_output=output,
        report=report,
        events=events,
    )


def build_insufficient(manifest: dict) -> c.FixtureResponseSet:
    scenario_id = "insufficient-external-service"
    index_id = "index-timeout-demo-v1"
    run_id = "run-insufficient-external-service"
    issue = load_issue("external-service.json")
    index = index_snapshot(index_id, manifest)
    run_input = frozen_input(index_id, issue)
    run_model = run(
        run_id,
        index_id,
        issue,
        run_input,
        scenario_id,
        "succeeded",
        1,
        7,
    )
    search = search_request("request-insufficient-search", run_id, index_id, "external payment service empty response")
    result = c.SearchCodeSuccess(
        **tool_base(search.request_id, run_id, index_id, 4),
        matches=[],
    )
    finding = c.Finding(
        finding_id="finding-insufficient-external",
        kind="observation",
        text="The fixture contains no local symbol or source evidence for the external payment service.",
    )
    output = c.ModelOutput(
        output_id="model-output-insufficient-external-service",
        run_id=run_id,
        index_id=index_id,
        generated_after_sequence=4,
        outcome="insufficient_evidence",
        summary="当前样本没有外部支付服务的本地实现或证据，不能确认根因。",
        findings=[finding],
    )
    report = c.Report(
        report_id="report-insufficient-external-service",
        run_id=run_id,
        project_id=FIXTURE_PROJECT_ID,
        session_id=FIXTURE_SESSION_ID,
        index_id=index_id,
        status="succeeded",
        outcome="insufficient_evidence",
        summary="证据不足：需要外部服务日志、集成样本或对应仓库才能继续核查。",
        graph=c.GraphSnapshot(index_id=index_id),
        findings=[finding],
        terminal_sequence=7,
    )
    events = make_events(
        [
            run_started(run_id, run_input, run_model),
            stage_event(run_id, 2, "retrieval"),
            tool_started(run_id, 3, search),
            tool_completed(run_id, 4, result),
            stage_event(run_id, 5, "analysis"),
            finding_event(run_id, 6, finding),
            c.RunCompletedEvent(
                run_id=run_id,
                sequence=7,
                timestamp=timestamp(7),
                payload=c.RunCompletedPayload(
                    status="succeeded",
                    outcome="insufficient_evidence",
                    report_id=report.report_id,
                ),
            ),
        ],
        run_id,
    )
    return c.FixtureResponseSet(
        scenario_id=scenario_id,
        mode="fixture",
        index=index,
        run=run_model,
        input=run_input,
        tool_results=[result],
        model_output=output,
        report=report,
        events=events,
    )


def build_tool_failure(manifest: dict) -> c.FixtureResponseSet:
    scenario_id = "tool-failure-read-source"
    index_id = "index-timeout-demo-v1"
    run_id = "run-tool-failure-read-source"
    issue = load_issue("timeout-missing.json")
    index = index_snapshot(index_id, manifest)
    run_input = frozen_input(index_id, issue)
    run_model = run(
        run_id,
        index_id,
        issue,
        run_input,
        scenario_id,
        "failed",
        3,
        9,
    )
    graph = build_graph(index_id)
    config_evidence = evidence("evidence-tool-config", index_id, manifest, "timeout_demo/config.py", 12, 25)
    search = search_request("request-failure-search", run_id, index_id, "timeout default initialize_client")
    read_config = read_request("request-failure-config", run_id, index_id, "timeout_demo/config.py", 12, 25)
    read_client = read_request("request-failure-client", run_id, index_id, "timeout_demo/client.py", 16, 19)
    symbols = {node.symbol_id: node for node in graph.nodes}
    results: list[c.ToolResult] = [
        c.SearchCodeSuccess(
            **tool_base(search.request_id, run_id, index_id, 4),
            matches=[c.SearchMatch(rank=1, symbol=symbols["sym-load-settings"], matched_terms=["timeout"])],
        ),
        c.ReadSourceSuccess(
            **tool_base(read_config.request_id, run_id, index_id, 6),
            evidence=config_evidence,
        ),
        c.ReadSourceFailure(
            **tool_base(read_client.request_id, run_id, index_id, 8),
            error=c.ToolError(
                code="fixture_read_source_error",
                message="Deterministic fixture read_source failure",
                retryable=False,
            ),
        ),
    ]
    finding = c.Finding(
        finding_id="finding-failure-before-client",
        kind="observation",
        text="The available configuration excerpt was collected before read_source failed on client.py.",
        symbol_ids=["sym-load-settings"],
        evidence_ids=[config_evidence.evidence_id],
    )
    output = c.ModelOutput(
        output_id="model-output-tool-failure-read-source",
        run_id=run_id,
        index_id=index_id,
        generated_after_sequence=8,
        outcome="failed",
        summary="读取客户端源码的工具调用失败，停止生成候选结论并保留此前证据。",
        findings=[finding],
        evidence_ids=[config_evidence.evidence_id],
    )
    report = c.Report(
        report_id="report-tool-failure-read-source",
        run_id=run_id,
        project_id=FIXTURE_PROJECT_ID,
        session_id=FIXTURE_SESSION_ID,
        index_id=index_id,
        status="failed",
        outcome="failed",
        summary="工具失败：read_source 返回确定性 fixture 错误；失败前的配置证据仍被保留。",
        graph=graph,
        evidence=[config_evidence],
        findings=[finding],
        terminal_sequence=9,
    )
    events = make_events(
        [
            run_started(run_id, run_input, run_model),
            stage_event(run_id, 2, "retrieval"),
            tool_started(run_id, 3, search),
            tool_completed(run_id, 4, results[0]),
            tool_started(run_id, 5, read_config),
            tool_completed(run_id, 6, results[1]),
            tool_started(run_id, 7, read_client),
            tool_failed(run_id, 8, results[2]),
            c.RunFailedEvent(
                run_id=run_id,
                sequence=9,
                timestamp=timestamp(9),
                payload=c.RunFailedPayload(
                    status="failed",
                    report_id=report.report_id,
                    error=c.RunError(
                        code="tool_failed",
                        message="read_source failed with fixture_read_source_error",
                    ),
                ),
            ),
        ],
        run_id,
    )
    return c.FixtureResponseSet(
        scenario_id=scenario_id,
        mode="fixture",
        index=index,
        run=run_model,
        input=run_input,
        tool_results=results,
        model_output=output,
        report=report,
        events=events,
    )


def write_bundle(bundle: c.FixtureResponseSet) -> None:
    output_path = RESPONSES / f"{bundle.scenario_id}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output_path)


def main() -> None:
    manifest = load_manifest()
    write_bundle(build_success(manifest))
    write_bundle(build_insufficient(manifest))
    write_bundle(build_tool_failure(manifest))


if __name__ == "__main__":
    main()
