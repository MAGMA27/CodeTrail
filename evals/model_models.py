"""P4-E2a contracts for bounded real-Run evaluation and comparison reports.

These contracts deliberately keep execution metadata separate from the P4-E1
gold labels.  The raw state is safe to persist after each terminal Run and can
be resumed without re-running a completed case.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from .models import CaseResult, EvalModel, EvaluationSummary, Metric


MODEL_EVALUATION_SCHEMA_VERSION = "p4-e2a"
MODEL_METHOD_VERSION = "real-run-offline-fake-v1"
MODEL_EXECUTION_VERSION = "p4-e2a-real-run-v1"
MODEL_STATE_SCHEMA_VERSION = "p4-e2a-state-v2"
MODEL_INPUT_CONSTRUCTION_VERSION = "p4-e2a-input-v2"


class ModelBudgetLimits(EvalModel):
    """The request-side Run limits, without mutable usage counters."""

    max_tool_calls: PositiveInt = Field(default=8, le=12)
    max_model_calls: PositiveInt = Field(default=4, le=6)
    max_repair_calls: NonNegativeInt = Field(default=1, le=2)
    max_seconds: PositiveInt = Field(default=30, le=120)
    max_source_lines: PositiveInt = Field(default=200, le=200)
    max_total_source_lines: PositiveInt = Field(default=800, le=2_000)
    max_input_chars: PositiveInt = Field(default=60_000, le=100_000)
    max_model_input_chars: PositiveInt = Field(default=80_000, le=100_000)
    max_output_chars: PositiveInt = Field(default=50_000, le=100_000)
    max_model_output_chars: PositiveInt = Field(default=20_000, le=50_000)
    max_events: PositiveInt = Field(default=100, ge=2, le=200)

    @model_validator(mode="after")
    def validate_limits(self) -> ModelBudgetLimits:
        if self.max_total_source_lines < self.max_source_lines:
            raise ValueError("total source line budget must cover one source read")
        if self.max_model_output_chars > self.max_output_chars:
            raise ValueError("model output budget cannot exceed total output budget")
        return self


class EvaluationBudgetLimits(EvalModel):
    """Hard limits shared by the complete multi-case evaluation."""

    max_cases: NonNegativeInt = Field(default=100, le=100)
    max_model_calls: NonNegativeInt = Field(default=24, le=600)
    max_input_chars: NonNegativeInt = Field(default=600_000, le=10_000_000)
    max_model_input_chars: NonNegativeInt = Field(default=800_000, le=10_000_000)
    max_output_chars: NonNegativeInt = Field(default=300_000, le=10_000_000)
    max_model_output_chars: NonNegativeInt = Field(default=120_000, le=10_000_000)
    max_seconds: NonNegativeInt = Field(default=180, le=86_400)
    token_usage_policy: Literal["unknown_reported_not_counted"] = "unknown_reported_not_counted"

    @model_validator(mode="after")
    def validate_limits(self) -> EvaluationBudgetLimits:
        if self.max_model_output_chars > self.max_output_chars:
            raise ValueError("evaluation model output budget cannot exceed total output budget")
        return self


class ModelMethod(EvalModel):
    """Identifies the evaluator, model protocol, and bounded request."""

    method_id: str = Field(min_length=1, max_length=200)
    method_version: str = Field(min_length=1, max_length=100)
    execution_mode: Literal["offline_fake", "external"]
    provider_kind: Literal["offline_fake", "external"]
    model_provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=200)
    execution_version: str = Field(min_length=1, max_length=200)
    budget: ModelBudgetLimits
    network_calls: NonNegativeInt = 0
    model_calls_expected: NonNegativeInt = 0
    source_scope: Literal["case_manifest_only"] = "case_manifest_only"

    @model_validator(mode="after")
    def validate_provider_mode(self) -> ModelMethod:
        if self.execution_mode == "offline_fake" and self.provider_kind != "offline_fake":
            raise ValueError("offline model execution must use the offline_fake provider kind")
        if self.execution_mode == "external" and self.provider_kind != "external":
            raise ValueError("external model execution must use the external provider kind")
        return self


class EvaluationCaseIdentity(EvalModel):
    """Frozen per-case identity used by plans, checkpoints, and raw results."""

    case_id: str = Field(min_length=1, max_length=200)
    split: Literal["calibration", "holdout"]
    issue_id: str = Field(min_length=1, max_length=200)
    issue_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_id: str = Field(min_length=1, max_length=200)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_construction_version: str = Field(min_length=1, max_length=100)
    method_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationIdentity(EvalModel):
    """Complete execution identity; changing any field requires new state."""

    schema_version: Literal["p4-e2a-identity-v2"] = "p4-e2a-identity-v2"
    dataset_id: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=1, max_length=100)
    split: Literal["all", "calibration", "holdout"]
    code_revision: str = Field(min_length=1, max_length=200)
    input_construction_version: str = Field(min_length=1, max_length=100)
    method_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    method: ModelMethod
    evaluation_budget: EvaluationBudgetLimits
    cases: list[EvaluationCaseIdentity] = Field(min_length=1, max_length=100)


class DryRunCase(EvalModel):
    """An auditable case input plan containing no annotation or baseline data."""

    case_id: str = Field(min_length=1, max_length=200)
    split: Literal["calibration", "holdout"]
    issue_id: str = Field(min_length=1, max_length=200)
    issue_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    issue_input: dict[str, object]
    index_id: str = Field(min_length=1, max_length=200)
    input_construction_version: str = Field(min_length=1, max_length=100)
    method_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_files: list[str] = Field(min_length=1, max_length=200)
    source_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_input_fields: list[str] = Field(min_length=1, max_length=20)
    result_path: str = Field(min_length=1, max_length=500)


class DryRunPlan(EvalModel):
    """The checklist written before any model or Run is executed."""

    schema_version: Literal["p4-e2a"] = MODEL_EVALUATION_SCHEMA_VERSION
    plan_kind: Literal["dry_run"] = "dry_run"
    dataset_id: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=1, max_length=100)
    split: Literal["all", "calibration", "holdout"]
    method: ModelMethod
    evaluation_budget: EvaluationBudgetLimits
    input_construction_version: str = Field(min_length=1, max_length=100)
    output_dir: str = Field(min_length=1, max_length=1_000)
    raw_state_path: str = Field(min_length=1, max_length=1_000)
    simulation_notice: str = Field(min_length=1, max_length=2_000)
    cases: list[DryRunCase] = Field(min_length=1, max_length=100)


class ModelUsage(EvalModel):
    """Persisted Run counters; unknown token usage stays null rather than zero."""

    model_calls: NonNegativeInt = 0
    repair_calls: NonNegativeInt = 0
    tool_calls: NonNegativeInt = 0
    source_lines: NonNegativeInt = 0
    input_chars: NonNegativeInt = 0
    model_input_chars: NonNegativeInt = 0
    output_chars: NonNegativeInt = 0
    model_output_chars: NonNegativeInt = 0
    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None


class EvaluationUsage(EvalModel):
    """Cumulative or reserved usage; missing provider tokens remain unknown."""

    cases: NonNegativeInt = 0
    model_calls: NonNegativeInt = 0
    repair_calls: NonNegativeInt = 0
    tool_calls: NonNegativeInt = 0
    source_lines: NonNegativeInt = 0
    input_chars: NonNegativeInt = 0
    model_input_chars: NonNegativeInt = 0
    output_chars: NonNegativeInt = 0
    model_output_chars: NonNegativeInt = 0
    events: NonNegativeInt = 0
    elapsed_ms: NonNegativeInt = 0
    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None


class EvaluationBudgetState(EvalModel):
    """Durable whole-evaluation reservations and settled consumption."""

    limits: EvaluationBudgetLimits
    reserved: EvaluationUsage
    consumed: EvaluationUsage
    reservations: dict[str, EvaluationUsage]


class ModelCandidate(EvalModel):
    """One final-report candidate converted to the common scoring shape."""

    rank: PositiveInt = Field(le=3)
    symbol_id: str = Field(min_length=1, max_length=200)
    index_id: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=500)
    qualified_name: str = Field(min_length=1, max_length=500)
    kind: Literal["module", "file", "class", "function", "test"]
    start_line: PositiveInt
    end_line: PositiveInt
    origin: Literal["candidate_link", "finding"]
    relation: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    evidence_legal: bool
    evidence_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence_reason: str | None = Field(default=None, max_length=1_000)


ModelExecutionStatus = Literal[
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
    "budget_skipped",
]


class RawModelCaseResult(EvalModel):
    """Gold-free terminal result saved as soon as one case finishes."""

    case_id: str = Field(min_length=1, max_length=200)
    split: Literal["calibration", "holdout"]
    issue_id: str = Field(min_length=1, max_length=200)
    issue_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_index_id: str | None = Field(default=None, min_length=1, max_length=200)
    source_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_construction_version: str | None = Field(default=None, min_length=1, max_length=100)
    method_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    execution_status: ModelExecutionStatus
    run_status: Literal["succeeded", "failed", "cancelled", "interrupted"] | None = None
    run_outcome: Literal["candidate_found", "insufficient_evidence", "failed", "cancelled", "interrupted"] | None = None
    failure_stage: str | None = Field(default=None, max_length=100)
    failure_reason: str | None = Field(default=None, max_length=2_000)
    project_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    report_id: str | None = None
    report_path: str | None = Field(default=None, max_length=1_000)
    input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    index_id: str | None = None
    snapshot_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_provider: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=200)
    execution_version: str | None = Field(default=None, max_length=200)
    candidates: list[ModelCandidate] = Field(default_factory=list, max_length=3)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    elapsed_ms: NonNegativeInt = 0


class ModelCaseResult(EvalModel):
    """Run metadata plus the post-run, gold-scored common result."""

    execution: RawModelCaseResult
    score: CaseResult


class ModelEvaluationReport(EvalModel):
    """Offline model-evaluation report; explicitly not a semantic quality claim."""

    schema_version: Literal["p4-e2a"] = MODEL_EVALUATION_SCHEMA_VERSION
    dataset_id: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=1, max_length=100)
    split: Literal["all", "calibration", "holdout"]
    run_id: str = Field(min_length=1, max_length=200)
    code_revision: str = Field(min_length=1, max_length=200)
    repository_dirty: bool
    generated_at: str = Field(min_length=1, max_length=100)
    elapsed_ms: NonNegativeInt
    method: ModelMethod
    evaluation_budget: EvaluationBudgetLimits = Field(default_factory=EvaluationBudgetLimits)
    evaluation_usage: EvaluationUsage = Field(default_factory=EvaluationUsage)
    evaluation_reserved: EvaluationUsage = Field(default_factory=EvaluationUsage)
    baseline_report_path: str = Field(min_length=1, max_length=1_000)
    raw_state_path: str = Field(min_length=1, max_length=1_000)
    simulation_notice: str = Field(min_length=1, max_length=2_000)
    summary: EvaluationSummary
    cases: list[ModelCaseResult] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_summary_counts(self) -> ModelEvaluationReport:
        if self.summary.total_cases != len(self.cases):
            raise ValueError("model summary total_cases must match case results")
        status_counts = {
            "succeeded": sum(item.score.status == "succeeded" for item in self.cases),
            "failed": sum(item.score.status == "failed" for item in self.cases),
            "skipped": sum(item.score.status == "skipped" for item in self.cases),
        }
        if (
            self.summary.succeeded_cases != status_counts["succeeded"]
            or self.summary.failed_cases != status_counts["failed"]
            or self.summary.skipped_cases != status_counts["skipped"]
        ):
            raise ValueError("model summary status counts must match case results")
        return self


class ComparisonMetrics(EvalModel):
    """Comparable metrics for one method and one case."""

    status: Literal["succeeded", "failed", "skipped"]
    file_hit_at_1: bool | None = None
    file_hit_at_3: bool | None = None
    symbol_hit_at_1: bool | None = None
    symbol_hit_at_3: bool | None = None
    location_hit_at_1: bool | None = None
    location_hit_at_3: bool | None = None
    abstained: bool | None = None
    false_positive: bool | None = None


class ComparisonCase(EvalModel):
    case_id: str = Field(min_length=1, max_length=200)
    split: Literal["calibration", "holdout"]
    baseline: ComparisonMetrics
    model: ComparisonMetrics
    model_execution_status: ModelExecutionStatus
    note: str | None = Field(default=None, max_length=1_000)


class ComparisonGroup(EvalModel):
    split: Literal["all", "calibration", "holdout"]
    baseline: EvaluationSummary
    model: EvaluationSummary


class ComparisonReport(EvalModel):
    """Per-case and split comparison with a mandatory simulation warning."""

    schema_version: Literal["p4-e2a"] = MODEL_EVALUATION_SCHEMA_VERSION
    dataset_id: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=1, max_length=100)
    method_id: str = Field(min_length=1, max_length=200)
    baseline_report_path: str = Field(min_length=1, max_length=1_000)
    model_report_path: str = Field(min_length=1, max_length=1_000)
    simulation_notice: str = Field(min_length=1, max_length=2_000)
    groups: list[ComparisonGroup] = Field(min_length=1, max_length=3)
    cases: list[ComparisonCase] = Field(min_length=1, max_length=100)


class RunCheckpoint(EvalModel):
    """Enough durable identity to resume a queued/running or terminal Run."""

    case_id: str = Field(min_length=1, max_length=200)
    split: Literal["calibration", "holdout"]
    issue_id: str = Field(min_length=1, max_length=200)
    issue_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_index_id: str = Field(min_length=1, max_length=200)
    source_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_construction_version: str = Field(min_length=1, max_length=100)
    method_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_root: str = Field(min_length=1, max_length=1_000)
    data_root: str = Field(min_length=1, max_length=1_000)
    project_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    index_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RawEvaluationState(EvalModel):
    """Gold-free resumable state; terminal results are never silently rerun."""

    schema_version: Literal["p4-e2a-state-v2"] = MODEL_STATE_SCHEMA_VERSION
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity: EvaluationIdentity
    budget: EvaluationBudgetState
    checkpoints: dict[str, RunCheckpoint] = Field(default_factory=dict)
    results: dict[str, RawModelCaseResult] = Field(default_factory=dict)


def comparison_metrics(result: CaseResult) -> ComparisonMetrics:
    """Project the shared scorer output into a compact comparison row."""

    return ComparisonMetrics(
        status=result.status,
        file_hit_at_1=result.file_hit_at_1,
        file_hit_at_3=result.file_hit_at_3,
        symbol_hit_at_1=result.symbol_hit_at_1,
        symbol_hit_at_3=result.symbol_hit_at_3,
        location_hit_at_1=result.location_hit_at_1,
        location_hit_at_3=result.location_hit_at_3,
        abstained=result.abstained,
        false_positive=result.false_positive,
    )
