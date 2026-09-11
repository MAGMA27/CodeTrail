"""Offline validation for contract schemas and fixture response bundles."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts import (
    EvidenceReference,
    FixtureResponseSet,
    McpSourceResponse,
    ReadSourceParams,
    ReadSourceSuccess,
    Project,
    SourceIndexResponse,
    SseEventFrame,
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


def test_insufficient_bundle_does_not_claim_a_root_cause() -> None:
    bundle = FixtureResponseSet.model_validate(raw_bundle("insufficient-external-service"))

    assert bundle.report.status == "succeeded"
    assert bundle.report.outcome == "insufficient_evidence"
    assert bundle.report.evidence == []
    assert bundle.report.candidate_links == []


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
        SseEventFrame(id=99, event=event.type, data=event)


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
