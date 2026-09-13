from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

import evals.model_pipeline as model_pipeline
from evals.model_models import ModelBudgetLimits
from evals.model_pipeline import (
    OfflineFakeIssueAdapter,
    _complete_case,
    _new_case_app,
    _start_run,
    build_dry_run_plan,
    default_method,
    map_report_candidates,
    run_model_evaluation,
)
from evals.pipeline import load_frozen_dataset
from app.contracts import CandidateLink, CancelRunRequest, Evidence, Finding, GraphSnapshot, Report, RunBudget, Symbol
from app.feature_generation import ModelCall


def test_dry_run_plan_is_gold_free() -> None:
    frozen = load_frozen_dataset()
    plan = build_dry_run_plan(frozen)

    serialized = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    assert "expected_files" not in serialized
    assert "defect_category" not in serialized
    assert all("annotation" not in case.issue_input for case in plan.cases)
    assert len(plan.cases) == 6


def test_offline_fake_real_run_scores_and_resumes_without_duplicate_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = load_frozen_dataset()
    adapters: list[OfflineFakeIssueAdapter] = []
    phases: list[str] = []

    original_loader = model_pipeline.load_annotations_for_dataset

    def load_annotations_after_execution(dataset):
        phases.append("annotations")
        return original_loader(dataset)

    monkeypatch.setattr(model_pipeline, "load_annotations_for_dataset", load_annotations_after_execution)

    def factory(_mode: str) -> OfflineFakeIssueAdapter:
        adapter = OfflineFakeIssueAdapter()
        adapters.append(adapter)
        phases.append("run")
        return adapter

    output_dir = tmp_path / "results"
    runtime_dir = tmp_path / "runtime"
    report, comparison = run_model_evaluation(
        frozen,
        runtime_dir=runtime_dir,
        output_dir=output_dir,
        adapter_factory=factory,
    )

    assert report.summary.total_cases == 6
    assert report.summary.failed_cases == 0
    assert report.summary.insufficient_abstention_rate.numerator == 2
    assert report.summary.insufficient_abstention_rate.denominator == 2
    assert report.summary.insufficient_false_positive_rate.numerator == 0
    assert report.cases[2].execution.run_outcome == "insufficient_evidence"
    assert report.cases[2].score.abstention_kind == "model_insufficient_evidence"
    assert all(item.execution.report_path for item in report.cases)
    assert all((output_dir / item.execution.report_path).is_file() for item in report.cases if item.execution.report_path)
    assert comparison.groups[0].model.total_cases == 6
    assert len(adapters) == 6
    assert phases == ["run"] * 6 + ["annotations"]

    resumed_adapters: list[OfflineFakeIssueAdapter] = []

    def resumed_factory(_mode: str) -> OfflineFakeIssueAdapter:
        adapter = OfflineFakeIssueAdapter()
        resumed_adapters.append(adapter)
        return adapter

    resumed, _ = run_model_evaluation(
        frozen,
        runtime_dir=runtime_dir,
        output_dir=output_dir,
        adapter_factory=resumed_factory,
    )

    assert resumed.summary == report.summary
    assert resumed_adapters == []
    raw_state = json.loads((output_dir / "raw-state.json").read_text(encoding="utf-8"))
    assert all("expected_files" not in json.dumps(value, ensure_ascii=False) for value in raw_state["results"].values())


def test_model_budget_failure_stays_in_the_denominator(tmp_path: Path) -> None:
    frozen = load_frozen_dataset()
    method = default_method(ModelBudgetLimits(max_model_calls=1))
    report, _ = run_model_evaluation(
        frozen,
        split="holdout",
        method=method,
        runtime_dir=tmp_path / "runtime",
        output_dir=tmp_path / "results",
    )

    applicable_failures = [
        item for item in report.cases if item.score.applicable and item.score.status == "failed"
    ]
    assert applicable_failures
    assert report.summary.failed_cases >= 1
    assert all(not item.execution.candidates for item in applicable_failures)
    assert all(item.score.file_hit_at_1 is False for item in applicable_failures)


class _BlockingAdapter:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def generate(self, prompt: str, **_: object) -> ModelCall:
        del prompt
        self.started.set()
        self.release.wait(2)
        raise RuntimeError("released after cancellation")

    def repair(self, prompt: str, invalid_output: str, validation_error: str, **_: object) -> ModelCall:
        del prompt, invalid_output, validation_error
        raise AssertionError("repair must not run after cancellation")


def test_real_run_cancellation_is_preserved_by_model_evaluation(tmp_path: Path) -> None:
    frozen = load_frozen_dataset()
    case = frozen.cases[0]
    source_root = tmp_path / "source"
    shutil.copytree(case.source_root, source_root)
    adapter = _BlockingAdapter()
    application = _new_case_app(source_root, tmp_path / "data", lambda _mode: adapter)
    method = default_method(ModelBudgetLimits(max_seconds=30))
    checkpoint = _start_run(
        application,
        case,
        method,
        RunBudget(**method.budget.model_dump(mode="python")),
        frozen.dataset.version,
    )
    assert adapter.started.wait(2)
    service = application.state.workspace_service
    service.cancel_run(
        checkpoint.project_id,
        checkpoint.session_id,
        checkpoint.run_id,
        CancelRunRequest(reason="test cancellation"),
    )
    adapter.release.set()
    raw = _complete_case(application, case, checkpoint, method, tmp_path / "results")

    assert raw.run_status == "cancelled"
    assert raw.execution_status == "cancelled"
    assert raw.candidates == []
    assert raw.failure_stage == "run"


def _valid_report(case) -> Report:
    index_id = "run-index"
    source_line = case.source_root.joinpath("reader.py").read_text(encoding="utf-8").splitlines()[5]
    symbol = Symbol(
        symbol_id="run-symbol",
        index_id=index_id,
        path="reader.py",
        qualified_name="reader.normalize_row",
        kind="function",
        start_line=6,
        end_line=6,
    )
    evidence = Evidence(
        evidence_id="run-evidence",
        index_id=index_id,
        path="reader.py",
        start_line=6,
        end_line=6,
        excerpt=source_line,
        content_hash=case.file_hashes["reader.py"],
        kind="source",
    )
    link = CandidateLink(
        candidate_id="run-candidate",
        index_id=index_id,
        source_symbol_id=symbol.symbol_id,
        relation="fake candidate",
        evidence_ids=[evidence.evidence_id],
    )
    finding = Finding(
        finding_id="run-finding",
        kind="hypothesis",
        text="same location duplicated through a finding",
        symbol_ids=[symbol.symbol_id],
        evidence_ids=[evidence.evidence_id],
    )
    return Report(
        report_id="run-report",
        run_id="run",
        project_id="project",
        session_id="session",
        index_id=index_id,
        status="succeeded",
        outcome="candidate_found",
        fixture_mode=False,
        summary="candidate",
        graph=GraphSnapshot(index_id=index_id, nodes=[symbol], candidate_links=[link]),
        evidence=[evidence],
        findings=[finding],
        candidate_links=[link],
        terminal_sequence=1,
    )


def test_final_candidate_mapping_deduplicates_and_checks_frozen_evidence() -> None:
    case = load_frozen_dataset().cases[0]
    candidates, errors = map_report_candidates(_valid_report(case), case)

    assert errors == []
    assert len(candidates) == 1
    assert candidates[0].origin == "candidate_link"
    assert candidates[0].evidence_legal is True


def test_final_candidate_mapping_rejects_wrong_evidence_hash() -> None:
    case = load_frozen_dataset().cases[0]
    report = _valid_report(case)
    bad_evidence = report.evidence[0].model_copy(update={"content_hash": "0" * 64})
    bad_report = report.model_copy(update={"evidence": [bad_evidence]})

    candidates, errors = map_report_candidates(bad_report, case)

    assert candidates == []
    assert any("hash differs" in error for error in errors)


def test_external_provider_is_not_implicitly_called() -> None:
    with pytest.raises(ValueError, match="P4-E2b"):
        run_model_evaluation(load_frozen_dataset(), provider="external")
