"""Versioned local contracts for the independent P4-E1 evaluation pipeline."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveInt, model_validator


EVAL_SCHEMA_VERSION = "p4-e1"
BASELINE_VERSION = "keyword-fts5-v1"


class EvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetEntry(EvalModel):
    case_id: str = Field(min_length=1, max_length=200)
    split: Literal["calibration", "holdout"]
    source: str = Field(min_length=1, max_length=500)
    issue: str = Field(min_length=1, max_length=500)
    manifest: str = Field(min_length=1, max_length=500)
    annotation: str = Field(min_length=1, max_length=500)


class Dataset(EvalModel):
    schema_version: Literal["p4-e1"]
    dataset_id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2_000)
    cases: list[DatasetEntry] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_case_ids(self) -> Dataset:
        case_ids = [item.case_id for item in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("evaluation case IDs must be unique")
        return self


class ManifestFile(EvalModel):
    path: str = Field(min_length=1, max_length=500)
    size: NonNegativeInt
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceManifest(EvalModel):
    schema_version: Literal["p4-e1"]
    case_id: str = Field(min_length=1, max_length=200)
    files: list[ManifestFile] = Field(min_length=1, max_length=200)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_paths(self) -> SourceManifest:
        paths = [item.path for item in self.files]
        if paths != sorted(paths) or len(set(paths)) != len(paths):
            raise ValueError("manifest files must be unique and sorted")
        if any(not path.endswith(".py") or path.startswith("/") or ".." in path.split("/") for path in paths):
            raise ValueError("manifest paths must be normalized Python source paths")
        return self


class EvalLocation(EvalModel):
    path: str = Field(min_length=1, max_length=500)
    start_line: PositiveInt
    end_line: PositiveInt

    @model_validator(mode="after")
    def validate_line_range(self) -> EvalLocation:
        if self.end_line < self.start_line:
            raise ValueError("evaluation location end_line must not precede start_line")
        return self


class Annotation(EvalModel):
    schema_version: Literal["p4-e1"]
    case_id: str = Field(min_length=1, max_length=200)
    applicable: bool
    defect_category: str = Field(min_length=1, max_length=100)
    expected_files: list[str] = Field(max_length=50)
    expected_symbols: list[str] = Field(max_length=50)
    expected_locations: list[EvalLocation] = Field(max_length=50)
    basis: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_applicability(self) -> Annotation:
        if self.applicable and (not self.expected_files or not self.expected_symbols or not self.expected_locations):
            raise ValueError("applicable evaluations require file, symbol, and location labels")
        if not self.applicable and (self.expected_files or self.expected_symbols or self.expected_locations):
            raise ValueError("non-applicable evaluations cannot carry localization labels")
        return self


class BaselineConfig(EvalModel):
    tokenizer_version: str = "unicode-word-v1"
    max_keywords: PositiveInt = Field(default=8, le=20)
    per_term_limit: PositiveInt = Field(default=8, le=40)
    max_candidates: PositiveInt = Field(default=3, le=3)
    source_max_lines: PositiveInt = Field(default=200, le=200)
    search_only_index: bool = True
    model_calls: NonNegativeInt = 0
    network_calls: NonNegativeInt = 0


class EvidenceCheck(EvalModel):
    legal: bool
    evidence_id: str | None = None
    index_id: str | None = None
    path: str | None = None
    start_line: PositiveInt | None = None
    end_line: PositiveInt | None = None
    content_hash: str | None = None
    reason: str | None = None


class BaselineCandidate(EvalModel):
    rank: PositiveInt = Field(le=3)
    symbol_id: str = Field(min_length=1, max_length=200)
    index_id: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=500)
    qualified_name: str = Field(min_length=1, max_length=500)
    kind: Literal["module", "file", "class", "function", "test"]
    start_line: PositiveInt
    end_line: PositiveInt
    matched_terms: list[str] = Field(min_length=1, max_length=20)
    evidence: EvidenceCheck


class BaselineUsage(EvalModel):
    search_calls: NonNegativeInt = 0
    keyword_count: NonNegativeInt = 0
    returned_matches: NonNegativeInt = 0
    evidence_checks: NonNegativeInt = 0
    evidence_legal: NonNegativeInt = 0


class CaseResult(EvalModel):
    case_id: str = Field(min_length=1, max_length=200)
    split: Literal["calibration", "holdout"]
    applicable: bool
    defect_category: str = Field(min_length=1, max_length=100)
    status: Literal["succeeded", "failed"]
    failure_stage: str | None = None
    failure_reason: str | None = None
    issue_id: str = Field(min_length=1, max_length=200)
    issue_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_id: str = Field(min_length=1, max_length=200)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    query_terms: list[str] = Field(max_length=20)
    candidates: list[BaselineCandidate] = Field(max_length=3)
    usage: BaselineUsage
    elapsed_ms: NonNegativeInt
    expected_files: list[str] = Field(max_length=50)
    expected_symbols: list[str] = Field(max_length=50)
    expected_locations: list[EvalLocation] = Field(max_length=50)
    file_hit_at_1: bool | None = None
    file_hit_at_3: bool | None = None
    symbol_hit_at_1: bool | None = None
    symbol_hit_at_3: bool | None = None
    location_hit_at_1: bool | None = None
    location_hit_at_3: bool | None = None
    abstained: bool | None = None
    false_positive: bool | None = None
    abstention_kind: Literal["baseline_no_candidate", "model_insufficient_evidence"] | None = None


class Metric(EvalModel):
    numerator: NonNegativeInt
    denominator: NonNegativeInt
    value: float | None = None

    @model_validator(mode="after")
    def validate_value(self) -> Metric:
        if self.denominator == 0 and self.value is not None:
            raise ValueError("zero-denominator metrics must have a null value")
        if self.denominator > 0 and self.value is None:
            raise ValueError("non-empty metrics require a value")
        return self


class EvaluationSummary(EvalModel):
    total_cases: NonNegativeInt
    succeeded_cases: NonNegativeInt
    failed_cases: NonNegativeInt
    applicable_cases: NonNegativeInt
    insufficient_cases: NonNegativeInt
    file_hit_at_1: Metric
    file_hit_at_3: Metric
    symbol_hit_at_1: Metric
    symbol_hit_at_3: Metric
    evidence_legal_rate: Metric
    insufficient_abstention_rate: Metric
    insufficient_false_positive_rate: Metric


class EvaluationReport(EvalModel):
    schema_version: Literal["p4-e1"]
    dataset_id: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=1, max_length=100)
    baseline_version: str = Field(min_length=1, max_length=100)
    split: Literal["all", "calibration", "holdout"]
    run_id: str = Field(min_length=1, max_length=200)
    code_revision: str = Field(min_length=1, max_length=200)
    repository_dirty: bool
    generated_at: str = Field(min_length=1, max_length=100)
    elapsed_ms: NonNegativeInt
    config: BaselineConfig
    summary: EvaluationSummary
    cases: list[CaseResult] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_summary_counts(self) -> EvaluationReport:
        if self.summary.total_cases != len(self.cases):
            raise ValueError("summary total_cases must match case results")
        return self
