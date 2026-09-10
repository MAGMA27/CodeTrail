"""Offline validation for contract schemas and fixture response bundles."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts import (
    FixtureResponseSet,
    ReadSourceParams,
    ReadSourceSuccess,
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
