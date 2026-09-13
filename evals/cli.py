"""Command line entry points for the offline P4-E1 evaluation."""

from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import ROOT, build_indexes, load_validated_dataset, run_evaluation, select_cases


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
    return parser


def main() -> None:
    args = _parser().parse_args()
    validated = load_validated_dataset(args.dataset)
    if args.command == "validate":
        print(
            f"valid dataset={validated.dataset.dataset_id} version={validated.dataset.version} "
            f"cases={len(validated.cases)}"
        )
        return
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
