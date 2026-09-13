"""Bounded Run execution for the local CodeTrail workspace.

This module deliberately keeps execution small: one daemon thread per submitted
Run, a bounded semaphore, and the same IndexService used by HTTP and MCP.  It
does not execute repository code, invoke a shell, or maintain a second tool
implementation. Real Runs use one bounded model/tool loop.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .contracts import (
    CandidateLink,
    Event,
    Finding,
    FindingCreatedEvent,
    FindingCreatedPayload,
    GetNeighborsFailure,
    GetNeighborsParams,
    GetNeighborsSuccess,
    GraphUpdatedEvent,
    GraphUpdatedPayload,
    GraphSnapshot,
    ReadSourceFailure,
    ReadSourceParams,
    ReadSourceSuccess,
    Report,
    Run,
    RunCompletedEvent,
    RunCompletedPayload,
    RunError,
    RunFailedEvent,
    RunFailedPayload,
    RunStartedEvent,
    RunStartedPayload,
    SearchCodeFailure,
    SearchCodeParams,
    SearchCodeSuccess,
    StageStartedEvent,
    StageStartedPayload,
    ToolCompletedEvent,
    ToolCompletedPayload,
    ToolFailedEvent,
    ToolFailedPayload,
    ToolRequest,
    ToolResult,
    ToolStartedEvent,
    ToolStartedPayload,
    ToolParams,
)
from .feature_generation import FeatureGenerationErrorBase, ModelCall
from .issue_analysis import (
    IssueAnalysisContext,
    IssueAnalysisEngine,
    IssueAnalysisFailure,
    IssueModelAdapter,
    default_issue_adapter,
)
from .relations import relation_coverage_limitations
from .storage import (
    IndexStore,
    RunRecord,
    StorageConflictError,
    StorageError,
    StorageInputError,
    StorageNotFoundError,
)


class RunExecutorCapacityError(RuntimeError):
    """Raised when the executor has no admission slot for a new Run."""


class RunExecutorStartupError(RuntimeError):
    """Raised when a persisted Run cannot be handed to a worker thread."""


class RunReservation:
    """One idempotent executor capacity reservation."""

    def __init__(self, release_callback: Callable[[], None]) -> None:
        self._release_callback = release_callback
        self._released = False
        self._lock = threading.Lock()

    @property
    def released(self) -> bool:
        with self._lock:
            return self._released

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._release_callback()


@dataclass(frozen=True)
class RunExecutorState:
    """Bounded runtime counters used by tests and local diagnostics."""

    capacity: int
    reserved: int
    worker_threads: int
    tracked_threads: int
    waiting_runs: int


class RunExecutor:
    """Run explicit fixture or real analyses with bounded admission and no wait queue."""

    def __init__(
        self,
        workspace: Any,
        *,
        max_concurrent_runs: int = 2,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
        model_adapter_factory: Callable[[str], IssueModelAdapter] | None = None,
    ) -> None:
        if max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be positive")
        self.workspace = workspace
        self.max_concurrent_runs = max_concurrent_runs
        self._slots = threading.BoundedSemaphore(max_concurrent_runs)
        self._threads: set[threading.Thread] = set()
        self._threads_lock = threading.RLock()
        self._reserved_count = 0
        self.thread_factory = thread_factory
        self.model_adapter_factory = model_adapter_factory or (
            lambda mode: default_issue_adapter(mode, model_config=workspace.registry.model_config)
        )
        self._cancel_events: dict[str, threading.Event] = {}
        # Tests and local demos may use this to make cancellation observable.
        # It is intentionally not part of the public request contract.
        self.tool_delay_seconds = 0.0

    def reserve(self) -> RunReservation:
        """Reserve one execution slot without creating a waiting worker."""

        if not self._slots.acquire(blocking=False):
            raise RunExecutorCapacityError("local Run executor capacity is full")
        with self._threads_lock:
            self._reserved_count += 1
        return RunReservation(self._release_slot)

    def submit(self, run: Run, *, reservation: RunReservation | None = None) -> None:
        """Start one worker using a caller-owned or newly acquired reservation."""

        admission = reservation or self.reserve()
        if admission.released:
            raise ValueError("Run executor reservation was already released")
        thread: threading.Thread | None = None
        try:
            with self._threads_lock:
                self._cancel_events[run.run_id] = threading.Event()
            thread = self.thread_factory(
                target=self._run_guarded,
                args=(run, admission),
                name=f"codetrail-run-{run.run_id[:20]}",
                daemon=True,
            )
            with self._threads_lock:
                self._threads.add(thread)
            thread.start()
        except Exception as exc:
            with self._threads_lock:
                if thread is not None:
                    self._threads.discard(thread)
                self._cancel_events.pop(run.run_id, None)
            admission.release()
            # The Run has already been persisted by WorkspaceService. Turn a
            # worker-start failure into a queryable terminal Run before
            # reporting the submission error to the caller.
            try:
                self._finish_failure(
                    run,
                    code="internal_error",
                    message=f"{run.mode} Run worker could not start",
                    evidence=(),
                    symbols=(),
                    static_edges=(),
                    candidate_links=(),
                )
            except (StorageConflictError, StorageNotFoundError, StorageError):
                # The original startup failure remains the useful error. If
                # storage is unavailable, no additional state can be made
                # durable from this process.
                pass
            raise RunExecutorStartupError("fixture Run worker could not start") from exc

    def signal_cancel(self, run_id: str) -> None:
        """Wake a test-gated worker; storage remains the cancellation source of truth."""

        with self._threads_lock:
            cancel_event = self._cancel_events.get(run_id)
        if cancel_event is not None:
            cancel_event.set()

    def state(self) -> RunExecutorState:
        """Return bounded worker/admission counters without exposing a queue."""

        with self._threads_lock:
            worker_threads = sum(1 for thread in self._threads if thread.is_alive())
            return RunExecutorState(
                capacity=self.max_concurrent_runs,
                reserved=self._reserved_count,
                worker_threads=worker_threads,
                tracked_threads=len(self._threads),
                waiting_runs=0,
            )

    def _release_slot(self) -> None:
        with self._threads_lock:
            if self._reserved_count < 1:
                raise RuntimeError("Run executor capacity was released too many times")
            self._reserved_count -= 1
        self._slots.release()

    def _run_guarded(self, run: Run, admission: RunReservation) -> None:
        thread = threading.current_thread()
        deadline = time.monotonic() + float(run.budget.max_seconds)
        try:
            self._execute(run, deadline)
        except (StorageConflictError, StorageNotFoundError):
            # A concurrent cancel or a project removal has already made the Run
            # terminal/out of scope. The persisted state is the source of truth.
            return
        except StorageInputError as exc:
            message = str(exc)
            code = "budget_exceeded" if _is_budget_error(message) else "internal_error"
            self._finish_failure(
                run,
                code=code,
                message=message if code == "budget_exceeded" else f"{run.mode} Run execution received invalid input",
                evidence=(),
                symbols=(),
                static_edges=(),
                candidate_links=(),
            )
        except Exception:
            self._finish_failure(
                run,
                code="internal_error",
                message="fixture Run execution failed unexpectedly",
                evidence=(),
                symbols=(),
                static_edges=(),
                candidate_links=(),
            )
        finally:
            admission.release()
            with self._threads_lock:
                self._threads.discard(thread)
                self._cancel_events.pop(run.run_id, None)

    def _execute(self, initial_run: Run, deadline: float) -> None:
        record = self._load(initial_run)
        started_at = datetime.now(timezone.utc)
        start_event = RunStartedEvent(
            run_id=record.run.run_id,
            sequence=record.run.budget.events_used + 1,
            timestamp=started_at,
            payload=RunStartedPayload(
                project_id=record.run.project_id,
                session_id=record.run.session_id,
                index_id=record.run.index_id,
                issue_id=record.run.issue.issue_id,
                mode=record.run.mode,
                execution_source=record.run.execution_source,
                message_end_sequence=record.run.message_end_sequence,
                input_hash=record.run.input_hash,
                feature_generation_id=record.run.feature_generation_id,
                execution_version=record.run.execution_version,
                budget=record.run.budget,
            ),
        )
        record = self._with_store(record, lambda store: store.start_run(record.run.run_id, started_at, start_event))
        if record is None:
            return
        symbols: dict[str, Any] = {}
        evidence: list[Any] = []
        static_edges: list[Any] = []
        candidate_links: list[CandidateLink] = []
        limitations: list[str] = []

        if record.run.mode == "real":
            self._execute_real(record, deadline)
            return

        self._append_stage(record, "retrieval", deadline=deadline)
        search_query = (
            "load_settings"
            if record.run.fixture_scenario in {"success-timeout-missing", "tool-failure-read-source"}
            else "external service integration"
        )
        search_result = self._call_tool(
            record,
            SearchCodeParams(query=search_query, limit=8),
            deadline=deadline,
        )
        if isinstance(search_result, SearchCodeFailure):
            self._finish_failure(
                record.run,
                code="tool_failed",
                message=search_result.error.message,
                evidence=evidence,
                symbols=tuple(symbols.values()),
                static_edges=static_edges,
                candidate_links=(),
            )
            return
        assert isinstance(search_result, SearchCodeSuccess)
        for match in search_result.matches:
            symbols[match.symbol.symbol_id] = match.symbol

        if record.run.fixture_scenario == "insufficient-external-service" or not search_result.matches:
            self._finish_success(
                record.run,
                outcome="insufficient_evidence",
                summary="No indexed symbol matched the external-service issue; evidence is insufficient.",
                evidence=evidence,
                symbols=tuple(symbols.values()),
                static_edges=static_edges,
                candidate_links=(),
                deadline=deadline,
                finding_text="The fixed snapshot contains no direct local symbol evidence for this issue.",
            )
            return

        target = _choose_target(search_result.matches, record.run.fixture_scenario)
        read_result = self._call_tool(
            record,
            ReadSourceParams(
                path=target.path,
                start_line=target.start_line,
                end_line=min(target.end_line, target.start_line + 39),
            ),
            deadline=deadline,
        )
        if isinstance(read_result, ReadSourceFailure):
            self._finish_failure(
                record.run,
                code="tool_failed",
                message=read_result.error.message,
                evidence=evidence,
                symbols=tuple(symbols.values()),
                static_edges=static_edges,
                candidate_links=(),
            )
            return
        assert isinstance(read_result, ReadSourceSuccess)
        evidence.append(read_result.evidence)

        if record.run.fixture_scenario == "tool-failure-read-source":
            client_read = self._call_tool(
                record,
                ReadSourceParams(
                    path="timeout_demo/client.py",
                    start_line=16,
                    end_line=19,
                ),
                deadline=deadline,
                inject_failure=True,
            )
            if isinstance(client_read, ReadSourceFailure):
                self._finish_failure(
                    record.run,
                    code="tool_failed",
                    message=client_read.error.message,
                    evidence=evidence,
                    symbols=tuple(symbols.values()),
                    static_edges=static_edges,
                    candidate_links=(),
                )
                return

        if record.run.fixture_scenario == "success-timeout-missing":
            neighbors_result = self._call_tool(
                record,
                GetNeighborsParams(
                    symbol_id=target.symbol_id,
                    direction="both",
                    depth=2,
                    limit=20,
                ),
                deadline=deadline,
            )
            if isinstance(neighbors_result, GetNeighborsFailure):
                self._finish_failure(
                    record.run,
                    code="tool_failed",
                    message=neighbors_result.error.message,
                    evidence=evidence,
                    symbols=tuple(symbols.values()),
                    static_edges=static_edges,
                    candidate_links=(),
                )
                return
            assert isinstance(neighbors_result, GetNeighborsSuccess)
            for node in neighbors_result.nodes:
                symbols[node.symbol_id] = node
            if neighbors_result.root is not None:
                symbols[neighbors_result.root.symbol_id] = neighbors_result.root
            static_edges.extend(neighbor.edge for neighbor in neighbors_result.neighbors)
            limitations.extend(
                relation_coverage_limitations(
                    truncated=neighbors_result.index_truncated,
                    truncation_reason=neighbors_result.index_truncation_reason,
                    unresolved_truncated=neighbors_result.index_unresolved_truncated,
                    unresolved_truncation_reason=neighbors_result.index_unresolved_truncation_reason,
                )
            )

        self._append_stage(record, "analysis", deadline=deadline)
        candidate_links.append(
            CandidateLink(
                candidate_id=f"candidate-{record.run.run_id}",
                index_id=record.run.index_id,
                source_symbol_id=target.symbol_id,
                target_symbol_id=None,
                relation="missing timeout initialization may reach the client construction path",
                evidence_ids=[read_result.evidence.evidence_id],
            )
        )
        finding = Finding(
            finding_id=f"finding-{record.run.run_id}",
            kind="hypothesis",
            text="The indexed settings path can omit timeout before client initialization; verify the caller's environment contract.",
            symbol_ids=[target.symbol_id],
            evidence_ids=[read_result.evidence.evidence_id],
        )
        self._append_finding(record, finding, deadline=deadline)
        self._finish_success(
            record.run,
            outcome="candidate_found",
            summary="Fixture retrieval found a bounded candidate and linked it to same-snapshot source evidence.",
            evidence=evidence,
            symbols=tuple(symbols.values()),
            static_edges=static_edges,
            candidate_links=candidate_links,
            deadline=deadline,
            finding_text=finding.text,
            findings=[finding],
            limitations=limitations,
        )

    def _execute_real(self, record: RunRecord, deadline: float) -> None:
        context = IssueAnalysisContext(
            project_id=record.run.project_id,
            index_id=record.run.index_id,
            issue=record.run.issue,
            run_input=record.input,
        )

        def ensure_running() -> None:
            self._ensure_running(self._load(record.run), deadline)

        def current_budget():
            return self._load(record.run).run.budget

        def reserve_model_call(input_chars: int, repair: bool) -> None:
            current = self._load(record.run)
            self._ensure_running(current, deadline)
            self._with_store(
                current,
                lambda store: store.reserve_run_model_call(
                    current.run.run_id,
                    input_chars=input_chars,
                    repair=repair,
                ),
            )

        def record_model_output(call: ModelCall) -> bool:
            current = self._load(record.run)
            if current.run.status not in {"queued", "running"}:
                return False
            updated = self._with_store(
                current,
                lambda store: store.record_run_model_output(
                    current.run.run_id,
                    output_chars=len(call.raw_output),
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                ),
            )
            return updated.run.status in {"queued", "running"}

        try:
            self._append_stage(record, "retrieval", deadline=deadline)
            adapter = self.model_adapter_factory("real")
            result = IssueAnalysisEngine(adapter).run(
                context,
                model=record.run.model or "",
                budget=record.run.budget,
                started=deadline - float(record.run.budget.max_seconds),
                ensure_running=ensure_running,
                current_budget=current_budget,
                reserve_model_call=reserve_model_call,
                record_model_output=record_model_output,
                call_tool=lambda params: self._call_tool(record, params, deadline=deadline),
            )
        except IssueAnalysisFailure as exc:
            if exc.code == "cancelled":
                return
            self._finish_failure(
                record.run,
                code=exc.code,
                message=exc.message,
                evidence=list(exc.context.evidence.values()),
                symbols=tuple(exc.context.observed_symbols.values()),
                static_edges=list(exc.context.static_edges),
                candidate_links=(),
                limitations=exc.context.limitations,
            )
            return
        except FeatureGenerationErrorBase as exc:
            self._finish_failure(
                record.run,
                code=exc.kind,
                message=exc.message,
                evidence=list(context.evidence.values()),
                symbols=tuple(context.observed_symbols.values()),
                static_edges=list(context.static_edges),
                candidate_links=(),
                limitations=context.limitations,
            )
            return
        except StorageInputError as exc:
            self._finish_failure(
                record.run,
                code="budget_exceeded" if _is_budget_error(str(exc)) else "internal_error",
                message=str(exc),
                evidence=list(context.evidence.values()),
                symbols=tuple(context.observed_symbols.values()),
                static_edges=list(context.static_edges),
                candidate_links=(),
                limitations=context.limitations,
            )
            return
        except StorageConflictError:
            return
        except Exception:
            self._finish_failure(
                record.run,
                code="internal_error",
                message="real Run execution failed unexpectedly",
                evidence=list(context.evidence.values()),
                symbols=tuple(context.observed_symbols.values()),
                static_edges=list(context.static_edges),
                candidate_links=(),
                limitations=context.limitations,
            )
            return

        try:
            self._append_stage(record, "analysis", deadline=deadline)
            for finding in result.final.findings:
                self._append_finding(record, finding, deadline=deadline)
            self._finish_success(
                record.run,
                outcome=result.final.outcome,
                summary=result.final.summary,
                evidence=list(result.context.evidence.values()),
                symbols=tuple(result.context.observed_symbols.values()),
                static_edges=list(result.context.static_edges),
                candidate_links=list(result.final.candidate_links),
                deadline=deadline,
                finding_text=result.final.summary,
                findings=list(result.final.findings),
                limitations=result.context.limitations,
            )
        except StorageConflictError:
            return
        except StorageInputError as exc:
            self._finish_failure(
                record.run,
                code="budget_exceeded" if _is_budget_error(str(exc)) else "internal_error",
                message=str(exc),
                evidence=list(result.context.evidence.values()),
                symbols=tuple(result.context.observed_symbols.values()),
                static_edges=list(result.context.static_edges),
                candidate_links=(),
                limitations=result.context.limitations,
            )

    def _call_tool(
        self,
        record: RunRecord,
        params: Any,
        *,
        deadline: float,
        inject_failure: bool = False,
    ) -> ToolResult:
        current = self._load(record.run)
        self._ensure_running(current, deadline)
        request = ToolRequest(
            request_id=f"{current.run.run_id}-{params.tool}-{current.run.budget.tool_calls_used + 1}",
            run_id=current.run.run_id,
            index_id=current.run.index_id,
            params=params,
        )
        started_sequence = current.run.budget.events_used + 1
        started_event = ToolStartedEvent(
            run_id=current.run.run_id,
            sequence=started_sequence,
            timestamp=datetime.now(timezone.utc),
            payload=ToolStartedPayload(request=request),
        )
        self._with_store(current, lambda store: store.append_run_event(started_event))
        if self.tool_delay_seconds > 0:
            with self._threads_lock:
                cancel_event = self._cancel_events.get(current.run.run_id)
            if cancel_event is None:
                time.sleep(self.tool_delay_seconds)
            else:
                cancel_event.wait(self.tool_delay_seconds)
        current = self._load(current.run)
        self._ensure_running(current, deadline)
        completed_sequence = current.run.budget.events_used + 1
        if inject_failure and isinstance(params, ReadSourceParams):
            result: ToolResult = ReadSourceFailure(
                request_id=request.request_id,
                run_id=request.run_id,
                index_id=request.index_id,
                completed_sequence=completed_sequence,
                error={
                    "code": "fixture_read_source_error",
                    "message": "fixture injected read_source failure for the client evidence step",
                    "retryable": False,
                },
            )
        else:
            result = self.workspace.index_service.execute_tool(request, completed_sequence=completed_sequence)
        if isinstance(result, (SearchCodeFailure, ReadSourceFailure, GetNeighborsFailure)):
            event: Event = ToolFailedEvent(
                run_id=request.run_id,
                sequence=completed_sequence,
                timestamp=datetime.now(timezone.utc),
                payload=ToolFailedPayload(result=result),
            )
        else:
            event = ToolCompletedEvent(
                run_id=request.run_id,
                sequence=completed_sequence,
                timestamp=datetime.now(timezone.utc),
                payload=ToolCompletedPayload(result=result),
            )
        self._with_store(current, lambda store: store.append_run_tool_result(event, result))
        return result

    def _append_stage(self, record: RunRecord, stage: str, *, deadline: float) -> None:
        current = self._load(record.run)
        self._ensure_running(current, deadline)
        event = StageStartedEvent(
            run_id=current.run.run_id,
            sequence=current.run.budget.events_used + 1,
            timestamp=datetime.now(timezone.utc),
            payload=StageStartedPayload(stage=stage),
        )
        self._with_store(current, lambda store: store.append_run_event(event))

    def _append_finding(self, record: RunRecord, finding: Finding, *, deadline: float) -> None:
        current = self._load(record.run)
        self._ensure_running(current, deadline)
        event = FindingCreatedEvent(
            run_id=current.run.run_id,
            sequence=current.run.budget.events_used + 1,
            timestamp=datetime.now(timezone.utc),
            payload=FindingCreatedPayload(finding=finding),
        )
        self._with_store(current, lambda store: store.append_run_event(event))

    def _append_graph(self, record: RunRecord, graph: GraphSnapshot, *, deadline: float) -> None:
        current = self._load(record.run)
        self._ensure_running(current, deadline)
        event = GraphUpdatedEvent(
            run_id=current.run.run_id,
            sequence=current.run.budget.events_used + 1,
            timestamp=datetime.now(timezone.utc),
            payload=GraphUpdatedPayload(graph=graph),
        )
        self._with_store(current, lambda store: store.append_run_event(event))

    def _finish_success(
        self,
        run: Run,
        *,
        outcome: str,
        summary: str,
        evidence: list[Any] | tuple[Any, ...],
        symbols: tuple[Any, ...],
        static_edges: list[Any] | tuple[Any, ...],
        candidate_links: list[CandidateLink] | tuple[CandidateLink, ...],
        deadline: float,
        finding_text: str,
        findings: list[Finding] | None = None,
        limitations: list[str] | tuple[str, ...] = (),
    ) -> None:
        current = self._load(run)
        self._ensure_running(current, deadline)
        graph = GraphSnapshot(
            index_id=current.run.index_id,
            nodes=list(symbols),
            static_edges=list(static_edges),
            candidate_links=list(candidate_links),
        )
        self._append_stage(current, "reporting", deadline=deadline)
        self._append_graph(current, graph, deadline=deadline)
        self._finish(
            current.run,
            status="succeeded",
            outcome=outcome,
            summary=summary,
            graph=graph,
            evidence=list(evidence),
            findings=list(findings or []),
            candidate_links=list(candidate_links),
            limitations=list(limitations),
        )

    def _finish_failure(
        self,
        run: Run,
        *,
        code: str,
        message: str,
        evidence: tuple[Any, ...] | list[Any],
        symbols: tuple[Any, ...],
        static_edges: tuple[Any, ...] | list[Any],
        candidate_links: tuple[CandidateLink, ...] | list[CandidateLink],
        limitations: list[str] | tuple[str, ...] = (),
    ) -> None:
        try:
            current = self._load(run)
            finding = Finding(
                finding_id=f"finding-{current.run.run_id}-failure",
                kind="observation",
                text=f"Run stopped before a complete candidate report: {message}",
            )
            self._finish(
                current.run,
                status="failed",
                outcome="failed",
                summary=(
                    "Fixture analysis failed before a complete evidence-backed conclusion."
                    if current.run.mode == "fixture"
                    else "Real model analysis failed before a complete evidence-backed conclusion."
                ),
                graph=GraphSnapshot(
                    index_id=current.run.index_id,
                    nodes=list(symbols),
                    static_edges=list(static_edges),
                    candidate_links=list(candidate_links),
                ),
                evidence=list(evidence),
                findings=[finding],
                candidate_links=list(candidate_links),
                limitations=list(limitations),
                error=RunError(code=code, message=message),
            )
        except StorageConflictError:
            return

    def _finish(
        self,
        run: Run,
        *,
        status: str,
        outcome: str,
        summary: str,
        graph: GraphSnapshot,
        evidence: list[Any],
        findings: list[Finding],
        candidate_links: list[CandidateLink],
        limitations: list[str] | tuple[str, ...] = (),
        error: RunError | None = None,
    ) -> None:
        current = self._load(run)
        if current.run.status not in {"queued", "running"}:
            return
        terminal_sequence = current.run.budget.events_used + 1
        report_id = f"report-{current.run.run_id}"
        report = Report(
            report_id=report_id,
            run_id=current.run.run_id,
            project_id=current.run.project_id,
            session_id=current.run.session_id,
            index_id=current.run.index_id,
            status=status,  # type: ignore[arg-type]
            outcome=outcome,  # type: ignore[arg-type]
            fixture_mode=current.run.mode == "fixture",
            summary=summary,
            graph=graph,
            evidence=evidence,
            findings=findings,
            candidate_links=candidate_links,
            limitations=list(dict.fromkeys(limitations))[:30],
            terminal_sequence=terminal_sequence,
        )
        if status == "succeeded":
            event: Event = RunCompletedEvent(
                run_id=current.run.run_id,
                sequence=terminal_sequence,
                timestamp=datetime.now(timezone.utc),
                payload=RunCompletedPayload(
                    status="succeeded",
                    outcome=outcome,  # type: ignore[arg-type]
                    report_id=report_id,
                ),
            )
        else:
            assert error is not None
            event = RunFailedEvent(
                run_id=current.run.run_id,
                sequence=terminal_sequence,
                timestamp=datetime.now(timezone.utc),
                payload=RunFailedPayload(status="failed", report_id=report_id, error=error),
            )
        updated_run = current.run.model_copy(
            update={
                "status": status,
                "ended_at": event.timestamp,
                "report_id": report_id,
            }
        )
        self._with_store(current, lambda store: store.finish_run(updated_run, event, report))

    def _load(self, run: Run) -> RunRecord:
        project = self.workspace._find_project(run.project_id)
        with self.workspace.registry.store_for_repository(project.repository_id) as store:
            return store._require_run_scope(run.project_id, run.session_id, run.run_id)

    def _with_store(self, record: RunRecord, operation: Callable[[IndexStore], Any]) -> Any:
        project = self.workspace._find_project(record.run.project_id)
        with self.workspace.registry.store_for_repository(project.repository_id) as store:
            return operation(store)

    @staticmethod
    def _ensure_running(record: RunRecord, deadline: float) -> None:
        if record.run.status not in {"queued", "running"}:
            raise StorageConflictError("Run is already terminal")
        if time.monotonic() >= deadline:
            raise StorageInputError("Run time budget is exhausted")


def _choose_target(matches: list[Any], scenario: str | None) -> Any:
    preferred = "load_settings" if scenario in {"success-timeout-missing", "tool-failure-read-source"} else "initialize_client"
    for match in matches:
        if preferred in match.symbol.qualified_name:
            return match.symbol
    return matches[0].symbol


def _is_budget_error(message: str) -> bool:
    normalized = message.casefold()
    return any(token in normalized for token in ("budget", "exhausted", "capacity"))
