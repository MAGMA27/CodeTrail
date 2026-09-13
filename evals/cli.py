"""Command line entry points for the offline P4 evaluation workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .model_pipeline import (
    DEFAULT_EVALUATION_BUDGET,
    build_dry_run_plan,
    run_model_evaluation,
    write_dry_run_plan,
)
from .model_models import EvaluationBudgetLimits
from .models import EvaluationReport
from .pipeline import (
    ROOT,
    build_indexes,
    load_frozen_dataset,
    load_validated_dataset,
    render_markdown,
    run_evaluation,
    select_cases,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run CodeTrail's deterministic offline evaluation.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate frozen data and manifests")
    validate.add_argument("--dataset", type=Path, default=ROOT / "dataset.json")

    build = subparsers.add_parser("build-index", help="build isolated SQLite indexes")
    build.add_argument("--dataset", type=Path, default=ROOT / "dataset.json")
    build.add_argument("--split", choices=["all", "calibration", "holdout"], default="all")
    build.add_argument("--runtime-dir", type=Path, default=ROOT / ".runtime")

    run = subparsers.add_parser("run", help="run the baseline and write JSON/Markdown results")
    run.add_argument("--dataset", type=Path, default=ROOT / "dataset.json")
    run.add_argument("--split", choices=["all", "calibration", "holdout"], default="all")
    run.add_argument("--runtime-dir", type=Path, default=ROOT / ".runtime")
    run.add_argument("--output-dir", type=Path, default=ROOT / "results" / "p4-e1-v1")

    model_plan = subparsers.add_parser("model-plan", help="write a gold-free P4-E2a execution checklist")
    model_plan.add_argument("--dataset", type=Path, default=ROOT / "dataset.json")
    model_plan.add_argument("--split", choices=["all", "calibration", "holdout"], default="all")
    model_plan.add_argument("--output-dir", type=Path, default=ROOT / "results" / "p4-e2a-fix-v1")
    _add_evaluation_budget_args(model_plan)

    model_run = subparsers.add_parser("model-run", help="run bounded real Runs with the offline fake provider")
    model_run.add_argument("--dataset", type=Path, default=ROOT / "dataset.json")
    model_run.add_argument("--split", choices=["all", "calibration", "holdout"], default="all")
    model_run.add_argument("--runtime-dir", type=Path, default=ROOT / ".runtime" / "p4-e2a-fix-v1")
    model_run.add_argument("--output-dir", type=Path, default=ROOT / "results" / "p4-e2a-fix-v1")
    model_run.add_argument(
        "--provider",
        choices=["offline_fake", "external"],
        default="offline_fake",
        help="external is reserved for the separately authorized P4-E2b run",
    )
    model_run.add_argument("--no-resume", action="store_true", help="refuse an existing raw state file")
    _add_evaluation_budget_args(model_run)

    render = subparsers.add_parser("render-baseline", help="rerender an existing P4-E1 JSON report")
    render.add_argument(
        "--report-json",
        type=Path,
        default=ROOT / "results" / "p4-e1-v1" / "baseline-all.json",
    )
    render.add_argument("--output", type=Path, default=None)
    return parser


def _add_evaluation_budget_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-eval-cases", type=int, default=None)
    parser.add_argument("--max-eval-model-calls", type=int, default=None)
    parser.add_argument("--max-eval-input-chars", type=int, default=None)
    parser.add_argument("--max-eval-model-input-chars", type=int, default=None)
    parser.add_argument("--max-eval-output-chars", type=int, default=None)
    parser.add_argument("--max-eval-model-output-chars", type=int, default=None)
    parser.add_argument("--max-eval-seconds", type=int, default=None)


def _evaluation_budget(args: argparse.Namespace) -> EvaluationBudgetLimits:
    values = DEFAULT_EVALUATION_BUDGET.model_dump(mode="python")
    argument_names = {
        "max_cases": "max_eval_cases",
        "max_model_calls": "max_eval_model_calls",
        "max_input_chars": "max_eval_input_chars",
        "max_model_input_chars": "max_eval_model_input_chars",
        "max_output_chars": "max_eval_output_chars",
        "max_model_output_chars": "max_eval_model_output_chars",
        "max_seconds": "max_eval_seconds",
    }
    for field, argument in argument_names.items():
        value = getattr(args, argument)
        if value is not None:
            values[field] = value
    return EvaluationBudgetLimits(**values)


def main() -> None:
    args = _parser().parse_args()
    if args.command == "validate":
        validated = load_validated_dataset(args.dataset)
        print(
            f"valid dataset={validated.dataset.dataset_id} version={validated.dataset.version} "
            f"cases={len(validated.cases)}"
        )
        return
    if args.command == "model-plan":
        frozen = load_frozen_dataset(args.dataset)
        plan = build_dry_run_plan(
            frozen,
            split=args.split,
            output_dir=args.output_dir,
            evaluation_budget=_evaluation_budget(args),
        )
        json_path, markdown_path = write_dry_run_plan(plan, args.output_dir)
        print(f"wrote {json_path} and {markdown_path} cases={len(plan.cases)}")
        return
    if args.command == "model-run":
        frozen = load_frozen_dataset(args.dataset)
        report, comparison = run_model_evaluation(
            frozen,
            split=args.split,
            runtime_dir=args.runtime_dir,
            output_dir=args.output_dir,
            provider=args.provider,
            evaluation_budget=_evaluation_budget(args),
            resume=not args.no_resume,
        )
        print(
            f"wrote {args.output_dir / f'model-{args.split}.json'} and "
            f"{args.output_dir / f'comparison-{args.split}.json'} "
            f"cases={report.summary.total_cases} failed={report.summary.failed_cases} "
            f"model_provider={report.method.model_provider}"
        )
        return
    if args.command == "render-baseline":
        try:
            report = EvaluationReport.model_validate(json.loads(args.report_json.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise SystemExit(f"could not load baseline report: {exc}") from exc
        output = args.output or args.report_json.with_suffix(".md")
        output.write_text(render_markdown(report), encoding="utf-8")
        print(f"wrote {output}")
        return

    validated = load_validated_dataset(args.dataset)
    cases = select_cases(validated, args.split)
    if args.command == "build-index":
        failures = build_indexes(validated, cases, args.runtime_dir / "indexes.sqlite3")
        if failures:
            for case_id, reason in failures.items():
                print(f"failed {case_id}: {reason}")
            raise SystemExit(1)
        print(f"built {len(cases)} indexes in {args.runtime_dir}")
        return
    report = run_evaluation(
        validated,
        split=args.split,
        runtime_dir=args.runtime_dir,
        output_dir=args.output_dir,
    )
    print(
        f"wrote {args.output_dir / f'baseline-{args.split}.json'} "
        f"cases={report.summary.total_cases} failed={report.summary.failed_cases}"
    )


if __name__ == "__main__":
    main()
