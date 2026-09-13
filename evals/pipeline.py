"""Deterministic, offline evaluation pipeline for the P4-E1 keyword baseline.

The baseline deliberately receives only an Issue and the persisted index built
from that case's frozen source snapshot.  Gold annotations are loaded by the
orchestrator only after retrieval has completed, so the retrieval procedure
cannot use answers to tune or rank its candidates.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.contracts import Issue, Symbol  # noqa: E402
from app.indexing import build_source_snapshot, extract_definitions  # noqa: E402
from app.relations import build_relation_index  # noqa: E402
from app.storage import IndexStore, StorageError  # noqa: E402

from .models import (  # noqa: E402
    BASELINE_VERSION,
    Annotation,
    BaselineCandidate,
    BaselineConfig,
    BaselineUsage,
    CaseResult,
    Dataset,
    DatasetEntry,
    EvidenceCheck,
    EvaluationReport,
    EvaluationSummary,
    EvalLocation,
    ManifestFile,
    Metric,
    SourceManifest,
)


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_STOPWORDS = frozenset(
    {
        "after",
        "and",
        "are",
        "before",
        "but",
        "can",
        "does",
        "for",
        "from",
        "into",
        "is",
        "must",
        "not",
        "only",
        "should",
        "that",
        "the",
        "this",
        "when",
        "with",
    }
)


@dataclass(frozen=True)
class EvalCase:
    """A validated case without exposing its annotation to retrieval."""

    entry: DatasetEntry
    issue: Issue
    manifest: SourceManifest
    source_root: Path
    repository_id: str
    index_id: str

    @property
    def file_hashes(self) -> Mapping[str, str]:
        return {item.path: item.sha256 for item in self.manifest.files}


@dataclass(frozen=True)
class ValidatedDataset:
    dataset: Dataset
    cases: tuple[EvalCase, ...]
    annotations: Mapping[str, Annotation]


@dataclass(frozen=True)
class FrozenDataset:
    """Validated Issue/source inputs that do not include independent labels."""

    dataset: Dataset
    cases: tuple[EvalCase, ...]


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _model_hash(model: object) -> str:
    model_dump = getattr(model, "model_dump")
    return _sha256_bytes(_canonical_json(model_dump(mode="json", by_alias=True)).encode("utf-8"))


def _resolve_eval_path(relative_path: str) -> Path:
    """Resolve one normalized path under evals and reject traversal."""

    raw = relative_path.replace("\\", "/")
    parts = raw.split("/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or any(
        part in {"", ".", ".."} for part in parts
    ):
        raise ValueError(f"evaluation path is not normalized: {relative_path!r}")
    candidate = (ROOT / Path(*parts)).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"evaluation path escapes evals: {relative_path!r}") from exc
    return candidate


def _resolve_source_path(source_root: Path, relative_path: str) -> Path:
    raw = relative_path.replace("\\", "/")
    parts = raw.split("/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or any(
        part in {"", ".", ".."} for part in parts
    ):
        raise ValueError(f"manifest source path is not normalized: {relative_path!r}")
    candidate = (source_root / Path(*parts)).resolve()
    try:
        candidate.relative_to(source_root.resolve())
    except ValueError as exc:
        raise ValueError(f"manifest path escapes the case source: {relative_path!r}") from exc
    return candidate


def _manifest_snapshot_hash(files: Sequence[ManifestFile]) -> str:
    entries = [
        {"kind": "file", "path": item.path, "size": item.size, "sha256": item.sha256}
        for item in files
    ]
    entries.sort(key=lambda item: (item["path"], item["kind"]))
    return _sha256_bytes(_canonical_json(entries).encode("utf-8"))


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read JSON {path.relative_to(ROOT)}: {exc}") from exc


def _load_issue(path: Path) -> Issue:
    try:
        return Issue.model_validate(_load_json(path))
    except ValidationError as exc:
        raise ValueError(f"invalid Issue {path.relative_to(ROOT)}: {exc}") from exc


def _load_manifest(path: Path) -> SourceManifest:
    try:
        return SourceManifest.model_validate(_load_json(path))
    except ValidationError as exc:
        raise ValueError(f"invalid source manifest {path.relative_to(ROOT)}: {exc}") from exc


def _load_annotation(path: Path) -> Annotation:
    try:
        return Annotation.model_validate(_load_json(path))
    except ValidationError as exc:
        raise ValueError(f"invalid annotation {path.relative_to(ROOT)}: {exc}") from exc


def _validate_source_manifest(source_root: Path, manifest: SourceManifest) -> None:
    if not source_root.is_dir():
        raise ValueError(f"case source directory does not exist: {source_root.relative_to(ROOT)}")

    actual_paths: list[str] = []
    for path in source_root.rglob("*.py"):
        if path.is_symlink():
            raise ValueError(f"case source contains a symlink: {path.relative_to(source_root)}")
        if path.is_file():
            actual_paths.append(path.relative_to(source_root).as_posix())
    if sorted(actual_paths) != [item.path for item in manifest.files]:
        raise ValueError(
            f"manifest file set differs from source for {manifest.case_id}: "
            f"manifest={sorted(item.path for item in manifest.files)!r}, actual={sorted(actual_paths)!r}"
        )

    for item in manifest.files:
        path = _resolve_source_path(source_root, item.path)
        raw = path.read_bytes()
        actual_hash = _sha256_bytes(raw)
        if len(raw) != item.size or actual_hash != item.sha256:
            raise ValueError(
                f"manifest mismatch for {manifest.case_id}/{item.path}: "
                f"expected size/hash {item.size}/{item.sha256}, got {len(raw)}/{actual_hash}"
            )
    actual_snapshot_hash = _manifest_snapshot_hash(manifest.files)
    if actual_snapshot_hash != manifest.snapshot_hash:
        raise ValueError(
            f"manifest snapshot hash mismatch for {manifest.case_id}: "
            f"expected {manifest.snapshot_hash}, got {actual_snapshot_hash}"
        )


def _validate_annotation(annotation: Annotation, manifest: SourceManifest, source_root: Path) -> None:
    manifest_paths = {item.path for item in manifest.files}
    for path in annotation.expected_files:
        if path not in manifest_paths:
            raise ValueError(f"annotation path is absent from manifest: {annotation.case_id}/{path}")
    for location in annotation.expected_locations:
        if location.path not in manifest_paths:
            raise ValueError(f"annotation location is absent from manifest: {annotation.case_id}/{location.path}")
        source_path = _resolve_source_path(source_root, location.path)
        line_count = len(source_path.read_text(encoding="utf-8").splitlines())
        if location.end_line > max(1, line_count):
            raise ValueError(
                f"annotation line is outside source: {annotation.case_id}/{location.path}:{location.end_line}"
            )


def load_frozen_dataset(dataset_path: Path | None = None) -> FrozenDataset:
    """Load only frozen Issue/source inputs; do not read annotation files."""

    path = dataset_path or ROOT / "dataset.json"
    try:
        dataset = Dataset.model_validate(_load_json(path))
    except ValidationError as exc:
        raise ValueError(f"invalid dataset {path}: {exc}") from exc

    cases: list[EvalCase] = []
    for entry in dataset.cases:
        source_root = _resolve_eval_path(entry.source)
        issue_path = _resolve_eval_path(entry.issue)
        manifest_path = _resolve_eval_path(entry.manifest)
        annotation_path = _resolve_eval_path(entry.annotation)
        for metadata_path in (issue_path, manifest_path, annotation_path):
            try:
                metadata_path.relative_to(source_root)
            except ValueError:
                continue
            raise ValueError(f"gold or issue metadata must stay outside source: {metadata_path}")

        issue = _load_issue(issue_path)
        manifest = _load_manifest(manifest_path)
        if manifest.case_id != entry.case_id:
            raise ValueError(f"case metadata IDs do not match dataset entry {entry.case_id}")
        _validate_source_manifest(source_root, manifest)
        manifest_paths = {item.path for item in manifest.files}
        missing_mentioned_paths = sorted(
            set(issue.mentioned_paths).difference(manifest_paths)
        )
        if missing_mentioned_paths:
            raise ValueError(
                f"Issue mentions paths absent from manifest for {entry.case_id}: "
                f"{missing_mentioned_paths}"
            )
        index_seed = f"{dataset.version}\0{entry.case_id}\0{manifest.snapshot_hash}"
        cases.append(
            EvalCase(
                entry=entry,
                issue=issue,
                manifest=manifest,
                source_root=source_root,
                repository_id=f"eval-repo-{entry.case_id}",
                index_id=f"eval-index-{_sha256_bytes(index_seed.encode('utf-8'))[:32]}",
            )
        )

    if len(cases) != 6:
        raise ValueError("p4-e1-v1 must contain exactly six frozen cases")
    if sum(item.entry.split == "calibration" for item in cases) != 3:
        raise ValueError("p4-e1-v1 must contain exactly three calibration cases")
    if sum(item.entry.split == "holdout" for item in cases) != 3:
        raise ValueError("p4-e1-v1 must contain exactly three holdout cases")

    return FrozenDataset(dataset=dataset, cases=tuple(cases))


def load_annotations_for_dataset(frozen: FrozenDataset) -> Mapping[str, Annotation]:
    """Load and validate labels after an execution path has completed."""

    annotations: dict[str, Annotation] = {}
    for case in frozen.cases:
        annotation_path = _resolve_eval_path(case.entry.annotation)
        annotation = _load_annotation(annotation_path)
        if annotation.case_id != case.entry.case_id:
            raise ValueError(f"case metadata IDs do not match dataset entry {case.entry.case_id}")
        _validate_annotation(annotation, case.manifest, case.source_root)
        annotations[case.entry.case_id] = annotation
    applicable = [
        annotations[item.entry.case_id]
        for item in frozen.cases
        if annotations[item.entry.case_id].applicable
    ]
    if len(applicable) != 4 or len(frozen.cases) - len(applicable) != 2:
        raise ValueError("p4-e1-v1 must contain four applicable and two insufficient cases")
    if len({item.defect_category for item in applicable}) < 3:
        raise ValueError("applicable cases must cover at least three defect categories")
    return annotations


def load_validated_dataset(dataset_path: Path | None = None) -> ValidatedDataset:
    """Load and validate frozen data, source hashes, and independent labels."""

    frozen = load_frozen_dataset(dataset_path)
    annotations = load_annotations_for_dataset(frozen)
    return ValidatedDataset(dataset=frozen.dataset, cases=frozen.cases, annotations=annotations)


def select_cases(
    validated: FrozenDataset | ValidatedDataset,
    split: str = "all",
) -> tuple[EvalCase, ...]:
    if split not in {"all", "calibration", "holdout"}:
        raise ValueError(f"unsupported evaluation split: {split}")
    if split == "all":
        return validated.cases
    return tuple(case for case in validated.cases if case.entry.split == split)


def build_index_for_case(store: IndexStore, case: EvalCase, dataset_version: str) -> None:
    """Index only the explicit case source and never execute its code."""

    snapshot = build_source_snapshot(
        case.source_root,
        repository_id=case.repository_id,
        revision=f"{dataset_version}:{case.entry.case_id}",
        dirty=False,
        index_id=case.index_id,
    )
    if snapshot.index.snapshot_hash != case.manifest.snapshot_hash:
        raise RuntimeError(
            f"index snapshot differs from manifest for {case.entry.case_id}: "
            f"{snapshot.index.snapshot_hash} != {case.manifest.snapshot_hash}"
        )
    if snapshot.index.status != "ready" or snapshot.issues:
        raise RuntimeError(f"source snapshot is not clean for {case.entry.case_id}: {snapshot.issues}")
    definitions = extract_definitions(snapshot)
    if definitions.issues:
        raise RuntimeError(f"definition extraction failed for {case.entry.case_id}: {definitions.issues}")
    relation = build_relation_index(
        definitions,
        max_edges=5_000,
        max_unresolved=5_000,
        max_seconds=5.0,
    )
    if relation.truncated or relation.issues:
        raise RuntimeError(
            f"relation index is incomplete for {case.entry.case_id}: "
            f"truncated={relation.truncated}, issues={relation.issues}"
        )
    store.write_index(relation)


def build_indexes(
    validated: ValidatedDataset,
    cases: Sequence[EvalCase],
    database_path: Path,
) -> dict[str, str]:
    """Build isolated SQLite indexes and return per-case build failures."""

    failures: dict[str, str] = {}
    with IndexStore(database_path) as store:
        for case in cases:
            try:
                build_index_for_case(store, case, validated.dataset.version)
            except (OSError, StorageError, RuntimeError, ValueError) as exc:
                failures[case.entry.case_id] = f"{type(exc).__name__}: {exc}"
    return failures


def extract_keywords(issue: Issue, config: BaselineConfig) -> list[str]:
    """Tokenize only title/body with stable first-seen, de-duplicated order."""

    terms: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_RE.findall(f"{issue.title}\n{issue.body}"):
        normalized = token.casefold()
        if normalized in _STOPWORDS or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(normalized)
        if len(terms) >= config.max_keywords:
            break
    return terms


@dataclass
class _CandidateAccumulator:
    symbol: Symbol
    terms: set[str]
    ranks: list[int]


def _candidate_sort_key(item: _CandidateAccumulator) -> tuple[object, ...]:
    symbol = item.symbol
    return (
        -len(item.terms),
        min(item.ranks),
        sum(item.ranks),
        symbol.path,
        symbol.start_line,
        symbol.qualified_name,
        symbol.symbol_id,
    )


def _evidence_check(store: IndexStore, case: EvalCase, symbol: Symbol, config: BaselineConfig) -> EvidenceCheck:
    if symbol.index_id != case.index_id:
        return EvidenceCheck(legal=False, reason="candidate index_id does not match case index")
    expected_hash = case.file_hashes.get(symbol.path)
    if expected_hash is None:
        return EvidenceCheck(legal=False, reason="candidate path is absent from case manifest")
    if symbol.end_line - symbol.start_line + 1 > config.source_max_lines:
        return EvidenceCheck(legal=False, reason="candidate source span exceeds evidence line budget")
    try:
        evidence = store.read_evidence(case.index_id, symbol.path, symbol.start_line, symbol.end_line)
    except StorageError as exc:
        return EvidenceCheck(legal=False, reason=f"evidence read failed: {exc}")
    if evidence.index_id != case.index_id:
        return EvidenceCheck(legal=False, reason="evidence index_id does not match case index")
    if evidence.content_hash != expected_hash:
        return EvidenceCheck(legal=False, reason="evidence hash differs from frozen manifest")
    return EvidenceCheck(
        legal=True,
        evidence_id=evidence.evidence_id,
        index_id=evidence.index_id,
        path=evidence.path,
        start_line=evidence.start_line,
        end_line=evidence.end_line,
        content_hash=evidence.content_hash,
    )


def run_case_baseline(store: IndexStore, case: EvalCase, config: BaselineConfig) -> CaseResult:
    """Run bounded FTS5 retrieval; this function has no annotation parameter."""

    started = time.perf_counter()
    query_terms = extract_keywords(case.issue, config)
    usage = BaselineUsage(keyword_count=len(query_terms))
    accumulators: dict[str, _CandidateAccumulator] = {}
    try:
        for term in query_terms:
            result = store.search_symbols_bounded(case.index_id, term, limit=config.per_term_limit)
            usage.search_calls += 1
            usage.returned_matches += len(result.matches)
            for item in result.matches:
                match = item.match
                accumulator = accumulators.get(match.symbol.symbol_id)
                if accumulator is None:
                    accumulator = _CandidateAccumulator(match.symbol, set(), [])
                    accumulators[match.symbol.symbol_id] = accumulator
                accumulator.terms.add(term)
                accumulator.ranks.append(match.rank)

        ranked = sorted(accumulators.values(), key=_candidate_sort_key)[: config.max_candidates]
        candidates: list[BaselineCandidate] = []
        for rank, accumulator in enumerate(ranked, start=1):
            symbol = accumulator.symbol
            evidence = _evidence_check(store, case, symbol, config)
            usage.evidence_checks += 1
            if evidence.legal:
                usage.evidence_legal += 1
            candidates.append(
                BaselineCandidate(
                    rank=rank,
                    symbol_id=symbol.symbol_id,
                    index_id=symbol.index_id,
                    path=symbol.path,
                    qualified_name=symbol.qualified_name,
                    kind=symbol.kind,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    matched_terms=sorted(accumulator.terms, key=lambda term: query_terms.index(term)),
                    evidence=evidence,
                )
            )
        return CaseResult(
            case_id=case.entry.case_id,
            split=case.entry.split,
            applicable=False,
            defect_category="unscored",
            status="succeeded",
            issue_id=case.issue.issue_id,
            issue_hash=_model_hash(case.issue),
            index_id=case.index_id,
            snapshot_hash=case.manifest.snapshot_hash,
            query_terms=query_terms,
            candidates=candidates,
            usage=usage,
            elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
            expected_files=[],
            expected_symbols=[],
            expected_locations=[],
        )
    except (OSError, StorageError, RuntimeError, ValueError) as exc:
        return CaseResult(
            case_id=case.entry.case_id,
            split=case.entry.split,
            applicable=False,
            defect_category="unscored",
            status="failed",
            failure_stage="retrieval",
            failure_reason=f"{type(exc).__name__}: {exc}",
            issue_id=case.issue.issue_id,
            issue_hash=_model_hash(case.issue),
            index_id=case.index_id,
            snapshot_hash=case.manifest.snapshot_hash,
            query_terms=query_terms,
            candidates=[],
            usage=usage,
            elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
            expected_files=[],
            expected_symbols=[],
            expected_locations=[],
        )


def failed_case_result(case: EvalCase, stage: str, reason: str) -> CaseResult:
    return CaseResult(
        case_id=case.entry.case_id,
        split=case.entry.split,
        applicable=False,
        defect_category="unscored",
        status="failed",
        failure_stage=stage,
        failure_reason=reason,
        issue_id=case.issue.issue_id,
        issue_hash=_model_hash(case.issue),
        index_id=case.index_id,
        snapshot_hash=case.manifest.snapshot_hash,
        query_terms=[],
        candidates=[],
        usage=BaselineUsage(),
        elapsed_ms=0,
        expected_files=[],
        expected_symbols=[],
        expected_locations=[],
    )


def _candidate_matches_file(candidate: BaselineCandidate, expected: Iterable[str]) -> bool:
    return candidate.path in set(expected)


def _candidate_matches_symbol(candidate: BaselineCandidate, expected: Iterable[str]) -> bool:
    expected_set = set(expected)
    return candidate.qualified_name in expected_set or candidate.symbol_id in expected_set


def _candidate_matches_location(candidate: BaselineCandidate, expected: Sequence[EvalLocation]) -> bool:
    return any(
        candidate.path == location.path
        and candidate.start_line <= location.end_line
        and candidate.end_line >= location.start_line
        for location in expected
    )


def score_case(result: CaseResult, annotation: Annotation) -> CaseResult:
    """Apply frozen labels after retrieval and calculate top-k localization hits."""

    top_1 = result.candidates[:1]
    top_3 = result.candidates[:3]
    if annotation.applicable:
        return result.model_copy(
            update={
                "applicable": True,
                "defect_category": annotation.defect_category,
                "expected_files": annotation.expected_files,
                "expected_symbols": annotation.expected_symbols,
                "expected_locations": annotation.expected_locations,
                "file_hit_at_1": bool(top_1 and _candidate_matches_file(top_1[0], annotation.expected_files)),
                "file_hit_at_3": any(_candidate_matches_file(item, annotation.expected_files) for item in top_3),
                "symbol_hit_at_1": bool(top_1 and _candidate_matches_symbol(top_1[0], annotation.expected_symbols)),
                "symbol_hit_at_3": any(_candidate_matches_symbol(item, annotation.expected_symbols) for item in top_3),
                "location_hit_at_1": bool(top_1 and _candidate_matches_location(top_1[0], annotation.expected_locations)),
                "location_hit_at_3": any(_candidate_matches_location(item, annotation.expected_locations) for item in top_3),
                "abstained": None,
                "false_positive": None,
                "abstention_kind": None,
            }
        )
    has_candidates = bool(result.candidates)
    return result.model_copy(
        update={
            "applicable": False,
            "defect_category": annotation.defect_category,
            "expected_files": [],
            "expected_symbols": [],
            "expected_locations": [],
            "file_hit_at_1": None,
            "file_hit_at_3": None,
            "symbol_hit_at_1": None,
            "symbol_hit_at_3": None,
            "location_hit_at_1": None,
            "location_hit_at_3": None,
            "abstained": not has_candidates if result.status == "succeeded" else False,
            "false_positive": has_candidates if result.status == "succeeded" else False,
            "abstention_kind": "baseline_no_candidate" if not has_candidates and result.status == "succeeded" else None,
        }
    )


def _metric(numerator: int, denominator: int) -> Metric:
    return Metric(
        numerator=numerator,
        denominator=denominator,
        value=None if denominator == 0 else round(numerator / denominator, 4),
    )


def summarize(results: Sequence[CaseResult]) -> EvaluationSummary:
    succeeded = sum(item.status == "succeeded" for item in results)
    applicable = [item for item in results if item.applicable]
    insufficient = [item for item in results if not item.applicable]
    evidence_checks = sum(item.usage.evidence_checks for item in results)
    evidence_legal = sum(item.usage.evidence_legal for item in results)
    return EvaluationSummary(
        total_cases=len(results),
        succeeded_cases=succeeded,
        failed_cases=len(results) - succeeded,
        applicable_cases=len(applicable),
        insufficient_cases=len(insufficient),
        file_hit_at_1=_metric(sum(item.file_hit_at_1 is True for item in applicable), len(applicable)),
        file_hit_at_3=_metric(sum(item.file_hit_at_3 is True for item in applicable), len(applicable)),
        symbol_hit_at_1=_metric(sum(item.symbol_hit_at_1 is True for item in applicable), len(applicable)),
        symbol_hit_at_3=_metric(sum(item.symbol_hit_at_3 is True for item in applicable), len(applicable)),
        evidence_legal_rate=_metric(evidence_legal, evidence_checks),
        insufficient_abstention_rate=_metric(
            sum(item.abstained is True for item in insufficient), len(insufficient)
        ),
        insufficient_false_positive_rate=_metric(
            sum(item.false_positive is True for item in insufficient), len(insufficient)
        ),
    )


def _git_metadata() -> tuple[str, bool]:
    revision = "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            revision = result.stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return revision, bool(status.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return revision, True


def _run_key(dataset: Dataset, cases: Sequence[EvalCase], config: BaselineConfig, split: str, revision: str) -> str:
    payload = {
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.version,
        "case_ids": [case.entry.case_id for case in cases],
        "manifests": [case.manifest.snapshot_hash for case in cases],
        "config": config.model_dump(mode="json"),
        "split": split,
        "code_revision": revision,
    }
    return _sha256_bytes(_canonical_json(payload).encode("utf-8"))[:32]


def run_evaluation(
    validated: ValidatedDataset,
    *,
    split: str = "all",
    runtime_dir: Path | None = None,
    output_dir: Path | None = None,
    config: BaselineConfig | None = None,
) -> EvaluationReport:
    config = config or BaselineConfig()
    cases = select_cases(validated, split)
    if not cases:
        raise ValueError(f"evaluation split has no cases: {split}")
    revision, dirty = _git_metadata()
    run_key = _run_key(validated.dataset, cases, config, split, revision)
    runtime_dir = runtime_dir or ROOT / ".runtime"
    output_dir = output_dir or ROOT / "results" / validated.dataset.version
    database_path = runtime_dir / f"{run_key}.sqlite3"
    build_failures = build_indexes(validated, cases, database_path)

    started = time.perf_counter()
    results: list[CaseResult] = []
    with IndexStore(database_path) as store:
        for case in cases:
            result = (
                failed_case_result(case, "index", build_failures[case.entry.case_id])
                if case.entry.case_id in build_failures
                else run_case_baseline(store, case, config)
            )
            results.append(score_case(result, validated.annotations[case.entry.case_id]))
    elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
    report = EvaluationReport(
        schema_version="p4-e1",
        dataset_id=validated.dataset.dataset_id,
        dataset_version=validated.dataset.version,
        baseline_version=BASELINE_VERSION,
        split=split,  # type: ignore[arg-type]
        run_id=f"eval-run-{run_key}",
        code_revision=revision,
        repository_dirty=dirty,
        generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        elapsed_ms=elapsed_ms,
        config=config,
        summary=summarize(results),
        cases=results,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"baseline-{split}.json").write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / f"baseline-{split}.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def render_markdown(report: EvaluationReport) -> str:
    def metric_text(metric: Metric) -> tuple[str, str]:
        return f"{metric.numerator}/{metric.denominator}", (
            "n/a" if metric.value is None else str(metric.value)
        )

    lines = [
        "# P4-E1 离线关键词基线",
        "",
        f"- 数据集：`{report.dataset_id}` / `{report.dataset_version}`",
        f"- 基线：`{report.baseline_version}`，划分：`{report.split}`",
        f"- 运行：`{report.run_id}`，代码 revision：`{report.code_revision}`",
        f"- 输入仓库状态：`{'dirty' if report.repository_dirty else 'clean'}`；模型调用：`{report.config.model_calls}`；网络调用：`{report.config.network_calls}`",
        "",
        "## 汇总",
        "",
        f"样本 **{report.summary.total_cases}**，成功 **{report.summary.succeeded_cases}**，失败 **{report.summary.failed_cases}**；可定位 **{report.summary.applicable_cases}**，证据不足 **{report.summary.insufficient_cases}**。",
        "",
        "| 指标 | 分子/分母 | 值 |",
        "| --- | ---: | ---: |",
        *[
            f"| {label} | {metric_text(metric)[0]} | {metric_text(metric)[1]} |"
            for label, metric in (
                ("file Hit@1", report.summary.file_hit_at_1),
                ("file Hit@3", report.summary.file_hit_at_3),
                ("symbol Hit@1", report.summary.symbol_hit_at_1),
                ("symbol Hit@3", report.summary.symbol_hit_at_3),
                ("证据位置/hash/归属合法率", report.summary.evidence_legal_rate),
                ("证据不足基线无候选率", report.summary.insufficient_abstention_rate),
                ("证据不足基线误报率", report.summary.insufficient_false_positive_rate),
            )
        ],
        "",
        "## 逐例结果",
        "",
        "| 案例 | 划分 | 状态 | 类型 | file H@1/H@3 | symbol H@1/H@3 | location H@1/H@3 | 候选 |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for case in report.cases:
        hit = lambda value: "n/a" if value is None else ("1" if value else "0")
        lines.append(
            f"| `{case.case_id}` | {case.split} | {case.status} | {case.defect_category} | "
            f"{hit(case.file_hit_at_1)}/{hit(case.file_hit_at_3)} | "
            f"{hit(case.symbol_hit_at_1)}/{hit(case.symbol_hit_at_3)} | "
            f"{hit(case.location_hit_at_1)}/{hit(case.location_hit_at_3)} | {len(case.candidates)} |"
        )
    lines.extend(["", "## 候选与失败详情", ""])
    for case in report.cases:
        lines.append(f"### `{case.case_id}`")
        if not case.candidates and not case.failure_reason:
            lines.append("- 无候选或失败信息。")
        for candidate in case.candidates:
            evidence = "合法" if candidate.evidence.legal else f"非法：{candidate.evidence.reason}"
            lines.append(
                f"- #{candidate.rank} `{candidate.path}:{candidate.start_line}-{candidate.end_line}` "
                f"`{candidate.qualified_name}`；terms=`{','.join(candidate.matched_terms)}`；证据={evidence}"
            )
        if case.failure_reason:
            lines.append(f"- 失败：`{case.failure_stage}` — {case.failure_reason}")
        lines.append("")
    lines.extend(
        [
            "",
            "## 边界与局限",
            "",
            "本报告只衡量固定 Issue 在修复前源码索引上的确定性关键词排序和证据合法性，不衡量根因推理、修复正确性或真实模型质量。关键词基线不读取 `evals/annotations/`，不运行案例代码/测试，不访问网络或真实模型；`abstention` 在本报告中仅表示基线没有候选，不能冒充模型弃答。",
            "",
            "每例源码由 manifest/hash 冻结，评分标注位于源码目录外；耗时、运行标识和工作树状态属于运行元数据，可能随执行环境变化。",
            "",
        ]
    )
    return "\n".join(lines)
