from __future__ import annotations

from pathlib import Path

import pytest

from evals.models import Annotation, BaselineCandidate, BaselineConfig, BaselineUsage, CaseResult, EvalLocation, EvidenceCheck
from evals.pipeline import ROOT, _evidence_check, _validate_source_manifest, load_validated_dataset, run_evaluation, score_case, summarize
from app.contracts import Issue


def _base_result(candidates: list[BaselineCandidate], *, status: str = "succeeded") -> CaseResult:
    return CaseResult(
        case_id="case",
        split="calibration",
        applicable=False,
        defect_category="unscored",
        status=status,  # type: ignore[arg-type]
        issue_id="issue-case",
        issue_hash="0" * 64,
        index_id="index-case",
        snapshot_hash="1" * 64,
        query_terms=["term"],
        candidates=candidates,
        usage=BaselineUsage(evidence_checks=len(candidates), evidence_legal=len(candidates)),
        elapsed_ms=1,
        expected_files=[],
        expected_symbols=[],
        expected_locations=[],
    )


def _candidate(rank: int, path: str, name: str, start: int, end: int) -> BaselineCandidate:
    return BaselineCandidate(
        rank=rank,
        symbol_id=f"symbol-{rank}",
        index_id="index-case",
        path=path,
        qualified_name=name,
        kind="function",
        start_line=start,
        end_line=end,
        matched_terms=["term"],
        evidence=EvidenceCheck(legal=True, index_id="index-case", path=path, start_line=start, end_line=end),
    )


def test_frozen_dataset_has_expected_split_and_is_valid() -> None:
    validated = load_validated_dataset()

    assert len(validated.cases) == 6
    assert [case.entry.case_id for case in validated.cases] == [
        "csv-import-validation",
        "cache-key-tenant",
        "partner-billing-outage",
        "redirect-query-encoding",
        "retry-control-flow",
        "remote-rollout-config",
    ]
    assert sum(annotation.applicable for annotation in validated.annotations.values()) == 4
    for case in validated.cases:
        source_root = case.source_root.resolve()
        for metadata in (
            (ROOT / case.entry.issue).resolve(),
            (ROOT / case.entry.manifest).resolve(),
            (ROOT / case.entry.annotation).resolve(),
        ):
            assert not str(metadata).startswith(str(source_root))


def test_keyword_input_excludes_mentioned_paths_and_is_deterministic() -> None:
    from evals.pipeline import extract_keywords

    issue = Issue(
        id="issue-keywords",
        title="Cache key bug",
        body="Tenant profile cache key must include tenant_id.",
        mentioned_paths=["service.py"],
    )
    config = BaselineConfig(max_keywords=20)

    assert extract_keywords(issue, config) == extract_keywords(issue, config)
    assert "service" not in extract_keywords(issue, config)
    assert "tenant_id" in extract_keywords(issue, config)


def test_manifest_mutation_is_rejected() -> None:
    validated = load_validated_dataset()
    case = validated.cases[0]
    bad_file = case.manifest.files[0].model_copy(update={"sha256": "0" * 64})
    bad_manifest = case.manifest.model_copy(update={"files": [bad_file, *case.manifest.files[1:]]})

    with pytest.raises(ValueError, match="manifest mismatch"):
        _validate_source_manifest(case.source_root, bad_manifest)


def test_evidence_check_rejects_cross_snapshot_candidate() -> None:
    from types import SimpleNamespace

    case = load_validated_dataset().cases[0]
    symbol = SimpleNamespace(
        index_id="another-index",
        path="reader.py",
        start_line=1,
        end_line=1,
    )

    evidence = _evidence_check(object(), case, symbol, BaselineConfig())

    assert evidence.legal is False
    assert evidence.reason == "candidate index_id does not match case index"


def test_score_case_distinguishes_file_symbol_and_location_hits() -> None:
    wrong_location = _candidate(1, "reader.py", "reader.normalize_row", 1, 1)
    correct = _candidate(2, "reader.py", "reader.normalize_row", 6, 6)
    result = _base_result([wrong_location, correct])
    annotation = Annotation(
        schema_version="p4-e1",
        case_id="case",
        applicable=True,
        defect_category="input_validation",
        expected_files=["reader.py"],
        expected_symbols=["reader.normalize_row"],
        expected_locations=[EvalLocation(path="reader.py", start_line=6, end_line=6)],
        basis="The missing-name access is the frozen defect location.",
    )

    scored = score_case(result, annotation)

    assert scored.file_hit_at_1 is True
    assert scored.file_hit_at_3 is True
    assert scored.symbol_hit_at_1 is True
    assert scored.symbol_hit_at_3 is True
    assert scored.location_hit_at_1 is False
    assert scored.location_hit_at_3 is True


def test_score_case_handles_no_candidate_failure_and_false_positive() -> None:
    insufficient = Annotation(
        schema_version="p4-e1",
        case_id="case",
        applicable=False,
        defect_category="external_evidence_missing",
        expected_files=[],
        expected_symbols=[],
        expected_locations=[],
        basis="The required external response is not in the local snapshot.",
    )
    no_candidate = score_case(_base_result([]), insufficient)
    false_positive = score_case(
        _base_result([_candidate(1, "billing.py", "billing.client", 1, 2)]),
        insufficient,
    )
    failed = score_case(_base_result([], status="failed"), insufficient)

    assert no_candidate.abstained is True
    assert no_candidate.abstention_kind == "baseline_no_candidate"
    assert no_candidate.false_positive is False
    assert false_positive.abstained is False
    assert false_positive.false_positive is True
    assert failed.status == "failed"
    assert failed.abstained is False
    assert failed.false_positive is False


def test_summary_keeps_denominators_and_counts_failures() -> None:
    applicable = Annotation(
        schema_version="p4-e1",
        case_id="case",
        applicable=True,
        defect_category="input_validation",
        expected_files=["reader.py"],
        expected_symbols=["reader.normalize_row"],
        expected_locations=[EvalLocation(path="reader.py", start_line=6, end_line=6)],
        basis="known label",
    )
    scored = score_case(_base_result([], status="failed"), applicable)
    summary = summarize([scored])

    assert summary.total_cases == 1
    assert summary.failed_cases == 1
    assert summary.file_hit_at_1.numerator == 0
    assert summary.file_hit_at_1.denominator == 1
    assert summary.file_hit_at_1.value == 0.0


def test_baseline_is_repeatable_for_frozen_input(tmp_path: Path) -> None:
    validated = load_validated_dataset()
    first = run_evaluation(
        validated,
        runtime_dir=tmp_path / "runtime",
        output_dir=tmp_path / "first",
    )
    second = run_evaluation(
        validated,
        runtime_dir=tmp_path / "runtime",
        output_dir=tmp_path / "second",
    )

    assert first.summary == second.summary
    assert [case.model_dump(mode="json", exclude={"elapsed_ms"}) for case in first.cases] == [
        case.model_dump(mode="json", exclude={"elapsed_ms"}) for case in second.cases
    ]
    for case in first.cases:
        assert len({candidate.symbol_id for candidate in case.candidates}) == len(case.candidates)
        assert [candidate.rank for candidate in case.candidates] == list(range(1, len(case.candidates) + 1))
    assert (tmp_path / "first" / "baseline-all.json").is_file()
    assert (tmp_path / "second" / "baseline-all.md").is_file()


def test_baseline_respects_keyword_and_result_budgets(tmp_path: Path) -> None:
    validated = load_validated_dataset()
    report = run_evaluation(
        validated,
        config=BaselineConfig(max_keywords=1, per_term_limit=1),
        runtime_dir=tmp_path / "runtime",
        output_dir=tmp_path / "output",
    )

    assert all(case.usage.keyword_count <= 1 for case in report.cases)
    assert all(case.usage.search_calls <= 1 for case in report.cases)
    assert all(case.usage.returned_matches <= 1 for case in report.cases)
    assert all(len(case.candidates) <= 3 for case in report.cases)
