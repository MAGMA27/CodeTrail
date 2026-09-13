"""P4-E2a/P4-E2b model-evaluation orchestration through the real Run service.

The default provider is a deterministic local adapter.  It exercises the same
IssueModelAdapter, tool, budget, persistence, and report path as a configured
provider, but never opens a network connection and never sees annotations.
The explicit external provider reuses the compatible HTTP adapter and is
blocked unless a caller supplies persistent configuration, an execution gate,
and a positive cumulative cost limit.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit

from pydantic import ValidationError

# Keep the standalone evals package runnable through ``uv run --project``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.contracts import (
    AppendSessionMessageRequest,
    CancelRunRequest,
    CreateProjectIndexRequest,
    CreateProjectRequest,
    CreateRunRequest,
    CreateSessionRequest,
    Evidence,
    Report,
    RunBudget,
)
from app.config import ModelProviderConfig, load_model_config_file
from app.feature_generation import ModelCall
from app.issue_analysis import ISSUE_PROMPT_VERSION, IssueModelAdapter, OpenAICompatibleIssueAdapter
from app.main import create_app

from .model_models import (
    MODEL_EVALUATION_SCHEMA_VERSION,
    MODEL_EXECUTION_VERSION,
    MODEL_INPUT_CONSTRUCTION_VERSION,
    MODEL_METHOD_VERSION,
    MODEL_STATE_SCHEMA_VERSION,
    ComparisonCase,
    ComparisonGroup,
    ComparisonReport,
    DryRunCase,
    DryRunPlan,
    EvaluationBudgetLimits,
    EvaluationBudgetState,
    EvaluationCaseIdentity,
    EvaluationCostPolicy,
    EvaluationIdentity,
    EvaluationUsage,
    ModelBudgetLimits,
    ModelCandidate,
    ModelCaseResult,
    ModelEvaluationReport,
    ModelMethod,
    ModelUsage,
    RawEvaluationState,
    RawModelCaseResult,
    RunCheckpoint,
    comparison_metrics,
)
from .models import (
    BaselineCandidate,
    BaselineUsage,
    CaseResult,
    EvidenceCheck,
    EvaluationReport,
    Metric,
)
from .pipeline import (
    EvalCase,
    FrozenDataset,
    ROOT,
    _canonical_json,
    _git_metadata,
    _model_hash,
    load_annotations_for_dataset,
    score_case,
    select_cases,
    summarize,
)


OFFLINE_FAKE_MODEL = "codetrail-eval-fake-v1"
OFFLINE_FAKE_PROVIDER = "offline-fake"
EXTERNAL_PROVIDER = "deepseek"
EXTERNAL_MODEL = "deepseek-v4-flash"
EXTERNAL_METHOD_VERSION = "real-run-external-deepseek-v1"
EXTERNAL_RESULTS_DIR = "p4-e2b-external-v1"
DEEPSEEK_PRICING_SOURCE_URL = "https://api-docs.deepseek.com/quick_start/pricing"
DEEPSEEK_PRICING_CHECKED_AT = "2026-09-13"
DEEPSEEK_COST_POLICY = EvaluationCostPolicy(
    provider="deepseek",
    model=EXTERNAL_MODEL,
    currency="USD",
    pricing_source_url=DEEPSEEK_PRICING_SOURCE_URL,
    pricing_checked_at=DEEPSEEK_PRICING_CHECKED_AT,
    # Official peak, cache-miss pricing as checked on 2026-09-13.  Values
    # are micro-USD per million tokens to keep reservations integer-only.
    input_cache_hit_microusd_per_million=14_000,
    input_cache_miss_microusd_per_million=440_000,
    output_microusd_per_million=1_320_000,
)
SIMULATION_NOTICE = (
    "本报告使用本地确定性假供应商，目的是验证真实 Run/工具/预算/持久化/评分适配；"
    "它不代表任何真实模型的语义质量、根因判断或供应商表现。"
)
EXTERNAL_NOTICE = (
    "这是受显式授权、配置指纹、发送范围和累计费用上限约束的真实供应商评测入口；"
    "本地验证使用 HTTP 假供应商，当前仓库交付不代表已经访问外网或产生真实费用。"
)
DEFAULT_BUDGET_LIMITS = ModelBudgetLimits()
DEFAULT_EVALUATION_BUDGET = EvaluationBudgetLimits()
MODEL_RESULTS_DIR = "p4-e2a-fix-v1"
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}


class OfflineFakeIssueAdapter:
    """A deterministic protocol adapter that only follows visible Run context."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.generate_calls = 0

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        timeout_seconds: float,
        max_input_chars: int,
        max_output_chars: int,
    ) -> ModelCall:
        del model, timeout_seconds, max_input_chars, max_output_chars
        self.prompts.append(prompt)
        self.generate_calls += 1
        index_id = _prompt_index_id(prompt)
        issue = _prompt_issue(prompt)
        if _issue_requires_abstention(issue):
            return _model_call(_insufficient_output(index_id))

        symbols = _prompt_symbols(prompt)
        if self.generate_calls == 1:
            query = _query_for_issue(issue)
            return _model_call(
                {
                    "schema_version": "0.1.0",
                    "kind": "tool_call",
                    "params": {"tool": "search_code", "query": query, "limit": 8},
                }
            )
        if self.generate_calls == 2:
            if not symbols:
                return _model_call(_insufficient_output(index_id))
            target = _pick_symbol(symbols)
            return _model_call(
                {
                    "schema_version": "0.1.0",
                    "kind": "tool_call",
                    "params": {
                        "tool": "read_source",
                        "path": target[1],
                        "start_line": int(target[2]),
                        "end_line": int(target[3]),
                    },
                }
            )

        evidence = _prompt_evidence(prompt)
        if not symbols or evidence is None:
            return _model_call(_insufficient_output(index_id))
        symbol_id, path, start_line, end_line, _kind, _qualified_name = _pick_symbol(symbols)
        evidence_id, _evidence_path, _evidence_start, _evidence_end, _content_hash = evidence
        return _model_call(
            {
                "schema_version": "0.1.0",
                "kind": "final",
                "index_id": index_id,
                "outcome": "candidate_found",
                "summary": "The bounded snapshot contains a candidate supported by same-snapshot source evidence.",
                "findings": [
                    {
                        "finding_id": "finding-offline-fake",
                        "kind": "hypothesis",
                        "text": "The selected source span is a bounded candidate for the Issue.",
                        "symbol_ids": [symbol_id],
                        "evidence_ids": [evidence_id],
                    }
                ],
                "candidate_links": [
                    {
                        "candidate_id": "candidate-offline-fake",
                        "index_id": index_id,
                        "source_symbol_id": symbol_id,
                        "target_symbol_id": None,
                        "relation": "selected by the offline fake adapter",
                        "evidence_ids": [evidence_id],
                    }
                ],
                "evidence_ids": [evidence_id],
                "limitations": ["offline fake adapter; no semantic quality claim"],
            }
        )

    def repair(
        self,
        prompt: str,
        invalid_output: str,
        validation_error: str,
        *,
        model: str,
        timeout_seconds: float,
        max_input_chars: int,
        max_output_chars: int,
    ) -> ModelCall:
        del invalid_output, validation_error, model, timeout_seconds, max_input_chars, max_output_chars
        return _model_call(_insufficient_output(_prompt_index_id(prompt)))


def _model_call(output: dict[str, object]) -> ModelCall:
    return ModelCall(raw_output=json.dumps(output, ensure_ascii=False, separators=(",", ":")))


def _prompt_index_id(prompt: str) -> str:
    match = re.search(r"^index_id=(\S+)$", prompt, re.MULTILINE)
    if match is None:
        raise ValueError("fake adapter prompt is missing index_id")
    return match.group(1)


def _prompt_issue(prompt: str) -> dict[str, object]:
    match = re.search(r"Issue JSON:\n(\{.*?\})\nFrozen saved Session messages", prompt, re.DOTALL)
    if match is None:
        raise ValueError("fake adapter prompt is missing Issue JSON")
    decoded = json.loads(match.group(1))
    if not isinstance(decoded, dict):
        raise ValueError("fake adapter Issue JSON is not an object")
    return decoded


def _issue_requires_abstention(issue: Mapping[str, object]) -> bool:
    text = f"{issue.get('title', '')} {issue.get('body', '')}".casefold()
    return "not available" in text or "no rollout response" in text


def _query_for_issue(issue: Mapping[str, object]) -> str:
    text = f"{issue.get('title', '')} {issue.get('body', '')}".casefold()
    for marker, query in (
        ("tenant", "tenant_id"),
        ("without a name", "name"),
        ("next_path", "next_path"),
        ("redirect", "next_path"),
        ("retry", "attempts"),
        ("transient failure", "attempts"),
    ):
        if marker in text:
            return query
    paths = issue.get("mentioned_paths")
    if isinstance(paths, list) and paths and isinstance(paths[0], str):
        return Path(paths[0]).stem
    return "issue"


def _prompt_symbols(prompt: str) -> list[tuple[str, str, str, str, str, str]]:
    return re.findall(
        r"- symbol_id=(\S+) path=(\S+) lines=(\d+)-(\d+) kind=(\S+) qualified_name=(\S+)",
        prompt,
    )


def _pick_symbol(symbols: Sequence[tuple[str, str, str, str, str, str]]) -> tuple[str, str, str, str, str, str]:
    for symbol in symbols:
        if symbol[4] in {"function", "test"}:
            return symbol
    return symbols[0]


def _prompt_evidence(prompt: str) -> tuple[str, str, str, str, str] | None:
    match = re.search(
        r"- evidence_id=(\S+) path=(\S+) lines=(\d+)-(\d+) hash=(\S+)",
        prompt,
    )
    return match.groups() if match else None


def _insufficient_output(index_id: str) -> dict[str, object]:
    return {
        "schema_version": "0.1.0",
        "kind": "final",
        "index_id": index_id,
        "outcome": "insufficient_evidence",
        "summary": "The bounded snapshot does not contain enough local evidence for a conclusion.",
        "findings": [],
        "candidate_links": [],
        "evidence_ids": [],
        "limitations": ["offline fake adapter abstention"],
    }


def default_method(budget: ModelBudgetLimits | None = None) -> ModelMethod:
    """Return the explicit P4-E2a offline method declaration."""

    limits = budget or DEFAULT_BUDGET_LIMITS
    return ModelMethod(
        method_id=MODEL_METHOD_VERSION,
        method_version=MODEL_METHOD_VERSION,
        execution_mode="offline_fake",
        provider_kind="offline_fake",
        model_provider=OFFLINE_FAKE_PROVIDER,
        model=OFFLINE_FAKE_MODEL,
        prompt_version=ISSUE_PROMPT_VERSION,
        execution_version=MODEL_EXECUTION_VERSION,
        budget=limits,
        network_calls=0,
        model_calls_expected=limits.max_model_calls,
    )


def provider_config_fingerprint(config: ModelProviderConfig) -> str:
    """Hash provider settings without ever serializing the credential itself."""

    redacted = {
        "provider": config.provider,
        "base_url": config.base_url,
        "model": config.model,
        "api_key_sha256": hashlib.sha256(config.api_key.encode("utf-8")).hexdigest(),
    }
    return hashlib.sha256(_canonical_json(redacted).encode("utf-8")).hexdigest()


def external_method(
    config: ModelProviderConfig,
    budget: ModelBudgetLimits | None = None,
) -> ModelMethod:
    """Declare the gated DeepSeek-compatible external evaluation method."""

    if config.provider.casefold() != EXTERNAL_PROVIDER:
        raise ValueError(
            f"P4-E2b external evaluation only supports provider {EXTERNAL_PROVIDER!r}; "
            f"got {config.provider!r}"
        )
    if config.model != EXTERNAL_MODEL:
        raise ValueError(
            f"P4-E2b external evaluation requires model {EXTERNAL_MODEL!r}; got {config.model!r}"
        )
    parsed = urlsplit(config.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("P4-E2b external evaluation requires a valid HTTP(S) provider URL")
    limits = budget or DEFAULT_BUDGET_LIMITS
    return ModelMethod(
        method_id=EXTERNAL_METHOD_VERSION,
        method_version=EXTERNAL_METHOD_VERSION,
        execution_mode="external",
        provider_kind="external",
        model_provider=config.provider,
        model=config.model,
        prompt_version=ISSUE_PROMPT_VERSION,
        execution_version=MODEL_EXECUTION_VERSION,
        budget=limits,
        network_calls=limits.max_model_calls,
        model_calls_expected=limits.max_model_calls,
        provider_config_fingerprint=provider_config_fingerprint(config),
        configuration_status="ready" if config.api_key.strip() else "missing",
        cost_policy=DEEPSEEK_COST_POLICY,
    )


def _method_fingerprint(method: ModelMethod) -> str:
    return hashlib.sha256(_canonical_json(method.model_dump(mode="json")).encode("utf-8")).hexdigest()


def _case_identity(
    case: EvalCase,
    method: ModelMethod,
    *,
    input_construction_version: str = MODEL_INPUT_CONSTRUCTION_VERSION,
) -> EvaluationCaseIdentity:
    return EvaluationCaseIdentity(
        case_id=case.entry.case_id,
        split=case.entry.split,
        issue_id=case.issue.issue_id,
        issue_hash=_model_hash(case.issue),
        index_id=case.index_id,
        snapshot_hash=case.manifest.snapshot_hash,
        input_construction_version=input_construction_version,
        method_fingerprint=_method_fingerprint(method),
    )


def _evaluation_identity(
    frozen: FrozenDataset,
    cases: Sequence[EvalCase],
    method: ModelMethod,
    evaluation_budget: EvaluationBudgetLimits,
    split: str,
    revision: str,
    *,
    input_construction_version: str = MODEL_INPUT_CONSTRUCTION_VERSION,
) -> EvaluationIdentity:
    return EvaluationIdentity(
        dataset_id=frozen.dataset.dataset_id,
        dataset_version=frozen.dataset.version,
        split=split,  # type: ignore[arg-type]
        code_revision=revision,
        input_construction_version=input_construction_version,
        method_fingerprint=_method_fingerprint(method),
        method=method,
        evaluation_budget=evaluation_budget,
        cases=[
            _case_identity(
                case,
                method,
                input_construction_version=input_construction_version,
            )
            for case in cases
        ],
    )


def build_dry_run_plan(
    frozen: FrozenDataset,
    *,
    split: str = "all",
    output_dir: Path | None = None,
    method: ModelMethod | None = None,
    evaluation_budget: EvaluationBudgetLimits | None = None,
    input_construction_version: str = MODEL_INPUT_CONSTRUCTION_VERSION,
) -> DryRunPlan:
    """Build a gold-free execution checklist before any Run is created."""

    selected = select_cases(frozen, split)
    if not selected:
        raise ValueError(f"evaluation split has no cases: {split}")
    selected_method = method or default_method()
    selected_evaluation_budget = evaluation_budget or DEFAULT_EVALUATION_BUDGET
    if selected_method.execution_mode == "external" and selected_evaluation_budget.max_cost_microusd <= 0:
        raise ValueError("external evaluation requires an explicit positive max evaluation cost")
    output = output_dir or ROOT / "results" / (
        EXTERNAL_RESULTS_DIR if selected_method.execution_mode == "external" else MODEL_RESULTS_DIR
    )
    planned_method = selected_method.model_copy(
        update={
            "model_calls_expected": len(selected) * selected_method.budget.max_model_calls,
            "network_calls": (
                len(selected) * selected_method.budget.max_model_calls
                if selected_method.execution_mode == "external"
                else selected_method.network_calls
            ),
        }
    )
    method_fingerprint = _method_fingerprint(planned_method)
    return DryRunPlan(
        dataset_id=frozen.dataset.dataset_id,
        dataset_version=frozen.dataset.version,
        split=split,  # type: ignore[arg-type]
        method=planned_method,
        evaluation_budget=selected_evaluation_budget,
        input_construction_version=input_construction_version,
        output_dir=str(output),
        raw_state_path=str(output / "raw-state.json"),
        simulation_notice=EXTERNAL_NOTICE if selected_method.execution_mode == "external" else SIMULATION_NOTICE,
        cases=[
            DryRunCase(
                case_id=case.entry.case_id,
                split=case.entry.split,
                issue_id=case.issue.issue_id,
                issue_hash=_model_hash(case.issue),
                issue_input=case.issue.model_dump(mode="json", by_alias=True),
                index_id=case.index_id,
                input_construction_version=input_construction_version,
                method_fingerprint=method_fingerprint,
                source_files=[item.path for item in case.manifest.files],
                source_manifest_hash=case.manifest.snapshot_hash,
                run_input_fields=["Issue JSON", "complete saved Session message prefix"],
                result_path=f"runs/{case.entry.case_id}/report.json",
            )
            for case in selected
        ],
    )


def render_dry_run_plan(plan: DryRunPlan) -> str:
    external = plan.method.execution_mode == "external"
    title = "P4-E2b 外部供应商评测执行清单" if external else "P4-E2a 离线 Run 评测执行清单"
    notice = EXTERNAL_NOTICE if external else SIMULATION_NOTICE
    cost_line = (
        f"；费用上限={plan.evaluation_budget.max_cost_microusd / 1_000_000:.6f} USD；"
        f"价格依据={plan.method.cost_policy.pricing_source_url}（检查日 {plan.method.cost_policy.pricing_checked_at}）"
        if external and plan.method.cost_policy is not None
        else ""
    )
    lines = [
        f"# {title}",
        "",
        f"- 数据集：`{plan.dataset_id}` / `{plan.dataset_version}`；划分：`{plan.split}`",
        f"- 方法：`{plan.method.method_id}`；provider：`{plan.method.model_provider}`；模型：`{plan.method.model}`",
        f"- 配置状态：`{plan.method.configuration_status}`；配置指纹：`{plan.method.provider_config_fingerprint or 'not-required'}`",
        f"- prompt：`{plan.method.prompt_version}`；execution：`{plan.method.execution_version}`",
        f"- 输入构造：`{plan.input_construction_version}`；整次预算：cases={plan.evaluation_budget.max_cases}，"
        f"model calls={plan.evaluation_budget.max_model_calls}，input/model input chars="
        f"{plan.evaluation_budget.max_input_chars}/{plan.evaluation_budget.max_model_input_chars}，"
        f"output/model output chars={plan.evaluation_budget.max_output_chars}/"
        f"{plan.evaluation_budget.max_model_output_chars}，seconds={plan.evaluation_budget.max_seconds}"
        f"{cost_line}",
        f"- 结果目录：`{plan.output_dir}`；恢复状态：`{plan.raw_state_path}`",
        f"- 网络调用：`{plan.method.network_calls}`；最大模型调用：`{plan.method.model_calls_expected}`",
        f"- 执行说明：{notice}",
        "",
        "## 案例输入",
        "",
        "| 案例 | 划分 | Issue | 源码文件 | snapshot hash | Run 输入 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in plan.cases:
        lines.append(
            f"| `{case.case_id}` | {case.split} | `{case.issue_id}` / `{case.issue_hash[:12]}` | "
            f"{len(case.source_files)} 个 manifest 文件 | `{case.source_manifest_hash[:12]}` | "
            f"{', '.join(case.run_input_fields)} |"
        )
    lines.extend(["", "## 发送范围", "", "每个 Run 只发送对应案例的 Issue JSON、完整保存 Session 消息前缀和工具返回的同快照有限上下文；工具根目录只包含该案例源码副本。标签、修复参考、其他案例和基线候选不进入执行路径。", ""])
    return "\n".join(lines)


def write_dry_run_plan(plan: DryRunPlan, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "dry-run-plan.json"
    markdown_path = output_dir / "dry-run-plan.md"
    _write_json(json_path, plan.model_dump(mode="json"))
    markdown_path.write_text(render_dry_run_plan(plan), encoding="utf-8")
    return json_path, markdown_path


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


_USAGE_COUNTERS = (
    "cases",
    "model_calls",
    "repair_calls",
    "tool_calls",
    "source_lines",
    "input_chars",
    "model_input_chars",
    "output_chars",
    "model_output_chars",
    "events",
    "elapsed_ms",
    "estimated_cost_microusd",
)


def _merge_tokens(left: EvaluationUsage, right: EvaluationUsage, field: str) -> int | None:
    left_value = getattr(left, field)
    right_value = getattr(right, field)
    left_empty = left.cases == 0 and left.model_calls == 0
    right_empty = right.cases == 0 and right.model_calls == 0
    if left_empty:
        return right_value
    if right_empty:
        return left_value
    if left_value is None or right_value is None:
        return None
    return left_value + right_value


def _add_usage(left: EvaluationUsage, right: EvaluationUsage) -> EvaluationUsage:
    payload = {name: getattr(left, name) + getattr(right, name) for name in _USAGE_COUNTERS}
    payload["input_tokens"] = _merge_tokens(left, right, "input_tokens")
    payload["output_tokens"] = _merge_tokens(left, right, "output_tokens")
    return EvaluationUsage(**payload)


def _subtract_usage(left: EvaluationUsage, right: EvaluationUsage) -> EvaluationUsage:
    payload: dict[str, int | None] = {}
    for name in _USAGE_COUNTERS:
        remaining = getattr(left, name) - getattr(right, name)
        if remaining < 0:
            raise ValueError(f"evaluation budget reservation exceeds {name} usage")
        payload[name] = remaining
    payload["input_tokens"] = None
    payload["output_tokens"] = None
    return EvaluationUsage(**payload)


def _usage_without_tokens(usage: EvaluationUsage) -> dict[str, int]:
    return {name: getattr(usage, name) for name in _USAGE_COUNTERS}


def _usage_from_model_usage(
    usage: ModelUsage,
    *,
    elapsed_ms: int,
    cases: int = 1,
    cost_policy: EvaluationCostPolicy | None = None,
) -> EvaluationUsage:
    return EvaluationUsage(
        cases=cases,
        model_calls=usage.model_calls,
        repair_calls=usage.repair_calls,
        tool_calls=usage.tool_calls,
        source_lines=usage.source_lines,
        input_chars=usage.input_chars,
        model_input_chars=usage.model_input_chars,
        output_chars=usage.output_chars,
        model_output_chars=usage.model_output_chars,
        elapsed_ms=elapsed_ms,
        estimated_cost_microusd=(
            _cost_from_tokens(
                cost_policy,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            )
            if cost_policy is not None
            and usage.input_tokens is not None
            and usage.output_tokens is not None
            else 0
        ),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )


def _cost_from_tokens(
    policy: EvaluationCostPolicy,
    *,
    input_tokens: int,
    output_tokens: int,
) -> int:
    total = (
        input_tokens * policy.input_cache_miss_microusd_per_million
        + output_tokens * policy.output_microusd_per_million
    )
    return (total + 999_999) // 1_000_000


def _worst_case_cost(policy: EvaluationCostPolicy, budget: ModelBudgetLimits) -> int:
    return _cost_from_tokens(
        policy,
        input_tokens=budget.max_model_input_chars * policy.tokens_per_char_upper_bound,
        output_tokens=budget.max_model_output_chars * policy.tokens_per_char_upper_bound,
    )


def _worst_case_usage(method: ModelMethod) -> EvaluationUsage:
    budget = method.budget
    return EvaluationUsage(
        cases=1,
        model_calls=budget.max_model_calls,
        repair_calls=budget.max_repair_calls,
        tool_calls=budget.max_tool_calls,
        source_lines=budget.max_total_source_lines,
        input_chars=budget.max_input_chars,
        model_input_chars=budget.max_model_input_chars,
        output_chars=budget.max_output_chars,
        model_output_chars=budget.max_model_output_chars,
        events=budget.max_events,
        elapsed_ms=budget.max_seconds * 1_000,
        estimated_cost_microusd=(
            _worst_case_cost(method.cost_policy, budget)
            if method.cost_policy is not None
            else 0
        ),
    )


def _reservation_failure_reason(
    state: EvaluationBudgetState,
    reservation: EvaluationUsage,
) -> str | None:
    consumed_and_reserved = _add_usage(state.consumed, state.reserved)
    limits = state.limits
    checks = (
        ("cases", consumed_and_reserved.cases + reservation.cases, limits.max_cases),
        ("model_calls", consumed_and_reserved.model_calls + reservation.model_calls, limits.max_model_calls),
        ("input_chars", consumed_and_reserved.input_chars + reservation.input_chars, limits.max_input_chars),
        (
            "model_input_chars",
            consumed_and_reserved.model_input_chars + reservation.model_input_chars,
            limits.max_model_input_chars,
        ),
        ("output_chars", consumed_and_reserved.output_chars + reservation.output_chars, limits.max_output_chars),
        (
            "model_output_chars",
            consumed_and_reserved.model_output_chars + reservation.model_output_chars,
            limits.max_model_output_chars,
        ),
        ("elapsed_ms", consumed_and_reserved.elapsed_ms + reservation.elapsed_ms, limits.max_seconds * 1_000),
        (
            "cost_microusd",
            consumed_and_reserved.estimated_cost_microusd + reservation.estimated_cost_microusd,
            limits.max_cost_microusd,
        ),
    )
    for name, requested, maximum in checks:
        if requested > maximum:
            return f"evaluation budget exhausted: max_{name} ({requested}>{maximum})"
    return None


def _reserve_budget(state: RawEvaluationState, case_id: str, reservation: EvaluationUsage) -> None:
    if case_id in state.budget.reservations:
        raise ValueError(f"evaluation budget reservation already exists for {case_id}")
    reason = _reservation_failure_reason(state.budget, reservation)
    if reason:
        raise ValueError(reason)
    state.budget.reservations[case_id] = reservation
    state.budget.reserved = _add_usage(state.budget.reserved, reservation)


def _settle_budget(
    state: RawEvaluationState,
    case_id: str,
    actual: EvaluationUsage,
) -> None:
    reservation = state.budget.reservations.pop(case_id, None)
    if reservation is None:
        raise ValueError(f"evaluation budget reservation is missing for {case_id}")
    state.budget.reserved = _subtract_usage(state.budget.reserved, reservation)
    state.budget.consumed = _add_usage(state.budget.consumed, actual)


def _validate_state(
    state: RawEvaluationState,
    identity: EvaluationIdentity,
) -> None:
    """Reject stale, partially migrated, or cross-case state before any write."""

    if state.schema_version != MODEL_STATE_SCHEMA_VERSION:
        raise ValueError(
            "P4-E2a raw state uses an unsupported legacy schema; choose a new result directory"
        )
    if state.identity.model_dump(mode="json") != identity.model_dump(mode="json"):
        raise ValueError(
            "P4-E2a raw state execution identity differs (Issue/source/index/method/budget); "
            "choose a new result directory"
        )
    if state.budget.limits.model_dump(mode="json") != identity.evaluation_budget.model_dump(mode="json"):
        raise ValueError("P4-E2a raw state evaluation budget differs; choose a new result directory")

    cases = {case.case_id: case for case in identity.cases}
    for collection_name, collection in (
        ("checkpoint", state.checkpoints),
        ("result", state.results),
        ("reservation", state.budget.reservations),
    ):
        unknown = sorted(set(collection).difference(cases))
        if unknown:
            raise ValueError(f"P4-E2a raw state has {collection_name} outside the execution list: {unknown}")

    for case_id, checkpoint in state.checkpoints.items():
        expected = cases[case_id]
        actual = {
            "case_id": checkpoint.case_id,
            "split": checkpoint.split,
            "issue_id": checkpoint.issue_id,
            "issue_hash": checkpoint.issue_hash,
            "index_id": checkpoint.source_index_id,
            "snapshot_hash": checkpoint.snapshot_hash,
            "input_construction_version": checkpoint.input_construction_version,
            "method_fingerprint": checkpoint.method_fingerprint,
        }
        wanted = expected.model_dump(mode="json")
        if actual != wanted or checkpoint.case_id != case_id:
            raise ValueError(f"P4-E2a checkpoint identity differs for {case_id}")

    for case_id, result in state.results.items():
        expected = cases[case_id]
        if result.case_id != case_id:
            raise ValueError(f"P4-E2a result key does not match case_id for {case_id}")
        if (
            result.split != expected.split
            or result.issue_id != expected.issue_id
            or result.issue_hash != expected.issue_hash
            or result.source_index_id != expected.index_id
            or result.source_manifest_hash != expected.snapshot_hash
            or result.input_construction_version != expected.input_construction_version
            or result.method_fingerprint != expected.method_fingerprint
            or (result.snapshot_hash is not None and result.snapshot_hash != expected.snapshot_hash)
        ):
            raise ValueError(f"P4-E2a result identity differs for {case_id}")
        checkpoint = state.checkpoints.get(case_id)
        if checkpoint is not None:
            if result.run_id is not None and result.run_id != checkpoint.run_id:
                raise ValueError(f"P4-E2a result Run identity differs for {case_id}")
            if result.index_id is not None and result.index_id != checkpoint.index_id:
                raise ValueError(f"P4-E2a result index identity differs for {case_id}")
            if result.input_hash is not None and result.input_hash != checkpoint.input_hash:
                raise ValueError(f"P4-E2a result input identity differs for {case_id}")
        if result.execution_status == "budget_skipped":
            if checkpoint is not None or result.run_id is not None or result.report_id is not None:
                raise ValueError(f"budget-skipped case unexpectedly has a Run for {case_id}")

    for case_id in state.checkpoints:
        if case_id not in state.results and case_id not in state.budget.reservations:
            raise ValueError(f"P4-E2a checkpoint has no result or active reservation for {case_id}")
    for case_id in state.budget.reservations:
        if case_id in state.results:
            raise ValueError(f"P4-E2a completed case still has a budget reservation: {case_id}")

    expected_reserved = EvaluationUsage()
    for reservation in state.budget.reservations.values():
        expected_reserved = _add_usage(expected_reserved, reservation)
    if _usage_without_tokens(state.budget.reserved) != _usage_without_tokens(expected_reserved):
        raise ValueError("P4-E2a raw state reserved usage does not match reservations")
    limits = state.budget.limits
    consumed_and_reserved = _add_usage(state.budget.consumed, state.budget.reserved)
    if (
        consumed_and_reserved.cases > limits.max_cases
        or consumed_and_reserved.model_calls > limits.max_model_calls
        or consumed_and_reserved.input_chars > limits.max_input_chars
        or consumed_and_reserved.model_input_chars > limits.max_model_input_chars
        or consumed_and_reserved.output_chars > limits.max_output_chars
        or consumed_and_reserved.model_output_chars > limits.max_model_output_chars
        or consumed_and_reserved.elapsed_ms > limits.max_seconds * 1_000
        or consumed_and_reserved.estimated_cost_microusd > limits.max_cost_microusd
    ):
        raise ValueError("P4-E2a raw state cumulative budget exceeds its limits")


def _state_fingerprint(
    frozen: FrozenDataset,
    cases: Sequence[EvalCase],
    method: ModelMethod,
    split: str,
    revision: str,
    evaluation_budget: EvaluationBudgetLimits | None = None,
    input_construction_version: str = MODEL_INPUT_CONSTRUCTION_VERSION,
) -> str:
    identity = _evaluation_identity(
        frozen,
        cases,
        method,
        evaluation_budget or DEFAULT_EVALUATION_BUDGET,
        split,
        revision,
        input_construction_version=input_construction_version,
    )
    return hashlib.sha256(_canonical_json(identity.model_dump(mode="json")).encode("utf-8")).hexdigest()


def _load_or_create_state(
    path: Path,
    fingerprint: str,
    *,
    resume: bool,
    identity: EvaluationIdentity,
) -> RawEvaluationState:
    if not path.exists():
        return RawEvaluationState(
            fingerprint=fingerprint,
            identity=identity,
            budget=EvaluationBudgetState(
                limits=identity.evaluation_budget,
                reserved=EvaluationUsage(),
                consumed=EvaluationUsage(),
                reservations={},
            ),
        )
    if not resume:
        raise ValueError(f"raw evaluation state already exists; use resume or choose a new output directory: {path}")
    raw: object
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != MODEL_STATE_SCHEMA_VERSION:
            raise ValueError(
                "P4-E2a raw state is legacy or unsupported; choose a new result directory"
            )
        state = RawEvaluationState.model_validate(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"invalid P4-E2a raw state: {path}") from exc
    except ValueError as exc:
        raise ValueError(f"invalid P4-E2a raw state: {path}: {exc}") from exc
    if state.fingerprint != fingerprint:
        raise ValueError(
            "P4-E2a raw state fingerprint differs (Issue/source/index/method/budget changed); "
            "choose a new result directory"
        )
    _validate_state(state, identity)
    return state


def _ensure_runtime_source(case: EvalCase, source_root: Path) -> None:
    """Create or verify a private source copy without mutating frozen inputs."""

    if not source_root.exists():
        source_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            case.source_root,
            source_root,
            ignore=shutil.ignore_patterns("__pycache__", ".codetrail"),
        )
    actual_paths = sorted(
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*.py")
        if path.is_file()
    )
    expected_paths = [item.path for item in case.manifest.files]
    if actual_paths != expected_paths:
        raise ValueError(f"isolated source copy differs from manifest for {case.entry.case_id}")
    for item in case.manifest.files:
        path = source_root / Path(*item.path.split("/"))
        raw = path.read_bytes()
        if len(raw) != item.size or hashlib.sha256(raw).hexdigest() != item.sha256:
            raise ValueError(f"isolated source copy hash differs for {case.entry.case_id}/{item.path}")


def _ensure_offline_config(data_root: Path) -> None:
    data_root.mkdir(parents=True, exist_ok=True)
    config_path = data_root / "config.json"
    if config_path.exists():
        return
    config_path.write_text(
        json.dumps(
            {
                "provider": OFFLINE_FAKE_PROVIDER,
                "api_key": "",
                "base_url": "http://offline.invalid",
                "model": OFFLINE_FAKE_MODEL,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _new_case_app(
    source_root: Path,
    data_root: Path,
    adapter_factory: Callable[[str], IssueModelAdapter] | None = None,
    *,
    model_config: ModelProviderConfig | None = None,
):
    if model_config is None:
        _ensure_offline_config(data_root)
    return create_app(
        {"eval-case": source_root},
        data_root=data_root,
        real_run_enabled=True,
        run_model_adapter_factory=adapter_factory,
        run_max_concurrent_runs=1,
        model_config=model_config,
    )


def _start_run(
    application,
    case: EvalCase,
    method: ModelMethod,
    budget: RunBudget,
    dataset_version: str,
    *,
    input_construction_version: str = MODEL_INPUT_CONSTRUCTION_VERSION,
) -> RunCheckpoint:
    service = application.state.workspace_service
    project = service.create_project(
        CreateProjectRequest(name=f"P4-E2a {case.entry.case_id}", repository_id="eval-case")
    )
    index_response = service.create_project_index(
        project.project_id,
        CreateProjectIndexRequest(
            revision=f"{dataset_version}:{case.entry.case_id}:real-run",
            dirty=False,
        ),
    )
    index = index_response.index
    if index.snapshot_hash != case.manifest.snapshot_hash:
        raise ValueError(
            f"Run index snapshot differs from frozen manifest for {case.entry.case_id}: "
            f"{index.snapshot_hash} != {case.manifest.snapshot_hash}"
        )
    session = service.create_session(
        project.project_id,
        CreateSessionRequest(topic=case.issue.title, index_id=index.index_id),
    )
    session_id = session.session.session_id
    service.append_session_message(
        project.project_id,
        session_id,
        AppendSessionMessageRequest(
            source="human",
            content=f"{case.issue.title}\n{case.issue.body}",
        ),
    )
    created = service.create_run(
        project.project_id,
        session_id,
        CreateRunRequest(
            index_id=index.index_id,
            issue=case.issue,
            mode="real",
            model=method.model,
            prompt_version=method.prompt_version,
            execution_version=method.execution_version,
            budget=budget,
            idempotency_key=f"p4-e2a-{case.entry.case_id}",
        ),
    )
    persisted = service.get_run(project.project_id, session_id, created.run_id).run
    return RunCheckpoint(
        case_id=case.entry.case_id,
        split=case.entry.split,
        issue_id=case.issue.issue_id,
        issue_hash=_model_hash(case.issue),
        source_index_id=case.index_id,
        source_manifest_hash=case.manifest.snapshot_hash,
        snapshot_hash=index.snapshot_hash,
        input_construction_version=input_construction_version,
        method_fingerprint=_method_fingerprint(method),
        source_root=str(application.state.registry.source_root_for("eval-case")),
        data_root=str(application.state.registry.data_root),
        project_id=project.project_id,
        session_id=session_id,
        index_id=index.index_id,
        run_id=persisted.run_id,
        input_hash=persisted.input_hash,
    )


def _wait_for_terminal(service, checkpoint: RunCheckpoint, max_seconds: int):
    deadline = time.monotonic() + max_seconds
    while time.monotonic() < deadline:
        status = service.get_run(checkpoint.project_id, checkpoint.session_id, checkpoint.run_id)
        if status.run.status in TERMINAL_STATUSES:
            return status, False
        time.sleep(0.01)
    try:
        service.cancel_run(
            checkpoint.project_id,
            checkpoint.session_id,
            checkpoint.run_id,
            CancelRunRequest(reason="evaluation wait exceeded the bounded recovery window"),
        )
    except Exception:
        pass
    return service.get_run(checkpoint.project_id, checkpoint.session_id, checkpoint.run_id), True


def _run_error_reason(service, checkpoint: RunCheckpoint) -> str | None:
    try:
        events = service.list_run_events(
            checkpoint.project_id,
            checkpoint.session_id,
            checkpoint.run_id,
            after_sequence=0,
            limit=100,
        )
    except Exception:
        return None
    for event in reversed(events.events):
        error = getattr(event.payload, "error", None)
        if error is not None:
            return f"{error.code}: {error.message}"
    return None


def _evidence_check(
    report: Report,
    case: EvalCase,
    evidence_ids: Sequence[str],
) -> tuple[EvidenceCheck | None, str | None]:
    if not evidence_ids:
        return None, "final candidate has no evidence IDs"
    evidence_by_id = {item.evidence_id: item for item in report.evidence}
    first: Evidence | None = None
    for evidence_id in evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            return None, f"evidence {evidence_id!r} is absent from the Run report"
        if evidence.index_id != report.index_id:
            return None, f"evidence {evidence_id!r} crosses the Run index"
        expected_hash = case.file_hashes.get(evidence.path)
        if expected_hash is None:
            return None, f"evidence {evidence_id!r} path is absent from the frozen manifest"
        if evidence.content_hash != expected_hash:
            return None, f"evidence {evidence_id!r} hash differs from the frozen manifest"
        first = first or evidence
    assert first is not None
    return (
        EvidenceCheck(
            legal=True,
            evidence_id=first.evidence_id,
            index_id=first.index_id,
            path=first.path,
            start_line=first.start_line,
            end_line=first.end_line,
            content_hash=first.content_hash,
        ),
        None,
    )


def map_report_candidates(report: Report, case: EvalCase) -> tuple[list[ModelCandidate], list[str]]:
    """Map only final links/findings to ranked candidates; ignore tool observations."""

    if report.status != "succeeded" or report.outcome != "candidate_found":
        return [], []
    symbols = {symbol.symbol_id: symbol for symbol in report.graph.nodes}
    mapped: list[ModelCandidate] = []
    errors: list[str] = []
    seen_locations: set[tuple[str, str, int, int]] = set()

    def add_candidate(
        symbol_id: str,
        origin: str,
        relation: str,
        evidence_ids: Sequence[str],
    ) -> None:
        symbol = symbols.get(symbol_id)
        if symbol is None:
            errors.append(f"{origin} references unobserved symbol {symbol_id!r}")
            return
        if symbol.index_id != report.index_id:
            errors.append(f"{origin} symbol {symbol_id!r} crosses the Run index")
            return
        evidence, reason = _evidence_check(report, case, evidence_ids)
        if evidence is None:
            errors.append(f"{origin} for {symbol_id!r}: {reason}")
            return
        location_key = (symbol.index_id, symbol.path, symbol.start_line, symbol.end_line)
        if location_key in seen_locations:
            return
        seen_locations.add(location_key)
        mapped.append(
            ModelCandidate(
                rank=len(mapped) + 1,
                symbol_id=symbol.symbol_id,
                index_id=symbol.index_id,
                path=symbol.path,
                qualified_name=symbol.qualified_name,
                kind=symbol.kind,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                origin=origin,  # type: ignore[arg-type]
                relation=relation,
                evidence_ids=list(evidence_ids),
                evidence_legal=evidence.legal,
                evidence_reason=evidence.reason,
                evidence_content_hash=evidence.content_hash,
            )
        )

    for link in report.candidate_links:
        if link.index_id != report.index_id:
            errors.append(f"candidate link {link.candidate_id!r} crosses the Run index")
            continue
        add_candidate(link.source_symbol_id, "candidate_link", link.relation, link.evidence_ids)
        if len(mapped) >= 3:
            break
    if len(mapped) < 3:
        for finding in report.findings:
            for symbol_id in finding.symbol_ids:
                add_candidate(symbol_id, "finding", finding.kind, finding.evidence_ids)
                if len(mapped) >= 3:
                    break
            if len(mapped) >= 3:
                break
    if not mapped and not errors:
        errors.append("candidate_found report did not yield a supported final candidate")
    return mapped, errors


def _model_usage(run_budget: RunBudget) -> ModelUsage:
    return ModelUsage(
        model_calls=run_budget.model_calls_used,
        repair_calls=run_budget.repair_calls_used,
        tool_calls=run_budget.tool_calls_used,
        source_lines=run_budget.source_lines_used,
        input_chars=run_budget.input_chars_used,
        model_input_chars=run_budget.model_input_chars_used,
        output_chars=run_budget.output_chars_used,
        model_output_chars=run_budget.model_output_chars_used,
        input_tokens=run_budget.model_input_tokens_used,
        output_tokens=run_budget.model_output_tokens_used,
    )


def _model_usage_from_evaluation_usage(usage: EvaluationUsage) -> ModelUsage:
    return ModelUsage(
        model_calls=usage.model_calls,
        repair_calls=usage.repair_calls,
        tool_calls=usage.tool_calls,
        source_lines=usage.source_lines,
        input_chars=usage.input_chars,
        model_input_chars=usage.model_input_chars,
        output_chars=usage.output_chars,
        model_output_chars=usage.model_output_chars,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )


def _settle_case_usage(
    raw: RawModelCaseResult,
    reservation: EvaluationUsage,
    method: ModelMethod,
    *,
    elapsed_ms: int,
) -> tuple[EvaluationUsage, RawModelCaseResult]:
    """Settle known usage, or retain the full reservation when billing is unknown."""

    if raw.execution_status == "interrupted":
        return reservation, raw.model_copy(
            update={
                "usage": _model_usage_from_evaluation_usage(reservation),
                "elapsed_ms": max(raw.elapsed_ms, reservation.elapsed_ms),
                "estimated_cost_microusd": reservation.estimated_cost_microusd,
                "cost_settlement": (
                    "worst_case_unknown"
                    if method.cost_policy is not None
                    else "not_applicable"
                ),
            }
        )

    actual = _usage_from_model_usage(
        raw.usage,
        elapsed_ms=elapsed_ms,
        cost_policy=method.cost_policy,
    )
    if method.cost_policy is None:
        return actual, raw.model_copy(update={"cost_settlement": "not_applicable"})
    if raw.usage.input_tokens is None or raw.usage.output_tokens is None:
        actual = actual.model_copy(update={"estimated_cost_microusd": reservation.estimated_cost_microusd})
        return actual, raw.model_copy(
            update={
                "estimated_cost_microusd": reservation.estimated_cost_microusd,
                "cost_settlement": "worst_case_unknown",
            }
        )
    estimated = actual.estimated_cost_microusd
    if estimated > reservation.estimated_cost_microusd:
        actual = actual.model_copy(update={"estimated_cost_microusd": reservation.estimated_cost_microusd})
        return actual, raw.model_copy(
            update={
                "estimated_cost_microusd": reservation.estimated_cost_microusd,
                "cost_settlement": "worst_case_unknown",
            }
        )
    return actual, raw.model_copy(
        update={
            "estimated_cost_microusd": estimated,
            "cost_settlement": "reported_tokens_cache_miss_conservative",
        }
    )


def _failure_result(
    case: EvalCase,
    stage: str,
    reason: str,
    *,
    method: ModelMethod,
    input_construction_version: str = MODEL_INPUT_CONSTRUCTION_VERSION,
    checkpoint: RunCheckpoint | None = None,
    execution_status: str = "failed",
    usage: ModelUsage | None = None,
    elapsed_ms: int = 0,
    run_status: str | None = None,
    run_outcome: str | None = None,
) -> RawModelCaseResult:
    return RawModelCaseResult(
        case_id=case.entry.case_id,
        split=case.entry.split,
        issue_id=case.issue.issue_id,
        issue_hash=_model_hash(case.issue),
        source_index_id=case.index_id,
        source_manifest_hash=case.manifest.snapshot_hash,
        input_construction_version=input_construction_version,
        method_fingerprint=_method_fingerprint(method),
        execution_status=execution_status,  # type: ignore[arg-type]
        run_status=run_status,  # type: ignore[arg-type]
        run_outcome=run_outcome,  # type: ignore[arg-type]
        failure_stage=stage,
        failure_reason=reason,
        project_id=checkpoint.project_id if checkpoint else None,
        session_id=checkpoint.session_id if checkpoint else None,
        run_id=checkpoint.run_id if checkpoint else None,
        input_hash=checkpoint.input_hash if checkpoint else None,
        index_id=checkpoint.index_id if checkpoint else None,
        snapshot_hash=checkpoint.snapshot_hash if checkpoint else case.manifest.snapshot_hash,
        usage=usage or ModelUsage(),
        elapsed_ms=elapsed_ms,
    )


def _budget_skipped_result(
    case: EvalCase,
    method: ModelMethod,
    reason: str,
    *,
    input_construction_version: str = MODEL_INPUT_CONSTRUCTION_VERSION,
) -> RawModelCaseResult:
    return RawModelCaseResult(
        case_id=case.entry.case_id,
        split=case.entry.split,
        issue_id=case.issue.issue_id,
        issue_hash=_model_hash(case.issue),
        source_index_id=case.index_id,
        source_manifest_hash=case.manifest.snapshot_hash,
        input_construction_version=input_construction_version,
        method_fingerprint=_method_fingerprint(method),
        execution_status="budget_skipped",
        failure_stage="evaluation_budget",
        failure_reason=reason,
        snapshot_hash=case.manifest.snapshot_hash,
    )


def _recover_uncreated_reservations(
    state: RawEvaluationState,
    cases: Sequence[EvalCase],
    method: ModelMethod,
    *,
    input_construction_version: str = MODEL_INPUT_CONSTRUCTION_VERSION,
) -> None:
    """Convert a reservation with no checkpoint into a conservative interruption."""

    case_by_id = {case.entry.case_id: case for case in cases}
    for case_id in list(state.budget.reservations):
        if case_id in state.checkpoints:
            continue
        case = case_by_id[case_id]
        reservation = state.budget.reservations[case_id]
        recovered = _failure_result(
            case,
            "recovery",
            "evaluation stopped after reserving the case budget before a Run checkpoint was durable; "
            "worst-case usage was conservatively consumed and the case will not be retried",
            method=method,
            input_construction_version=input_construction_version,
            execution_status="interrupted",
            usage=_model_usage_from_evaluation_usage(reservation),
            elapsed_ms=reservation.elapsed_ms,
            run_outcome="interrupted",
        )
        state.results[case_id] = recovered.model_copy(
            update={
                "estimated_cost_microusd": reservation.estimated_cost_microusd,
                "cost_settlement": (
                    "worst_case_unknown"
                    if method.cost_policy is not None
                    else "not_applicable"
                ),
            }
        )
        _settle_budget(state, case_id, reservation)


def _complete_case(
    application,
    case: EvalCase,
    checkpoint: RunCheckpoint,
    method: ModelMethod,
    output_dir: Path,
    *,
    input_construction_version: str = MODEL_INPUT_CONSTRUCTION_VERSION,
) -> RawModelCaseResult:
    started = time.perf_counter()
    service = application.state.workspace_service
    run_status, wait_timed_out = _wait_for_terminal(service, checkpoint, method.budget.max_seconds)
    run = run_status.run
    report = service.get_run_report(checkpoint.project_id, checkpoint.session_id, checkpoint.run_id).report
    report_path = Path("runs") / case.entry.case_id / "report.json"
    actual_index = service.get_project_index(checkpoint.project_id, checkpoint.index_id).index
    if (
        run.project_id != checkpoint.project_id
        or run.session_id != checkpoint.session_id
        or run.index_id != checkpoint.index_id
        or run.mode != "real"
        or run.issue.model_dump(mode="json") != case.issue.model_dump(mode="json")
        or run.model != method.model
        or run.model_provider != method.model_provider
        or run.prompt_version != method.prompt_version
        or run.execution_version != method.execution_version
        or run.input_hash != checkpoint.input_hash
        or report.run_id != checkpoint.run_id
        or report.project_id != checkpoint.project_id
        or report.session_id != checkpoint.session_id
        or report.index_id != checkpoint.index_id
        or report.fixture_mode
        or actual_index.index_id != checkpoint.index_id
        or actual_index.snapshot_hash != case.manifest.snapshot_hash
        or checkpoint.snapshot_hash != case.manifest.snapshot_hash
    ):
        raise ValueError(f"Run/index identity differs from frozen case {case.entry.case_id}")
    _write_json(output_dir / report_path, report.model_dump(mode="json"))
    mapping_errors: list[str] = []
    candidates: list[ModelCandidate] = []
    if report.status == "succeeded" and report.outcome == "candidate_found":
        candidates, mapping_errors = map_report_candidates(report, case)
    failure_stage: str | None = None
    failure_reason: str | None = None
    execution_status = report.status
    if wait_timed_out:
        failure_stage = "timeout"
        failure_reason = "evaluation wait exceeded the bounded recovery window"
        execution_status = "cancelled" if report.status == "cancelled" else "failed"
        candidates = []
    elif report.status != "succeeded":
        failure_stage = "run"
        failure_reason = _run_error_reason(service, checkpoint) or report.summary
        candidates = []
    elif mapping_errors:
        failure_stage = "mapping"
        failure_reason = "; ".join(mapping_errors)
        execution_status = "failed"
        candidates = []
    return RawModelCaseResult(
        case_id=case.entry.case_id,
        split=case.entry.split,
        issue_id=case.issue.issue_id,
        issue_hash=_model_hash(case.issue),
        source_index_id=case.index_id,
        source_manifest_hash=case.manifest.snapshot_hash,
        input_construction_version=input_construction_version,
        method_fingerprint=_method_fingerprint(method),
        execution_status=execution_status,  # type: ignore[arg-type]
        run_status=run.status,
        run_outcome=report.outcome,
        failure_stage=failure_stage,
        failure_reason=failure_reason,
        project_id=run.project_id,
        session_id=run.session_id,
        run_id=run.run_id,
        report_id=report.report_id,
        report_path=report_path.as_posix(),
        input_hash=run.input_hash,
        index_id=run.index_id,
        snapshot_hash=actual_index.snapshot_hash,
        model_provider=run.model_provider,
        model=run.model,
        prompt_version=run.prompt_version,
        execution_version=run.execution_version,
        candidates=candidates,
        usage=_model_usage(run.budget),
        elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
    )


def _to_scored_result(raw: RawModelCaseResult, case: EvalCase) -> CaseResult:
    candidates: list[BaselineCandidate] = []
    for candidate in raw.candidates:
        candidates.append(
            BaselineCandidate(
                rank=candidate.rank,
                symbol_id=candidate.symbol_id,
                index_id=candidate.index_id,
                path=candidate.path,
                qualified_name=candidate.qualified_name,
                kind=candidate.kind,
                start_line=candidate.start_line,
                end_line=candidate.end_line,
                matched_terms=[candidate.origin],
                evidence=EvidenceCheck(
                    legal=candidate.evidence_legal,
                    evidence_id=candidate.evidence_ids[0] if candidate.evidence_ids else None,
                    index_id=candidate.index_id,
                    path=candidate.path,
                    start_line=candidate.start_line,
                    end_line=candidate.end_line,
                    content_hash=candidate.evidence_content_hash,
                    reason=candidate.evidence_reason,
                ),
            )
        )
    status = (
        "succeeded"
        if raw.execution_status == "succeeded"
        else "skipped"
        if raw.execution_status == "budget_skipped"
        else "failed"
    )
    return CaseResult(
        case_id=case.entry.case_id,
        split=case.entry.split,
        applicable=False,
        defect_category="unscored",
        status=status,  # type: ignore[arg-type]
        failure_stage=raw.failure_stage,
        failure_reason=raw.failure_reason,
        issue_id=case.issue.issue_id,
        issue_hash=raw.issue_hash,
        index_id=raw.index_id or case.index_id,
        snapshot_hash=raw.snapshot_hash or case.manifest.snapshot_hash,
        query_terms=[],
        candidates=candidates,
        usage=BaselineUsage(
            evidence_checks=len(candidates),
            evidence_legal=sum(candidate.evidence.legal for candidate in candidates),
        ),
        elapsed_ms=raw.elapsed_ms,
        expected_files=[],
        expected_symbols=[],
        expected_locations=[],
    )


def _score_results(
    raw_results: Sequence[RawModelCaseResult],
    cases: Sequence[EvalCase],
    annotations: Mapping[str, object],
) -> list[ModelCaseResult]:
    case_by_id = {case.entry.case_id: case for case in cases}
    scored: list[ModelCaseResult] = []
    for raw in raw_results:
        case = case_by_id[raw.case_id]
        annotation = annotations[raw.case_id]
        base = _to_scored_result(raw, case)
        result = score_case(base, annotation)  # type: ignore[arg-type]
        if raw.run_outcome == "insufficient_evidence" and result.abstention_kind == "baseline_no_candidate":
            result = result.model_copy(update={"abstention_kind": "model_insufficient_evidence"})
        scored.append(ModelCaseResult(execution=raw, score=result))
    return scored


def _comparison_group(
    split: str,
    baseline: Sequence[CaseResult],
    model: Sequence[CaseResult],
) -> ComparisonGroup:
    return ComparisonGroup(
        split=split,  # type: ignore[arg-type]
        baseline=summarize(baseline),
        model=summarize(model),
    )


def build_comparison(
    model_report: ModelEvaluationReport,
    baseline_report: EvaluationReport,
    *,
    baseline_path: str,
    model_path: str,
) -> ComparisonReport:
    baseline_by_id = {case.case_id: case for case in baseline_report.cases}
    model_cases = model_report.cases
    groups: list[ComparisonGroup] = []
    for group_name, selected in (
        ("all", model_cases),
        ("calibration", [item for item in model_cases if item.score.split == "calibration"]),
        ("holdout", [item for item in model_cases if item.score.split == "holdout"]),
    ):
        if not selected:
            continue
        baseline_selected = [baseline_by_id[item.score.case_id] for item in selected]
        groups.append(_comparison_group(group_name, baseline_selected, [item.score for item in selected]))
    cases: list[ComparisonCase] = []
    for item in model_cases:
        baseline = baseline_by_id.get(item.score.case_id)
        if baseline is None:
            raise ValueError(f"baseline report is missing case {item.score.case_id}")
        note = item.execution.failure_reason
        if item.execution.run_outcome == "insufficient_evidence":
            note = "model explicitly returned insufficient_evidence"
        cases.append(
            ComparisonCase(
                case_id=item.score.case_id,
                split=item.score.split,
                baseline=comparison_metrics(baseline),
                model=comparison_metrics(item.score),
                model_execution_status=item.execution.execution_status,
                note=note,
            )
        )
    return ComparisonReport(
        dataset_id=model_report.dataset_id,
        dataset_version=model_report.dataset_version,
        method_id=model_report.method.method_id,
        baseline_report_path=baseline_path,
        model_report_path=model_path,
        simulation_notice=model_report.simulation_notice,
        groups=groups,
        cases=cases,
    )


def _metric_text(metric: Metric) -> str:
    return f"{metric.numerator}/{metric.denominator} ({'n/a' if metric.value is None else metric.value})"


def render_model_markdown(report: ModelEvaluationReport) -> str:
    external = report.method.execution_mode == "external"
    title = "P4-E2b 外部供应商 Run 评测" if external else "P4-E2a 离线假供应商 Run 评测"
    cost_summary = (
        f"；费用上限={report.evaluation_budget.max_cost_microusd / 1_000_000:.6f} USD，"
        f"已结算={report.evaluation_usage.estimated_cost_microusd / 1_000_000:.6f} USD，"
        f"价格依据={report.method.cost_policy.pricing_source_url}（检查日 {report.method.cost_policy.pricing_checked_at}）"
        if external and report.method.cost_policy is not None
        else ""
    )
    lines = [
        f"# {title}",
        "",
        f"- 数据集：`{report.dataset_id}` / `{report.dataset_version}`；划分：`{report.split}`",
        f"- 方法：`{report.method.method_id}`；provider：`{report.method.model_provider}`；模型：`{report.method.model}`",
        f"- 运行：`{report.run_id}`；代码 revision：`{report.code_revision}`",
        f"- 原始状态：`{report.raw_state_path}`；P4-E1 基线：`{report.baseline_report_path}`",
        f"- 整次预算：cases={report.evaluation_budget.max_cases}，model calls={report.evaluation_budget.max_model_calls}，"
        f"input/model input chars={report.evaluation_budget.max_input_chars}/"
        f"{report.evaluation_budget.max_model_input_chars}，output/model output chars="
        f"{report.evaluation_budget.max_output_chars}/{report.evaluation_budget.max_model_output_chars}，"
        f"seconds={report.evaluation_budget.max_seconds}{cost_summary}",
        f"- 累计已结算：cases={report.evaluation_usage.cases}，model calls={report.evaluation_usage.model_calls}，"
        f"input/model input chars={report.evaluation_usage.input_chars}/{report.evaluation_usage.model_input_chars}，"
        f"output/model output chars={report.evaluation_usage.output_chars}/"
        f"{report.evaluation_usage.model_output_chars}，elapsed_ms={report.evaluation_usage.elapsed_ms}；"
        f"未结算预留 cases={report.evaluation_reserved.cases}，"
        f"cost_microusd={report.evaluation_reserved.estimated_cost_microusd}",
        f"- 执行说明：{report.simulation_notice}",
        "",
        "## 汇总",
        "",
        f"样本 **{report.summary.total_cases}**，成功 **{report.summary.succeeded_cases}**，失败 **{report.summary.failed_cases}**，跳过 **{report.summary.skipped_cases}**；可定位 **{report.summary.applicable_cases}**，证据不足 **{report.summary.insufficient_cases}**。",
        "",
        "| 指标 | 分子/分母 | 值 |",
        "| --- | ---: | ---: |",
    ]
    for label, metric in (
        ("file Hit@1", report.summary.file_hit_at_1),
        ("file Hit@3", report.summary.file_hit_at_3),
        ("symbol Hit@1", report.summary.symbol_hit_at_1),
        ("symbol Hit@3", report.summary.symbol_hit_at_3),
        ("证据合法率", report.summary.evidence_legal_rate),
        ("模型明确证据不足率", report.summary.insufficient_abstention_rate),
        ("证据不足误报率", report.summary.insufficient_false_positive_rate),
    ):
        value = "n/a" if metric.value is None else str(metric.value)
        lines.append(f"| {label} | {metric.numerator}/{metric.denominator} | {value} |")
    lines.extend(["", "## 逐例结果", "", "| 案例 | 划分 | Run | outcome | file H@1/H@3 | symbol H@1/H@3 | 候选 |", "| --- | --- | --- | --- | --- | --- | ---: |"])
    for item in report.cases:
        score = item.score
        hit = lambda value: "n/a" if value is None else ("1" if value else "0")
        lines.append(
            f"| `{score.case_id}` | {score.split} | {item.execution.execution_status} | "
            f"{item.execution.run_outcome or 'n/a'} | {hit(score.file_hit_at_1)}/{hit(score.file_hit_at_3)} | "
            f"{hit(score.symbol_hit_at_1)}/{hit(score.symbol_hit_at_3)} | {len(item.execution.candidates)} |"
        )
    lines.extend(["", "## 候选、用量与失败详情", ""])
    for item in report.cases:
        execution = item.execution
        lines.append(f"### `{item.score.case_id}`")
        if not execution.candidates:
            lines.append("- 无最终候选。")
        for candidate in execution.candidates:
            evidence = "合法" if candidate.evidence_legal else f"非法：{candidate.evidence_reason}"
            lines.append(
                f"- #{candidate.rank} `{candidate.path}:{candidate.start_line}-{candidate.end_line}` "
                f"`{candidate.qualified_name}`；来源=`{candidate.origin}`；证据={evidence}"
            )
        usage = execution.usage
        token_text = f"{usage.input_tokens}/{usage.output_tokens}" if usage.input_tokens is not None and usage.output_tokens is not None else "unknown"
        lines.append(
            f"- 用量：model={usage.model_calls}，repair={usage.repair_calls}，tool={usage.tool_calls}，"
            f"input/model input chars={usage.input_chars}/{usage.model_input_chars}，"
            f"output/model output chars={usage.output_chars}/{usage.model_output_chars}，tokens={token_text}。"
        )
        if external:
            lines.append(
                f"- 费用估算：{execution.estimated_cost_microusd / 1_000_000:.6f} USD；"
                f"结算方式=`{execution.cost_settlement}`。"
            )
        if execution.failure_reason:
            lines.append(f"- 失败：`{execution.failure_stage}` — {execution.failure_reason}")
        lines.append("")
    boundary = (
        "外部费用按官方 USD 价格的 cache-miss 最坏档保守估算；供应商未返回 token 或请求中断时，整例保留最坏费用预留，不能把未知记为零。"
        if external
        else "token 用量未知时保持 unknown/null，整次预算使用可执行的调用/字符/墙钟最坏预留，不能据此推断 token 费用。"
    )
    lines.extend(["## 边界与局限", "", report.simulation_notice, "模型结果只取最终 candidate_link/finding 的同快照证据；中间检索符号不计为答案，失败/取消/中断不继承中间命中，budget_skipped 保留在总样本分母。", boundary, "P4-E1 基线和模型结果使用相同冻结案例及评分字段，但算术对照不能证明模型优于基线。", ""])
    return "\n".join(lines)


def render_comparison_markdown(report: ComparisonReport) -> str:
    external = report.method_id == EXTERNAL_METHOD_VERSION
    title = "P4-E2b 外部供应商与基线对照" if external else "P4-E2a 基线与 Run 评测对照"
    lines = [
        f"# {title}",
        "",
        f"- 数据集：`{report.dataset_id}` / `{report.dataset_version}`；方法：`{report.method_id}`",
        f"- 基线：`{report.baseline_report_path}`；模型报告：`{report.model_report_path}`",
        f"- 模拟说明：{report.simulation_notice}",
        "",
        "## 分组汇总",
        "",
        "| 分组 | 方法 | file H@1 | file H@3 | symbol H@1 | symbol H@3 | 失败 | 跳过 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for group in report.groups:
        model_label = "external Run" if external else "offline fake Run"
        for method, summary in (("baseline", group.baseline), (model_label, group.model)):
            lines.append(
                f"| {group.split} | {method} | {_metric_text(summary.file_hit_at_1)} | "
                f"{_metric_text(summary.file_hit_at_3)} | {_metric_text(summary.symbol_hit_at_1)} | "
                f"{_metric_text(summary.symbol_hit_at_3)} | {summary.failed_cases}/{summary.total_cases} | "
                f"{summary.skipped_cases}/{summary.total_cases} |"
            )
    model_label = "external Run" if external else "fake Run"
    lines.extend(["", "## 逐例对照", "", f"| 案例 | 划分 | 基线 symbol H@1/H@3 | {model_label} symbol H@1/H@3 | Run 状态 |", "| --- | --- | --- | --- | --- |"])
    for case in report.cases:
        hit = lambda value: "n/a" if value is None else ("1" if value else "0")
        lines.append(
            f"| `{case.case_id}` | {case.split} | "
            f"{hit(case.baseline.symbol_hit_at_1)}/{hit(case.baseline.symbol_hit_at_3)} | "
            f"{hit(case.model.symbol_hit_at_1)}/{hit(case.model.symbol_hit_at_3)} | {case.model_execution_status} |"
        )
    boundary = (
        "这是相同冻结输入上的指标对照和执行状态记录。外部 provider 的真实语义质量、网络稳定性和最终费用仍需在独立授权运行后判断；"
        if external
        else "这是相同冻结输入上的指标对照和执行状态记录。provider 是本地模拟适配器，不能据此作出真实模型优劣、根因能力或供应商质量结论；"
    )
    lines.extend(["", "## 解释边界", "", boundary + "留出集分母、失败、跳过、弃答和误报均按报告逐例保留。", ""])
    return "\n".join(lines)


def _load_baseline_report(path: Path) -> EvaluationReport:
    try:
        return EvaluationReport.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"invalid P4-E1 baseline report: {path}") from exc


def _path_reference(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.parent.resolve()).as_posix()
    except ValueError:
        return str(path)


def _default_model_config_file() -> Path:
    return ROOT.parent / ".codetrail" / "config.json"


def _resolve_external_config(
    model_config: ModelProviderConfig | None,
    config_file: Path | None,
) -> ModelProviderConfig:
    if model_config is not None and config_file is not None:
        raise ValueError("provide either model_config or config_file, not both")
    return model_config or load_model_config_file(config_file or _default_model_config_file())


def run_model_evaluation(
    frozen: FrozenDataset,
    *,
    split: str = "all",
    runtime_dir: Path | None = None,
    output_dir: Path | None = None,
    provider: str = "offline_fake",
    method: ModelMethod | None = None,
    evaluation_budget: EvaluationBudgetLimits | None = None,
    adapter_factory: Callable[[str], IssueModelAdapter] | None = None,
    resume: bool = True,
    allow_external: bool = False,
    model_config: ModelProviderConfig | None = None,
    config_file: Path | None = None,
) -> tuple[ModelEvaluationReport, ComparisonReport]:
    """Run/recover bounded real Runs, then load gold and score the results."""

    if provider not in {"offline_fake", "external"}:
        raise ValueError(f"unsupported evaluation provider: {provider}")
    external_config: ModelProviderConfig | None = None
    if provider == "external":
        if not allow_external:
            raise ValueError("P4-E2b external evaluation requires explicit allow_external=True")
        if adapter_factory is not None:
            raise ValueError("external evaluation owns the compatible HTTP adapter; custom adapters are forbidden")
        external_config = _resolve_external_config(model_config, config_file)
        if not external_config.api_key.strip():
            raise ValueError("P4-E2b external evaluation requires a non-empty key in persistent config.json")
        selected_method = method or external_method(external_config)
        if selected_method.execution_mode != "external":
            raise ValueError("external evaluation requires an external ModelMethod")
        if selected_method.provider_config_fingerprint != provider_config_fingerprint(external_config):
            raise ValueError("external provider config fingerprint differs; create a new plan")
        if selected_method.model_provider != external_config.provider or selected_method.model != external_config.model:
            raise ValueError("external provider config differs from the ModelMethod; create a new plan")
    else:
        if method is not None and method.execution_mode != "offline_fake":
            raise ValueError("offline_fake evaluation requires an offline ModelMethod")
        selected_method = method or default_method()
    selected = select_cases(frozen, split)
    if not selected:
        raise ValueError(f"evaluation split has no cases: {split}")
    result_dir_name = EXTERNAL_RESULTS_DIR if provider == "external" else MODEL_RESULTS_DIR
    output = output_dir or ROOT / "results" / result_dir_name
    runtime = runtime_dir or ROOT / ".runtime" / result_dir_name
    selected_evaluation_budget = evaluation_budget or DEFAULT_EVALUATION_BUDGET
    plan = build_dry_run_plan(
        frozen,
        split=split,
        output_dir=output,
        method=selected_method,
        evaluation_budget=selected_evaluation_budget,
    )
    revision, dirty = _git_metadata()
    identity = _evaluation_identity(
        frozen,
        selected,
        plan.method,
        plan.evaluation_budget,
        split,
        revision,
        input_construction_version=plan.input_construction_version,
    )
    fingerprint = hashlib.sha256(_canonical_json(identity.model_dump(mode="json")).encode("utf-8")).hexdigest()
    state_path = output / "raw-state.json"
    state = _load_or_create_state(
        state_path,
        fingerprint,
        resume=resume,
        identity=identity,
    )
    _recover_uncreated_reservations(
        state,
        selected,
        plan.method,
        input_construction_version=plan.input_construction_version,
    )
    _validate_state(state, identity)
    # Plan and state are written only after the existing state has passed all
    # identity checks, so a rejected resume cannot overwrite any artifact.
    write_dry_run_plan(plan, output)
    _write_json(state_path, state.model_dump(mode="json"))
    factory = adapter_factory or (lambda _mode: OfflineFakeIssueAdapter())
    started = time.perf_counter()
    for case in selected:
        case_id = case.entry.case_id
        if case_id in state.results:
            continue
        checkpoint = state.checkpoints.get(case_id)
        checkpoint_was_existing = checkpoint is not None
        reservation = state.budget.reservations.get(case_id)
        if checkpoint is None:
            if reservation is not None:
                raise ValueError(f"P4-E2a active reservation has no recovered result for {case_id}")
            worst_case = _worst_case_usage(plan.method)
            reason = _reservation_failure_reason(state.budget, worst_case)
            if reason:
                state.results[case_id] = _budget_skipped_result(
                    case,
                    plan.method,
                    reason,
                    input_construction_version=plan.input_construction_version,
                )
                _write_json(state_path, state.model_dump(mode="json"))
                continue
            _reserve_budget(state, case_id, worst_case)
            reservation = worst_case
            _write_json(state_path, state.model_dump(mode="json"))
        elif reservation is None:
            raise ValueError(f"P4-E2a checkpoint has no active reservation for {case_id}")

        assert reservation is not None
        case_started = time.perf_counter()
        case_runtime = runtime / plan.method.method_id / case_id
        source_root = Path(checkpoint.source_root) if checkpoint else case_runtime / "source"
        data_root = Path(checkpoint.data_root) if checkpoint else case_runtime / "data"
        try:
            _ensure_runtime_source(case, source_root)
            application = _new_case_app(
                source_root,
                data_root,
                None if provider == "external" else factory,
                model_config=external_config,
            )
            budget = RunBudget(**plan.method.budget.model_dump(mode="python"))
            if checkpoint is None:
                checkpoint = _start_run(
                    application,
                    case,
                    plan.method,
                    budget,
                    frozen.dataset.version,
                    input_construction_version=plan.input_construction_version,
                )
                state.checkpoints[case_id] = checkpoint
                _write_json(state_path, state.model_dump(mode="json"))
            raw = _complete_case(
                application,
                case,
                checkpoint,
                plan.method,
                output,
                input_construction_version=plan.input_construction_version,
            )
            elapsed_ms = max(raw.elapsed_ms, round((time.perf_counter() - case_started) * 1000))
            raw = raw.model_copy(update={"elapsed_ms": elapsed_ms})
            actual, raw = _settle_case_usage(
                raw,
                reservation,
                plan.method,
                elapsed_ms=elapsed_ms,
            )
            if checkpoint_was_existing:
                # A checkpoint can survive a process restart.  Its prior
                # wall-clock interval is not recoverable, so settle that
                # dimension conservatively even when the Run is terminal.
                actual = actual.model_copy(update={"elapsed_ms": reservation.elapsed_ms})
        except Exception as exc:
            raw = _failure_result(
                case,
                "orchestration",
                f"{type(exc).__name__}: {exc}",
                method=plan.method,
                input_construction_version=plan.input_construction_version,
                checkpoint=checkpoint,
                execution_status="interrupted" if checkpoint is not None else "failed",
                usage=_model_usage_from_evaluation_usage(reservation),
                elapsed_ms=reservation.elapsed_ms,
                run_outcome="interrupted" if checkpoint is not None else None,
            )
            actual = reservation
            if plan.method.cost_policy is not None:
                raw = raw.model_copy(
                    update={
                        "estimated_cost_microusd": reservation.estimated_cost_microusd,
                        "cost_settlement": "worst_case_unknown",
                    }
                )
        state.results[case.entry.case_id] = raw
        _settle_budget(state, case_id, actual)
        _write_json(state_path, state.model_dump(mode="json"))

    raw_results = [state.results[case.entry.case_id] for case in selected]
    raw_results_path = output / "model-raw.json"
    _write_json(
        raw_results_path,
        {
            "schema_version": MODEL_EVALUATION_SCHEMA_VERSION,
            "dataset_id": frozen.dataset.dataset_id,
            "dataset_version": frozen.dataset.version,
            "split": split,
            "method": plan.method.model_dump(mode="json"),
            "evaluation_budget": plan.evaluation_budget.model_dump(mode="json"),
            "evaluation_usage": state.budget.consumed.model_dump(mode="json"),
            "evaluation_reserved": state.budget.reserved.model_dump(mode="json"),
            "input_construction_version": plan.input_construction_version,
            "simulation_notice": plan.simulation_notice,
            "cases": [item.model_dump(mode="json") for item in raw_results],
        },
    )

    # Gold is intentionally loaded only after every execution result is durable.
    annotations = load_annotations_for_dataset(frozen)
    scored_cases = _score_results(raw_results, selected, annotations)
    report = ModelEvaluationReport(
        dataset_id=frozen.dataset.dataset_id,
        dataset_version=frozen.dataset.version,
        split=split,  # type: ignore[arg-type]
        run_id=f"eval-model-{fingerprint[:32]}",
        code_revision=revision,
        repository_dirty=dirty,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
        method=plan.method,
        evaluation_budget=plan.evaluation_budget,
        evaluation_usage=state.budget.consumed,
        evaluation_reserved=state.budget.reserved,
        baseline_report_path="evals/results/p4-e1-v1/baseline-all.json",
        raw_state_path=_path_reference(state_path),
        simulation_notice=plan.simulation_notice,
        summary=summarize([item.score for item in scored_cases]),
        cases=scored_cases,
    )
    model_json_path = output / f"model-{split}.json"
    model_md_path = output / f"model-{split}.md"
    _write_json(model_json_path, report.model_dump(mode="json"))
    model_md_path.write_text(render_model_markdown(report), encoding="utf-8")
    baseline_path = ROOT / "results" / "p4-e1-v1" / "baseline-all.json"
    baseline = _load_baseline_report(baseline_path)
    comparison = build_comparison(
        report,
        baseline,
        baseline_path="evals/results/p4-e1-v1/baseline-all.json",
        model_path=_path_reference(output / f"model-{split}.json"),
    )
    comparison_json_path = output / f"comparison-{split}.json"
    comparison_md_path = output / f"comparison-{split}.md"
    _write_json(comparison_json_path, comparison.model_dump(mode="json"))
    comparison_md_path.write_text(render_comparison_markdown(comparison), encoding="utf-8")
    return report, comparison


__all__ = [
    "DEFAULT_BUDGET_LIMITS",
    "DEFAULT_EVALUATION_BUDGET",
    "DEEPSEEK_COST_POLICY",
    "DEEPSEEK_PRICING_CHECKED_AT",
    "DEEPSEEK_PRICING_SOURCE_URL",
    "EXTERNAL_METHOD_VERSION",
    "EXTERNAL_MODEL",
    "EXTERNAL_PROVIDER",
    "EXTERNAL_RESULTS_DIR",
    "MODEL_METHOD_VERSION",
    "MODEL_RESULTS_DIR",
    "OFFLINE_FAKE_MODEL",
    "OfflineFakeIssueAdapter",
    "build_comparison",
    "build_dry_run_plan",
    "default_method",
    "external_method",
    "map_report_candidates",
    "provider_config_fingerprint",
    "render_comparison_markdown",
    "render_dry_run_plan",
    "render_model_markdown",
    "run_model_evaluation",
    "write_dry_run_plan",
]
