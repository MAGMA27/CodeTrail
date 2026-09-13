from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

import pytest

import evals.model_pipeline as model_pipeline
from evals.model_models import EvaluationBudgetLimits, ModelBudgetLimits
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
from evals.pipeline import FrozenDataset, load_frozen_dataset
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
    assert report.evaluation_usage.cases == 6
    assert report.evaluation_reserved.cases == 0
    assert report.evaluation_usage.input_tokens is None
    assert report.evaluation_usage.output_tokens is None

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


def test_cumulative_budget_preserves_denominator_and_does_not_start_skipped_runs(tmp_path: Path) -> None:
    frozen = load_frozen_dataset()
    adapters: list[str] = []

    def factory(_mode: str):
        adapters.append("started")
        return OfflineFakeIssueAdapter()

    report, _ = run_model_evaluation(
        frozen,
        method=default_method(ModelBudgetLimits(max_model_calls=1)),
        evaluation_budget=EvaluationBudgetLimits(max_cases=2),
        runtime_dir=tmp_path / "runtime",
        output_dir=tmp_path / "results",
        adapter_factory=factory,
    )

    assert report.summary.total_cases == 6
    assert report.summary.failed_cases == 2
    assert report.summary.skipped_cases == 4
    assert [item.execution.execution_status for item in report.cases[2:]] == ["budget_skipped"] * 4
    assert len(adapters) == 2
    assert report.evaluation_usage.cases == 2
    assert report.evaluation_reserved.cases == 0
    assert not any(
        (tmp_path / "results" / "runs" / item.score.case_id / "report.json").exists()
        for item in report.cases[2:]
    )


def test_budget_rejection_before_start_creates_no_run_or_adapter(tmp_path: Path) -> None:
    frozen = load_frozen_dataset()
    adapters: list[str] = []

    def factory(_mode: str):
        adapters.append("unexpected")
        raise AssertionError("budget rejection must happen before creating a Run")

    report, _ = run_model_evaluation(
        frozen,
        evaluation_budget=EvaluationBudgetLimits(max_model_calls=0),
        runtime_dir=tmp_path / "runtime",
        output_dir=tmp_path / "results",
        adapter_factory=factory,
    )

    assert all(item.execution.execution_status == "budget_skipped" for item in report.cases)
    assert report.summary.skipped_cases == 6
    assert report.evaluation_usage.cases == 0
    assert adapters == []
    assert not (tmp_path / "runtime").exists()


def test_cumulative_model_call_budget_stops_before_next_run(tmp_path: Path) -> None:
    frozen = load_frozen_dataset()
    adapters: list[str] = []

    def factory(_mode: str):
        adapters.append("started")
        return OfflineFakeIssueAdapter()

    report, _ = run_model_evaluation(
        frozen,
        method=default_method(ModelBudgetLimits(max_model_calls=4)),
        evaluation_budget=EvaluationBudgetLimits(max_model_calls=4),
        runtime_dir=tmp_path / "runtime",
        output_dir=tmp_path / "results",
        adapter_factory=factory,
    )

    assert report.cases[0].execution.execution_status == "succeeded"
    assert all(item.execution.execution_status == "budget_skipped" for item in report.cases[1:])
    assert report.summary.failed_cases == 0
    assert report.summary.skipped_cases == 5
    assert report.evaluation_usage.model_calls == 3
    assert len(adapters) == 1


def test_first_failed_run_does_not_stop_following_cases(tmp_path: Path) -> None:
    frozen = load_frozen_dataset()

    class FailingAdapter:
        def generate(self, prompt: str, **_: object) -> ModelCall:
            del prompt
            raise RuntimeError("first provider failure")

        def repair(self, prompt: str, invalid_output: str, validation_error: str, **_: object) -> ModelCall:
            del prompt, invalid_output, validation_error
            raise AssertionError("repair must not run after provider failure")

    created = 0

    def factory(_mode: str):
        nonlocal created
        created += 1
        return FailingAdapter() if created == 1 else OfflineFakeIssueAdapter()

    report, _ = run_model_evaluation(
        frozen,
        split="holdout",
        runtime_dir=tmp_path / "runtime",
        output_dir=tmp_path / "results",
        adapter_factory=factory,
    )

    assert created == 3
    assert report.cases[0].execution.execution_status == "failed"
    assert report.cases[1].execution.execution_status == "succeeded"
    assert report.cases[2].execution.execution_status == "succeeded"
    assert report.summary.failed_cases == 1
    assert report.summary.skipped_cases == 0


def test_first_cancelled_run_consumes_case_budget_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = load_frozen_dataset()
    blocker = _BlockingAdapter()
    created = 0
    original_complete = model_pipeline._complete_case
    first = True

    def factory(_mode: str):
        nonlocal created
        created += 1
        return blocker if first else OfflineFakeIssueAdapter()

    def complete(application, case, checkpoint, method, output_dir, **kwargs):
        nonlocal first
        if first:
            first = False
            application.state.workspace_service.cancel_run(
                checkpoint.project_id,
                checkpoint.session_id,
                checkpoint.run_id,
                CancelRunRequest(reason="evaluation cancellation regression"),
            )
            blocker.release.set()
        return original_complete(application, case, checkpoint, method, output_dir, **kwargs)

    monkeypatch.setattr(model_pipeline, "_complete_case", complete)
    report, _ = run_model_evaluation(
        frozen,
        split="holdout",
        runtime_dir=tmp_path / "runtime",
        output_dir=tmp_path / "results",
        adapter_factory=factory,
    )

    assert created >= 2
    assert report.cases[0].execution.execution_status == "cancelled"
    assert report.cases[1].execution.execution_status == "succeeded"
    assert report.evaluation_usage.cases == 3
    assert report.evaluation_reserved.cases == 0


def test_first_interrupted_run_uses_conservative_reserved_usage_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = load_frozen_dataset()
    blocker = _BlockingAdapter()
    created = 0
    original_complete = model_pipeline._complete_case
    first = True

    def factory(_mode: str):
        nonlocal created
        created += 1
        return blocker if first else OfflineFakeIssueAdapter()

    def complete(application, case, checkpoint, method, output_dir, **kwargs):
        nonlocal first
        if first:
            first = False
            service = application.state.workspace_service
            project = service._find_project(checkpoint.project_id)
            with service.registry.store_for_repository(project.repository_id) as store:
                store.recover_incomplete_runs(datetime.now(timezone.utc))
            blocker.release.set()
        return original_complete(application, case, checkpoint, method, output_dir, **kwargs)

    monkeypatch.setattr(model_pipeline, "_complete_case", complete)
    report, _ = run_model_evaluation(
        frozen,
        split="holdout",
        runtime_dir=tmp_path / "runtime",
        output_dir=tmp_path / "results",
        adapter_factory=factory,
    )

    assert created >= 2
    assert report.cases[0].execution.execution_status == "interrupted"
    assert report.cases[0].execution.usage.model_calls == 4
    assert report.cases[1].execution.execution_status == "succeeded"
    assert report.evaluation_usage.model_calls >= 4
    assert report.evaluation_reserved.cases == 0


def test_changed_issue_rejects_resume_without_overwriting_artifacts(tmp_path: Path) -> None:
    frozen = load_frozen_dataset()
    budget = EvaluationBudgetLimits(max_cases=0)
    output_dir = tmp_path / "results"
    run_model_evaluation(
        frozen,
        evaluation_budget=budget,
        runtime_dir=tmp_path / "runtime",
        output_dir=output_dir,
    )
    tracked = [output_dir / "dry-run-plan.json", output_dir / "dry-run-plan.md", output_dir / "raw-state.json"]
    before = {path: path.read_bytes() for path in tracked}
    changed_case = replace(
        frozen.cases[0],
        issue=frozen.cases[0].issue.model_copy(update={"body": frozen.cases[0].issue.body + " changed"}),
    )
    changed = FrozenDataset(dataset=frozen.dataset, cases=(changed_case, *frozen.cases[1:]))

    with pytest.raises(ValueError, match="fingerprint|identity"):
        run_model_evaluation(
            changed,
            evaluation_budget=budget,
            runtime_dir=tmp_path / "runtime",
            output_dir=output_dir,
        )

    assert {path: path.read_bytes() for path in tracked} == before


def test_state_item_identity_mismatch_rejects_resume_without_writing(tmp_path: Path) -> None:
    frozen = load_frozen_dataset()
    budget = EvaluationBudgetLimits(max_cases=0)
    output_dir = tmp_path / "results"
    run_model_evaluation(
        frozen,
        evaluation_budget=budget,
        runtime_dir=tmp_path / "runtime",
        output_dir=output_dir,
    )
    state_path = output_dir / "raw-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["results"][frozen.cases[0].entry.case_id]["issue_hash"] = "0" * 64
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = state_path.read_bytes()
    plan_before = (output_dir / "dry-run-plan.json").read_bytes()

    with pytest.raises(ValueError, match="result identity"):
        run_model_evaluation(
            frozen,
            evaluation_budget=budget,
            runtime_dir=tmp_path / "runtime",
            output_dir=output_dir,
        )

    assert state_path.read_bytes() == before
    assert (output_dir / "dry-run-plan.json").read_bytes() == plan_before


def test_legacy_state_is_rejected_explicitly(tmp_path: Path) -> None:
    frozen = load_frozen_dataset()
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    state_path = output_dir / "raw-state.json"
    state_path.write_text(json.dumps({"schema_version": "p4-e2a-state"}) + "\n", encoding="utf-8")
    before = state_path.read_bytes()

    with pytest.raises(ValueError, match="legacy|unsupported"):
        run_model_evaluation(
            frozen,
            evaluation_budget=EvaluationBudgetLimits(max_cases=0),
            runtime_dir=tmp_path / "runtime",
            output_dir=output_dir,
        )

    assert state_path.read_bytes() == before


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
