"""Offline validation for contract schemas and fixture response bundles."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts import (
    CodeEdge,
    CodeExplanation,
    CodeUnderstandingFinal,
    ContextualCall,
    EvidenceReference,
    ExplanationStep,
    FixtureResponseSet,
    GetNeighborsSuccess,
    IndexFreshness,
    McpSourceResponse,
    ModuleSymbolsPageResponse,
    Neighbor,
    NeighborsIndexResponse,
    ReadSourceParams,
    ReadSourceSuccess,
    Project,
    SourceLocation,
    SourceIndexResponse,
    Symbol,
    SseEventFrame,
    SseStreamEnd,
    SseStreamEndFrame,
)
from app.export_schema import build_schema_document


ROOT = Path(__file__).resolve().parents[3]
RESPONSES = ROOT / "fixtures" / "responses"
SCHEMA_PATH = ROOT / "contracts" / "schemas" / "codetrail.json"
SCENARIOS = (
    "success-timeout-missing",
    "insufficient-external-service",
    "tool-failure-read-source",
)


def raw_bundle(scenario_id: str) -> dict:
    return json.loads((RESPONSES / f"{scenario_id}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_fixture_response_bundle_is_valid(scenario_id: str) -> None:
    bundle = FixtureResponseSet.model_validate(raw_bundle(scenario_id))

    assert bundle.scenario_id == scenario_id
    assert bundle.index.index_id == "index-timeout-demo-v1"
    assert bundle.events.events[-1].type in {
        "run.completed",
        "run.failed",
        "run.cancelled",
        "run.interrupted",
    }


def test_success_bundle_keeps_static_and_candidate_edges_separate() -> None:
    bundle = FixtureResponseSet.model_validate(raw_bundle("success-timeout-missing"))

    assert bundle.report.outcome == "candidate_found"
    assert bundle.report.candidate_links
    assert all(edge.kind != "candidate_link" for edge in bundle.report.graph.static_edges)
    assert bundle.report.graph.unresolved[0].possible_target_ids == [
        "sym-primary-handle",
        "sym-secondary-handle",
    ]


def test_contextual_call_contract_binds_returned_activation_edge() -> None:
    index_id = "index-context"
    activation = Symbol(
        symbol_id="symbol-activation",
        index_id=index_id,
        path="sample/factory.py",
        qualified_name="sample.factory.create",
        kind="function",
        start_line=1,
        end_line=20,
    )
    source = Symbol(
        symbol_id="symbol-source",
        index_id=index_id,
        path="sample/workspace.py",
        qualified_name="sample.workspace.Workspace.read",
        kind="function",
        start_line=1,
        end_line=8,
    )
    target = Symbol(
        symbol_id="symbol-target",
        index_id=index_id,
        path="sample/backend.py",
        qualified_name="sample.backend.Backend.read_source",
        kind="function",
        start_line=1,
        end_line=5,
    )
    activation_location = SourceLocation(
        path="sample/factory.py", start_line=12, end_line=12
    )
    edge = CodeEdge(
        edge_id="edge-activation",
        index_id=index_id,
        source_id=activation.symbol_id,
        target_id=source.symbol_id,
        kind="calls",
        extraction_method="ast",
        source_location=activation_location,
    )
    contextual = ContextualCall(
        context_id="context-one",
        index_id=index_id,
        activation_edge_id=edge.edge_id,
        activation_source=activation,
        source=source,
        target=target,
        activation_location=activation_location,
        source_location=SourceLocation(path=source.path, start_line=6, end_line=6),
        receiver_constructor_location=SourceLocation(
            path="sample/factory.py", start_line=10, end_line=10
        ),
        field_assignment_location=SourceLocation(
            path=source.path, start_line=3, end_line=3
        ),
        field_name="backend",
    )
    neighbor = Neighbor(symbol=source, edge=edge)
    response = NeighborsIndexResponse(
        index_id=index_id,
        root=activation,
        nodes=[activation, source],
        neighbors=[neighbor],
        contextual_calls=[contextual],
    )
    assert response.contextual_calls == [contextual]

    tool_result = GetNeighborsSuccess(
        request_id="request-one",
        run_id="run-one",
        index_id=index_id,
        completed_sequence=1,
        root=activation,
        nodes=[activation, source],
        neighbors=[neighbor],
        contextual_calls=[contextual],
    )
    assert tool_result.contextual_calls == [contextual]
    assert GetNeighborsSuccess(
        request_id="request-legacy",
        run_id="run-legacy",
        index_id=index_id,
        completed_sequence=1,
        neighbors=[],
    ).contextual_calls == []

    invalid_payload = tool_result.model_dump(mode="python")
    invalid_payload["contextual_calls"][0]["activation_edge_id"] = "edge-missing"
    with pytest.raises(ValidationError, match="returned activation edge"):
        GetNeighborsSuccess.model_validate(invalid_payload)

    mismatched_source = source.model_copy(update={"qualified_name": "sample.other.read"})
    conflicting_payload = tool_result.model_dump(mode="python")
    conflicting_payload["neighbors"][0]["symbol"] = mismatched_source.model_dump(
        mode="python"
    )
    with pytest.raises(ValidationError, match="conflicting symbol metadata"):
        GetNeighborsSuccess.model_validate(conflicting_payload)


def test_insufficient_bundle_does_not_claim_a_root_cause() -> None:
    bundle = FixtureResponseSet.model_validate(raw_bundle("insufficient-external-service"))

    assert bundle.report.status == "succeeded"
    assert bundle.report.outcome == "insufficient_evidence"
    assert bundle.report.evidence == []
    assert bundle.report.candidate_links == []
    assert bundle.report.explanation is None


def test_code_understanding_requires_ordered_supported_steps() -> None:
    explanation = CodeExplanation(
        coverage="partial",
        steps=[
            ExplanationStep(
                step_id="step-entry",
                kind="confirmed",
                title="入口读取配置",
                description="入口函数读取固定配置源码。",
                evidence_ids=["evidence-entry"],
                symbol_ids=["symbol-entry"],
            ),
            ExplanationStep(
                step_id="step-gap",
                kind="unknown",
                title="运行时分支",
                description="当前静态工具结果无法确认运行时分支。",
            ),
        ],
    )
    final = CodeUnderstandingFinal(
        index_id="index-one",
        summary="已解释可见的入口和未确认边界。",
        explanation=explanation,
        evidence_ids=["evidence-entry"],
    )

    assert final.explanation.steps[0].symbol_ids == ["symbol-entry"]

    with pytest.raises(ValidationError):
        ExplanationStep(
            step_id="step-invalid",
            kind="confirmed",
            title="没有证据",
            description="确认步骤不能没有证据。",
        )
    with pytest.raises(ValidationError):
        CodeExplanation(
            coverage="complete",
            steps=[
                ExplanationStep(
                    step_id="step-unknown",
                    kind="unknown",
                    title="未知",
                    description="完整解释不能包含未确认步骤。",
                )
            ],
        )
    with pytest.raises(ValidationError):
        CodeUnderstandingFinal(
            index_id="index-one",
            summary="证据集合与步骤不一致。",
            explanation=explanation,
            evidence_ids=["evidence-entry", "evidence-unused"],
        )


def test_schema_artifact_includes_code_understanding_contracts() -> None:
    models = build_schema_document()["models"]

    assert {"CodeExplanation", "CodeUnderstandingFinal", "ExplanationStep"} <= models.keys()


def test_schema_artifact_includes_freshness_contracts() -> None:
    models = build_schema_document()["models"]

    assert {
        "IndexFreshness",
        "IndexRefreshResponse",
        "McpIndexFreshnessResponse",
        "McpIndexRefreshResponse",
    } <= models.keys()


def test_schema_artifact_includes_module_symbol_paging_contracts() -> None:
    models = build_schema_document()["models"]

    assert {"ModuleSymbolsPageResponse", "McpModuleSymbolsResponse"} <= models.keys()


def test_module_symbol_page_contract_keeps_definitions_inside_the_module_snapshot() -> None:
    module = Symbol(
        symbol_id="module-symbol",
        index_id="index-symbol-page",
        path="pkg/module.py",
        qualified_name="pkg.module",
        kind="module",
        start_line=1,
        end_line=5,
    )
    function = Symbol(
        symbol_id="function-symbol",
        index_id="index-symbol-page",
        path=module.path,
        qualified_name="pkg.module.run",
        kind="function",
        start_line=3,
        end_line=4,
    )

    page = ModuleSymbolsPageResponse(
        project_id="project-symbol-page",
        index_id=module.index_id,
        module=module,
        symbols=[function],
        next_cursor="opaque-cursor",
        has_more=True,
    )
    assert page.symbols == [function]

    with pytest.raises(ValidationError, match="module scope"):
        ModuleSymbolsPageResponse(
            project_id="project-symbol-page",
            index_id=module.index_id,
            module=module,
            symbols=[function.model_copy(update={"path": "pkg/other.py"})],
        )


def test_failed_tool_bundle_preserves_prior_evidence_without_fake_source() -> None:
    bundle = FixtureResponseSet.model_validate(raw_bundle("tool-failure-read-source"))
    failures = [result for result in bundle.tool_results if result.kind == "read_source.failed"]

    assert bundle.report.status == "failed"
    assert bundle.report.evidence
    assert len(failures) == 1
    assert all(not isinstance(result, ReadSourceSuccess) for result in failures)
    assert not hasattr(failures[0], "evidence")


def test_schema_artifact_is_reproducible() -> None:
    expected = json.dumps(build_schema_document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    assert SCHEMA_PATH.read_text(encoding="utf-8") == expected


def test_schema_artifact_includes_session_contracts() -> None:
    models = build_schema_document()["models"]

    assert {
        "AppendSessionMessageRequest",
        "CreateSessionRequest",
        "Session",
        "SessionMessage",
        "SessionMessageResponse",
        "SessionMessagesResponse",
        "SessionListResponse",
        "SessionReference",
        "SessionResponse",
        "McpSessionListResponse",
        "McpSessionMessageResponse",
        "McpSessionMessagesResponse",
        "McpSessionResponse",
    } <= models.keys()


def test_schema_artifact_includes_run_impact_contracts() -> None:
    models = build_schema_document()["models"]

    assert {"ImpactPath", "RunImpactResponse", "McpRunImpactResponse"} <= models.keys()


def test_cross_snapshot_reference_is_rejected() -> None:
    payload = raw_bundle("success-timeout-missing")
    payload["report"]["index_id"] = "index-other"

    with pytest.raises(ValidationError):
        FixtureResponseSet.model_validate(payload)


def test_unreturned_evidence_reference_is_rejected() -> None:
    payload = raw_bundle("success-timeout-missing")
    payload["model_output"]["evidence_ids"] = ["evidence-not-returned"]

    with pytest.raises(ValidationError):
        FixtureResponseSet.model_validate(payload)


def test_evidence_excerpt_line_range_is_rejected_when_out_of_range() -> None:
    payload = raw_bundle("success-timeout-missing")
    payload["report"]["evidence"][0]["end_line"] = 200

    with pytest.raises(ValidationError):
        FixtureResponseSet.model_validate(payload)


def test_invalid_event_payload_is_rejected() -> None:
    payload = raw_bundle("success-timeout-missing")
    payload["events"]["events"][0]["payload"] = {"index_id": "index-timeout-demo-v1"}

    with pytest.raises(ValidationError):
        FixtureResponseSet.model_validate(payload)


@pytest.mark.parametrize("sequence", [1, 3])
def test_duplicate_or_reverse_event_sequence_is_rejected(sequence: int) -> None:
    payload = raw_bundle("success-timeout-missing")
    payload["events"]["events"][1]["sequence"] = sequence

    with pytest.raises(ValidationError):
        FixtureResponseSet.model_validate(payload)


def test_event_after_terminal_state_is_rejected() -> None:
    payload = raw_bundle("success-timeout-missing")
    terminal_event = copy.deepcopy(payload["events"]["events"][-1])
    terminal_event["sequence"] += 1
    payload["events"]["events"].append(terminal_event)

    with pytest.raises(ValidationError):
        FixtureResponseSet.model_validate(payload)


def test_read_source_budget_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReadSourceParams(path="timeout_demo/config.py", start_line=1, end_line=201)


def test_sse_frame_metadata_must_match_event() -> None:
    bundle = FixtureResponseSet.model_validate(raw_bundle("insufficient-external-service"))
    event = bundle.events.events[0]

    with pytest.raises(ValidationError):
        SseEventFrame(id=f"{event.run_id}:99", event=event.type, data=event)


def test_sse_stream_end_requires_a_terminal_run_status() -> None:
    bundle = FixtureResponseSet.model_validate(raw_bundle("insufficient-external-service"))
    event = bundle.events.events[-1]
    frame = SseStreamEndFrame(
        id=f"{event.run_id}:{event.sequence}",
        data=SseStreamEnd(
            run_id=event.run_id,
            sequence=event.sequence,
            reason="terminal",
            status="succeeded",
        ),
    )

    assert frame.data.status == "succeeded"
    with pytest.raises(ValidationError):
        SseStreamEnd(
            run_id=event.run_id,
            sequence=event.sequence,
            reason="terminal",
            status="running",
        )


def test_project_contract_keeps_latest_and_building_indexes_separate() -> None:
    created_at = datetime.now(timezone.utc)
    project = Project(
        project_id="project-one",
        name="Project One",
        repository_id="repo-one",
        created_at=created_at,
        available_index_ids=["index-one"],
        latest_index_id="index-one",
        building_index_id="pending-index",
    )

    assert project.latest_index_id == "index-one"
    with pytest.raises(ValidationError):
        Project(
            project_id="project-one",
            name="Project One",
            repository_id="repo-one",
            created_at=created_at,
            available_index_ids=["index-one", "index-one"],
        )


def test_github_freshness_keeps_remote_identity_separate_from_index_rules() -> None:
    checked_at = datetime.now(timezone.utc)
    freshness = IndexFreshness(
        project_id="project-one",
        baseline_index_id="index-one",
        status="changed",
        checked_at=checked_at,
        scan_complete=True,
        current_rules_version="rules-v2",
        reasons=["rules_changed"],
        remote_status="consistent",
        remote_source_url="https://github.com/owner/repo",
        remote_requested_ref="main",
        remote_resolved_ref="main",
        remote_resolved_commit="a" * 40,
    )

    assert freshness.remote_status == "consistent"
    assert freshness.status == "changed"
    with pytest.raises(ValidationError):
        IndexFreshness(
            project_id="project-one",
            baseline_index_id="index-one",
            status="consistent",
            checked_at=checked_at,
            scan_complete=True,
            remote_status="unknown",
        )


def test_mcp_source_reference_must_match_immutable_evidence() -> None:
    bundle = FixtureResponseSet.model_validate(raw_bundle("success-timeout-missing"))
    tool_result = next(result for result in bundle.tool_results if isinstance(result, ReadSourceSuccess))
    source = SourceIndexResponse(index_id=tool_result.index_id, evidence=tool_result.evidence)
    reference = EvidenceReference(
        project_id="project-one",
        index_id=tool_result.index_id,
        path=tool_result.evidence.path,
        start_line=tool_result.evidence.start_line,
        end_line=tool_result.evidence.end_line,
        content_hash=tool_result.evidence.content_hash,
        evidence_id=tool_result.evidence.evidence_id,
        url="http://localhost:3000/?project_id=project-one",
    )

    response = McpSourceResponse(ok=True, result=source, reference=reference)
    assert response.reference is not None

    with pytest.raises(ValidationError):
        McpSourceResponse(
            ok=True,
            result=source,
            reference=reference.model_copy(update={"content_hash": "0" * 64}),
        )
