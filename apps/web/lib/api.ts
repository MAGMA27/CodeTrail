export const SCHEMA_VERSION = "0.1.0" as const;

export type SymbolKind = "module" | "file" | "class" | "function" | "test";
export type EdgeKind = "contains" | "imports" | "calls";
export type NeighborDirection = "callers" | "callees" | "both";

export type SymbolRecord = {
  symbol_id: string;
  index_id: string;
  path: string;
  qualified_name: string;
  kind: SymbolKind;
  start_line: number;
  end_line: number;
};

export type SourceLocation = {
  path: string;
  start_line: number;
  end_line: number;
};

export type CodeEdge = {
  edge_id: string;
  index_id: string;
  source_id: string;
  target_id: string;
  kind: EdgeKind;
  extraction_method: "ast" | "explicit_import" | "fixture";
  source_location: SourceLocation;
};

export type UnresolvedReference = {
  unresolved_id: string;
  index_id: string;
  source_symbol_id: string;
  path: string;
  expression: string;
  reason: string;
  possible_target_ids: string[];
  start_line: number;
  end_line: number;
};

export type Evidence = {
  evidence_id: string;
  index_id: string;
  path: string;
  start_line: number;
  end_line: number;
  excerpt: string;
  content_hash: string;
  kind: "source" | "stacktrace" | "metadata";
};

export type IndexSnapshot = {
  index_id: string;
  repository_id: string;
  revision: string;
  dirty: boolean;
  snapshot_hash: string;
  status: "ready" | "building" | "failed";
};

export type IndexIssue = {
  path: string;
  kind: string;
  message: string;
  size: number | null;
};

export type IndexResponse = {
  schema_version: typeof SCHEMA_VERSION;
  index: IndexSnapshot;
  issues: IndexIssue[];
  truncated: boolean;
  truncation_reason: string | null;
};

export type ProjectRecord = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  name: string;
  repository_id: string;
  created_at: string;
  available_index_ids: string[];
  latest_index_id: string | null;
  building_index_id: string | null;
};

export type ProjectListResponse = {
  schema_version: typeof SCHEMA_VERSION;
  projects: ProjectRecord[];
};

export type ImportStatus =
  | "queued"
  | "acquiring"
  | "indexing"
  | "ready"
  | "failed"
  | "cancelled"
  | "interrupted";

export type ImportAttempt = {
  schema_version: typeof SCHEMA_VERSION;
  attempt_id: string;
  project_id: string;
  repository_id: string;
  source_path: string;
  status: ImportStatus;
  phase: ImportStatus;
  index_id: string | null;
  error_code: string | null;
  error_message: string | null;
  source_kind: "local" | "github";
  source_url: string | null;
  requested_ref: string | null;
  resolved_commit: string | null;
  content_hash: string | null;
  cleanup_status: "not_required" | "pending" | "succeeded" | "failed";
  cleanup_error: string | null;
  created_at: string;
  updated_at: string;
};

export type LocalImportResponse = {
  schema_version: typeof SCHEMA_VERSION;
  source_path: string;
  project: ProjectRecord;
  attempt: ImportAttempt;
  index: IndexResponse | null;
};

export type GitHubImportResponse = {
  schema_version: typeof SCHEMA_VERSION;
  source_url: string;
  requested_ref: string | null;
  resolved_commit: string | null;
  content_hash: string | null;
  cleanup_status: "not_required" | "pending" | "succeeded" | "failed";
  cleanup_error: string | null;
  project: ProjectRecord;
  attempt: ImportAttempt;
  index: IndexResponse | null;
};

export type ImportAttemptResponse = {
  schema_version: typeof SCHEMA_VERSION;
  source_path: string;
  project: ProjectRecord;
  attempt: ImportAttempt;
  latest_index: IndexResponse | null;
};

export type ProjectIndexesResponse = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  indexes: IndexSnapshot[];
  latest_index_id: string | null;
  building_index_id: string | null;
};

export type SessionMessageSource = "human" | "agent";

export type Session = {
  schema_version: typeof SCHEMA_VERSION;
  session_id: string;
  project_id: string;
  index: IndexSnapshot;
  feature_generation_id: string | null;
  topic: string;
  created_at: string;
  updated_at: string;
  message_count: number;
};

export type SessionMessage = {
  schema_version: typeof SCHEMA_VERSION;
  message_id: string;
  session_id: string;
  sequence: number;
  source: SessionMessageSource;
  content: string;
  created_at: string;
};

export type SessionResponse = {
  schema_version: typeof SCHEMA_VERSION;
  session: Session;
};

export type SessionListResponse = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  sessions: Session[];
  next_cursor: string | null;
  has_more: boolean;
};

export type SessionMessagesResponse = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  session_id: string;
  messages: SessionMessage[];
  next_cursor: string | null;
  has_more: boolean;
};

export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
export type RunTerminalStatus = "succeeded" | "failed" | "cancelled" | "interrupted";
export type RunMode = "fixture" | "real";
export type RunScenario =
  | "success-timeout-missing"
  | "insufficient-external-service"
  | "tool-failure-read-source";

export type RunBudget = {
  max_tool_calls: number;
  max_model_calls: number;
  max_repair_calls: number;
  max_seconds: number;
  max_source_lines: number;
  max_total_source_lines: number;
  max_input_chars: number;
  max_model_input_chars: number;
  max_output_chars: number;
  max_model_output_chars: number;
  max_events: number;
  tool_calls_used: number;
  model_calls_used: number;
  repair_calls_used: number;
  source_lines_used: number;
  input_chars_used: number;
  model_input_chars_used: number;
  output_chars_used: number;
  model_output_chars_used: number;
  model_input_tokens_used: number | null;
  model_output_tokens_used: number | null;
  events_used: number;
};

export type RunInput = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  session_id: string;
  index_id: string;
  feature_generation_id: string | null;
  message_end_sequence: number;
  messages: SessionMessage[];
  input_hash: string;
  input_chars: number;
  frozen_at: string;
};

export type RunRecord = {
  schema_version: typeof SCHEMA_VERSION;
  run_id: string;
  project_id: string;
  session_id: string;
  index_id: string;
  feature_generation_id: string | null;
  issue: { schema_version: typeof SCHEMA_VERSION; id: string; title: string; body: string; stacktrace: string | null; mentioned_paths: string[] };
  mode: RunMode;
  fixture_scenario: RunScenario | null;
  model_provider: string | null;
  model: string | null;
  prompt_version: string | null;
  execution_version: string;
  status: RunStatus;
  budget: RunBudget;
  message_end_sequence: number;
  input_hash: string;
  input_chars: number;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  report_id: string | null;
};

export type RunStatusResponse = {
  schema_version: typeof SCHEMA_VERSION;
  run: RunRecord;
  input: RunInput;
};

export type CreateRunResponse = {
  schema_version: typeof SCHEMA_VERSION;
  run_id: string;
  project_id: string;
  session_id: string;
  index_id: string;
  status: "queued";
};

export type RunListResponse = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  session_id: string;
  runs: RunRecord[];
  next_cursor: string | null;
  has_more: boolean;
};

export type RunEvent = {
  run_id: string;
  sequence: number;
  type: string;
  timestamp: string;
  payload: Record<string, unknown>;
};

export type RunEventsResponse = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  session_id: string;
  run_id: string;
  events: RunEvent[];
  next_after_sequence: number | null;
  has_more: boolean;
};

export type RunStreamEnd = {
  schema_version: typeof SCHEMA_VERSION;
  run_id: string;
  sequence: number;
  reason: "terminal";
  status: RunTerminalStatus;
};

export type RunToolResult = {
  request_id: string;
  run_id: string;
  index_id: string;
  completed_sequence: number;
  kind: string;
  tool: string;
  [key: string]: unknown;
};

export type RunToolResultsResponse = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  session_id: string;
  run_id: string;
  results: RunToolResult[];
  next_after_sequence: number | null;
  has_more: boolean;
};

export type RunCandidateLink = {
  candidate_id: string;
  index_id: string;
  source_symbol_id: string;
  target_symbol_id: string | null;
  relation: string;
  evidence_ids: string[];
};

export type RunFinding = {
  finding_id: string;
  kind: "observation" | "hypothesis" | "suggestion";
  text: string;
  symbol_ids: string[];
  evidence_ids: string[];
};

export type RunReport = {
  schema_version: typeof SCHEMA_VERSION;
  report_id: string;
  run_id: string;
  project_id: string;
  session_id: string;
  index_id: string;
  status: "succeeded" | "failed" | "cancelled" | "interrupted";
  outcome: "candidate_found" | "insufficient_evidence" | "failed" | "cancelled" | "interrupted";
  fixture_mode: boolean;
  summary: string;
  graph: {
    index_id: string;
    nodes: SymbolRecord[];
    static_edges: CodeEdge[];
    unresolved: UnresolvedReference[];
    candidate_links: RunCandidateLink[];
    truncated: boolean;
    truncation_reason: string | null;
  };
  evidence: Evidence[];
  findings: RunFinding[];
  candidate_links: RunCandidateLink[];
  terminal_sequence: number;
};

export type RunReportResponse = {
  schema_version: typeof SCHEMA_VERSION;
  report: RunReport;
};

export type ImpactDirection = "callers" | "callees";
export type ImpactMappingStatus = "mapped" | "unmapped";

export type ImpactPath = {
  path_id: string;
  index_id: string;
  symbol_ids: string[];
  edges: CodeEdge[];
  depth: number;
};

export type RunImpactResponse = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  session_id: string;
  run_id: string;
  index_id: string;
  candidate_id: string | null;
  finding_id: string | null;
  mapping_status: ImpactMappingStatus;
  direction: ImpactDirection;
  max_depth: number;
  root_symbol_id: string | null;
  root: SymbolRecord | null;
  nodes: SymbolRecord[];
  edges: CodeEdge[];
  paths: ImpactPath[];
  unresolved: UnresolvedReference[];
  reachable_node_count: number;
  returned_node_count: number;
  scope_note: string;
  truncated: boolean;
  truncation_reason: string | null;
};

export type CancelRunResponse = {
  schema_version: typeof SCHEMA_VERSION;
  run_id: string;
  project_id: string;
  session_id: string;
  index_id: string;
  status: RunStatus;
  cancel_event_sequence: number | null;
};

export type CreateSessionInput = {
  topic: string;
  indexId?: string;
  featureGenerationId?: string;
  idempotencyKey?: string;
};

export type CreateRunInput = {
  indexId: string;
  issueId: string;
  title: string;
  body: string;
  mode: RunMode;
  scenario?: RunScenario;
  messageEndSequence?: number;
  featureGenerationId?: string;
  idempotencyKey?: string;
};

export type SearchMatch = {
  rank: number;
  symbol: SymbolRecord;
  matched_terms: string[];
};

export type SearchResponse = {
  schema_version: typeof SCHEMA_VERSION;
  index_id: string;
  matches: SearchMatch[];
  truncated: boolean;
  truncation_reason: string | null;
};

export type Neighbor = {
  symbol: SymbolRecord;
  edge: CodeEdge;
};

export type NeighborsResponse = {
  schema_version: typeof SCHEMA_VERSION;
  index_id: string;
  root: SymbolRecord;
  nodes: SymbolRecord[];
  neighbors: Neighbor[];
  unresolved: UnresolvedReference[];
  truncated: boolean;
  truncation_reason: string | null;
};

export type OverviewEntryPoint = {
  start_line: number;
  end_line: number;
  entry_point_id: string;
  index_id: string;
  path: string;
  kind: "python_main_module" | "python_main_guard";
  rule_id: string;
  label: string;
  symbol_id: string | null;
  evidence: Evidence | null;
  evidence_error: string | null;
  direct_symbol_ids: string[];
  unresolved_ids: string[];
};

export type OverviewModule = {
  module_id: string;
  index_id: string;
  symbol: SymbolRecord;
  entry_point_ids: string[];
  entry_reachable: boolean;
};

export type OverviewPath = {
  path_id: string;
  index_id: string;
  entry_point_id: string;
  symbol_ids: string[];
  edges: CodeEdge[];
  depth: number;
};

export type OverviewCoverage = {
  total_modules: number;
  visible_modules: number;
  reachable_modules: number;
  unreachable_modules: number;
  entry_point_count: number;
  unresolved_count: number;
};

export type ProjectOverview = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  index: IndexSnapshot;
  rules_version: string;
  entry_status: "identified" | "not_identified" | "partial";
  entries: OverviewEntryPoint[];
  modules: OverviewModule[];
  paths: OverviewPath[];
  unresolved: UnresolvedReference[];
  coverage: OverviewCoverage;
  notes: string[];
  truncated: boolean;
  truncation_reason: string | null;
};

export type FeatureGenerationMode = "fixture" | "real";
export type FeatureSourceScope = "entry_reachable" | "visible_modules";
export type FeatureGenerationStatus = "succeeded" | "partial" | "insufficient_evidence" | "failed";

export type FeatureGenerationBudget = {
  max_tool_calls: number;
  max_model_calls: number;
  max_seconds: number;
  max_source_lines: number;
  max_total_source_lines: number;
  max_input_chars: number;
  max_features: number;
  max_output_chars: number;
};

export type FeatureGenerationRequest = {
  mode: FeatureGenerationMode;
  model?: string;
  prompt_version?: string;
  source_scope?: FeatureSourceScope;
  budget?: Partial<FeatureGenerationBudget>;
  idempotency_key?: string;
};

export type Feature = {
  feature_id: string;
  index_id: string;
  name: string;
  description: string;
  entry_point_ids: string[];
  module_ids: string[];
  symbol_ids: string[];
  evidence_ids: string[];
  limitations: string[];
};

export type FeatureAssociation = {
  association_id: string;
  index_id: string;
  feature_id: string;
  relation: "triggered_by" | "implemented_by" | "related_to";
  target_kind: "entry_point" | "module" | "symbol";
  target_id: string;
  evidence_ids: string[];
};

export type FeatureGenerationError = {
  kind: string;
  message: string;
  retryable: boolean;
};

export type FeatureGenerationUsage = {
  tool_calls: number;
  model_calls: number;
  repair_calls: number;
  source_lines: number;
  input_chars: number;
  output_chars: number;
  model_input_chars: number[];
  model_output_chars: number[];
  input_tokens: number | null;
  output_tokens: number | null;
  elapsed_ms: number;
};

export type FeatureGeneration = {
  schema_version: typeof SCHEMA_VERSION;
  generation_id: string;
  project_id: string;
  index: IndexSnapshot;
  mode: FeatureGenerationMode;
  model: string | null;
  prompt_version: string;
  rules_version: string;
  source_scope: FeatureSourceScope;
  status: FeatureGenerationStatus;
  summary: string;
  features: Feature[];
  associations: FeatureAssociation[];
  evidence: Evidence[];
  observed_entry_point_ids: string[];
  observed_module_ids: string[];
  observed_symbol_ids: string[];
  source_paths: string[];
  omitted_module_ids: string[];
  limitations: string[];
  usage: FeatureGenerationUsage;
  budget: FeatureGenerationBudget;
  cache_key: string;
  created_at: string;
  error: FeatureGenerationError | null;
  truncated: boolean;
  truncation_reason: string | null;
};

export type FeatureGenerationResponse = {
  schema_version: typeof SCHEMA_VERSION;
  generation: FeatureGeneration;
};

export type FeatureGenerationSummary = {
  schema_version: typeof SCHEMA_VERSION;
  generation_id: string;
  project_id: string;
  index: IndexSnapshot;
  mode: FeatureGenerationMode;
  model: string | null;
  prompt_version: string;
  rules_version: string;
  source_scope: FeatureSourceScope;
  status: FeatureGenerationStatus;
  summary: string;
  feature_count: number;
  evidence_count: number;
  usage: FeatureGenerationUsage;
  created_at: string;
  error: FeatureGenerationError | null;
  truncated: boolean;
  truncation_reason: string | null;
};

export type FeatureGenerationListResponse = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  index_id: string | null;
  generations: FeatureGenerationSummary[];
  next_cursor: string | null;
  has_more: boolean;
};

export type FeatureGraphNodeKind = "entry_point" | "feature" | "module" | "symbol";
export type FeatureGraphOrigin = "semantic" | "static";
export type FeatureGraphRelation =
  | "triggered_by"
  | "implemented_by"
  | "related_to"
  | "contains"
  | "imports"
  | "calls";

export type FeatureGraphNode = {
  node_id: string;
  index_id: string;
  kind: FeatureGraphNodeKind;
  label: string;
  detail: string | null;
  feature_id: string | null;
  entry_point_id: string | null;
  symbol: SymbolRecord | null;
  path: string | null;
  start_line: number | null;
  end_line: number | null;
  evidence_ids: string[];
};

export type FeatureGraphEdge = {
  edge_id: string;
  index_id: string;
  source_id: string;
  target_id: string;
  origin: FeatureGraphOrigin;
  relation: FeatureGraphRelation;
  evidence_ids: string[];
};

export type FeatureGraph = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  index_id: string;
  generation_id: string;
  status: FeatureGenerationStatus;
  nodes: FeatureGraphNode[];
  edges: FeatureGraphEdge[];
  evidence: Evidence[];
  unavailable_target_ids: string[];
  limitations: string[];
  truncated: boolean;
  truncation_reason: string | null;
};

export type FeatureGraphResponse = {
  schema_version: typeof SCHEMA_VERSION;
  graph: FeatureGraph;
};

export type SourceResponse = {
  schema_version: typeof SCHEMA_VERSION;
  index_id: string;
  evidence: Evidence;
};

export type ApiErrorResponse = {
  schema_version: typeof SCHEMA_VERSION;
  code: string;
  message: string;
};

export type HealthResponse = {
  status: "ok";
  service: "codetrail-api";
  version: string;
};

export class ApiRequestError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, status: number, code: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
  }
}

const apiBaseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isPositiveInteger(value: unknown): value is number {
  return isNumber(value) && Number.isInteger(value) && value > 0;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(isString);
}

function isLineRange(value: Record<string, unknown>): boolean {
  return isNumber(value.start_line) && isNumber(value.end_line);
}

function isTruncation(value: Record<string, unknown>): boolean {
  return (
    typeof value.truncated === "boolean" &&
    (value.truncation_reason === null || isString(value.truncation_reason))
  );
}

export function isSymbolRecord(value: unknown): value is SymbolRecord {
  if (!isRecord(value) || !isLineRange(value)) {
    return false;
  }
  return (
    isString(value.symbol_id) &&
    isString(value.index_id) &&
    isString(value.path) &&
    isString(value.qualified_name) &&
    ["module", "file", "class", "function", "test"].includes(String(value.kind))
  );
}

function isSourceLocation(value: unknown): value is SourceLocation {
  return isRecord(value) && isString(value.path) && isLineRange(value);
}

function isEvidence(value: unknown): value is Evidence {
  return (
    isRecord(value) &&
    isString(value.evidence_id) &&
    isString(value.index_id) &&
    isString(value.path) &&
    isLineRange(value) &&
    isString(value.excerpt) &&
    isString(value.content_hash) &&
    ["source", "stacktrace", "metadata"].includes(String(value.kind))
  );
}

export function isCodeEdge(value: unknown): value is CodeEdge {
  return (
    isRecord(value) &&
    isString(value.edge_id) &&
    isString(value.index_id) &&
    isString(value.source_id) &&
    isString(value.target_id) &&
    ["contains", "imports", "calls"].includes(String(value.kind)) &&
    ["ast", "explicit_import", "fixture"].includes(String(value.extraction_method)) &&
    isSourceLocation(value.source_location)
  );
}

function isUnresolvedReference(value: unknown): value is UnresolvedReference {
  return (
    isRecord(value) &&
    isLineRange(value) &&
    isString(value.unresolved_id) &&
    isString(value.index_id) &&
    isString(value.source_symbol_id) &&
    isString(value.path) &&
    isString(value.expression) &&
    isString(value.reason) &&
    isStringArray(value.possible_target_ids)
  );
}

function isIndexSnapshot(value: unknown): value is IndexSnapshot {
  return (
    isRecord(value) &&
    isString(value.index_id) &&
    isString(value.repository_id) &&
    isString(value.revision) &&
    typeof value.dirty === "boolean" &&
    isString(value.snapshot_hash) &&
    ["ready", "building", "failed"].includes(String(value.status))
  );
}

function isNullableString(value: unknown): value is string | null {
  return value === null || isString(value);
}

function isImportStatus(value: unknown): value is ImportStatus {
  return ["queued", "acquiring", "indexing", "ready", "failed", "cancelled", "interrupted"].includes(
    String(value),
  );
}

export function isProjectRecord(value: unknown): value is ProjectRecord {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.project_id) &&
    isString(value.name) &&
    isString(value.repository_id) &&
    isString(value.created_at) &&
    isStringArray(value.available_index_ids) &&
    isNullableString(value.latest_index_id) &&
    isNullableString(value.building_index_id)
  );
}

export function isProjectListResponse(value: unknown): value is ProjectListResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    Array.isArray(value.projects) &&
    value.projects.every(isProjectRecord)
  );
}

export function isImportAttempt(value: unknown): value is ImportAttempt {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.attempt_id) &&
    isString(value.project_id) &&
    isString(value.repository_id) &&
    isString(value.source_path) &&
    isImportStatus(value.status) &&
    value.phase === value.status &&
    isNullableString(value.index_id) &&
    isNullableString(value.error_code) &&
    isNullableString(value.error_message) &&
    ["local", "github"].includes(String(value.source_kind)) &&
    isNullableString(value.source_url) &&
    isNullableString(value.requested_ref) &&
    isNullableString(value.resolved_commit) &&
    isNullableString(value.content_hash) &&
    ["not_required", "pending", "succeeded", "failed"].includes(String(value.cleanup_status)) &&
    isNullableString(value.cleanup_error) &&
    isString(value.created_at) &&
    isString(value.updated_at)
  );
}

export function isGitHubImportResponse(value: unknown): value is GitHubImportResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.source_url) &&
    isNullableString(value.requested_ref) &&
    isNullableString(value.resolved_commit) &&
    isNullableString(value.content_hash) &&
    ["not_required", "pending", "succeeded", "failed"].includes(String(value.cleanup_status)) &&
    isNullableString(value.cleanup_error) &&
    isProjectRecord(value.project) &&
    isImportAttempt(value.attempt) &&
    (value.index === null || isIndexResponse(value.index))
  );
}

export function isLocalImportResponse(value: unknown): value is LocalImportResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.source_path) &&
    isProjectRecord(value.project) &&
    isImportAttempt(value.attempt) &&
    (value.index === null || isIndexResponse(value.index))
  );
}

export function isImportAttemptResponse(value: unknown): value is ImportAttemptResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.source_path) &&
    isProjectRecord(value.project) &&
    isImportAttempt(value.attempt) &&
    (value.latest_index === null || isIndexResponse(value.latest_index))
  );
}

export function isProjectIndexesResponse(value: unknown): value is ProjectIndexesResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.project_id) &&
    Array.isArray(value.indexes) &&
    value.indexes.every(isIndexSnapshot) &&
    isNullableString(value.latest_index_id) &&
    isNullableString(value.building_index_id)
  );
}

export function isSession(value: unknown): value is Session {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.session_id) &&
    isString(value.project_id) &&
    isIndexSnapshot(value.index) &&
    value.index.status === "ready" &&
    isNullableString(value.feature_generation_id) &&
    isString(value.topic) &&
    isString(value.created_at) &&
    isString(value.updated_at) &&
    isNonNegativeInteger(value.message_count)
  );
}

export function isSessionMessage(value: unknown): value is SessionMessage {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.message_id) &&
    isString(value.session_id) &&
    isPositiveInteger(value.sequence) &&
    ["human", "agent"].includes(String(value.source)) &&
    isString(value.content) &&
    isString(value.created_at)
  );
}

export function isSessionResponse(value: unknown): value is SessionResponse {
  return isRecord(value) && value.schema_version === SCHEMA_VERSION && isSession(value.session);
}

export function isSessionListResponse(value: unknown): value is SessionListResponse {
  if (
    !isRecord(value) ||
    value.schema_version !== SCHEMA_VERSION ||
    !isString(value.project_id) ||
    !Array.isArray(value.sessions) ||
    !value.sessions.every(isSession) ||
    !isNullableString(value.next_cursor) ||
    typeof value.has_more !== "boolean" ||
    value.has_more !== (value.next_cursor !== null)
  ) {
    return false;
  }
  const sessionIds = new Set(value.sessions.map((session) => session.session_id));
  return sessionIds.size === value.sessions.length && value.sessions.every(
    (session) => session.project_id === value.project_id,
  );
}

export function isSessionMessagesResponse(value: unknown): value is SessionMessagesResponse {
  if (
    !isRecord(value) ||
    value.schema_version !== SCHEMA_VERSION ||
    !isString(value.project_id) ||
    !isString(value.session_id) ||
    !Array.isArray(value.messages) ||
    !value.messages.every(isSessionMessage) ||
    !isNullableString(value.next_cursor) ||
    typeof value.has_more !== "boolean" ||
    value.has_more !== (value.next_cursor !== null)
  ) {
    return false;
  }
  const sequences = value.messages.map((message) => message.sequence);
  return (
    new Set(value.messages.map((message) => message.message_id)).size === value.messages.length &&
    value.messages.every((message) => message.session_id === value.session_id) &&
    sequences.every((sequence, index) => index === 0 || sequence > sequences[index - 1])
  );
}

function isRunStatus(value: unknown): value is RunStatus {
  return ["queued", "running", "succeeded", "failed", "cancelled", "interrupted"].includes(String(value));
}

function isRunScenario(value: unknown): value is RunScenario {
  return ["success-timeout-missing", "insufficient-external-service", "tool-failure-read-source"].includes(String(value));
}

function isRunBudget(value: unknown): value is RunBudget {
  if (!isRecord(value)) {
    return false;
  }
  return [
    "max_tool_calls",
    "max_model_calls",
    "max_repair_calls",
    "max_seconds",
    "max_source_lines",
    "max_total_source_lines",
    "max_input_chars",
    "max_model_input_chars",
    "max_output_chars",
    "max_model_output_chars",
    "max_events",
    "tool_calls_used",
    "model_calls_used",
    "repair_calls_used",
    "source_lines_used",
    "input_chars_used",
    "model_input_chars_used",
    "output_chars_used",
    "model_output_chars_used",
    "events_used",
  ].every((key) => isNonNegativeInteger(value[key])) &&
    (value.model_input_tokens_used === null || isNonNegativeInteger(value.model_input_tokens_used)) &&
    (value.model_output_tokens_used === null || isNonNegativeInteger(value.model_output_tokens_used));
}

function isRunRecord(value: unknown): value is RunRecord {
  if (!isRecord(value)) {
    return false;
  }
  if (!isRunBudget(value.budget)) {
    return false;
  }
  const record = value as Record<string, unknown>;
  const issue = record.issue;
  return (
    record.schema_version === SCHEMA_VERSION &&
    isString(record.run_id) &&
    isString(record.project_id) &&
    isString(record.session_id) &&
    isString(record.index_id) &&
    isNullableString(record.feature_generation_id) &&
    isRecord(issue) &&
    issue.schema_version === SCHEMA_VERSION &&
    isString(issue.id) &&
    isString(issue.title) &&
    isString(issue.body) &&
    isNullableString(issue.stacktrace) &&
    isStringArray(issue.mentioned_paths) &&
    ["fixture", "real"].includes(String(record.mode)) &&
    (record.fixture_scenario === null || isRunScenario(record.fixture_scenario)) &&
    isNullableString(record.model_provider) &&
    isNullableString(record.model) &&
    isNullableString(record.prompt_version) &&
    isString(record.execution_version) &&
    isRunStatus(record.status) &&
    isPositiveInteger(record.message_end_sequence) &&
    isString(record.input_hash) &&
    isPositiveInteger(record.input_chars) &&
    isString(record.created_at) &&
    isNullableString(record.started_at) &&
    isNullableString(record.ended_at) &&
    isNullableString(record.report_id)
  );
}


function isRunInput(value: unknown): value is RunInput {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.project_id) &&
    isString(value.session_id) &&
    isString(value.index_id) &&
    isNullableString(value.feature_generation_id) &&
    isPositiveInteger(value.message_end_sequence) &&
    Array.isArray(value.messages) &&
    value.messages.every(isSessionMessage) &&
    isString(value.input_hash) &&
    isPositiveInteger(value.input_chars) &&
    isString(value.frozen_at)
  );
}

export function isRunEvent(value: unknown): value is RunEvent {
  return (
    isRecord(value) &&
    isString(value.run_id) &&
    isPositiveInteger(value.sequence) &&
    isString(value.type) &&
    isString(value.timestamp) &&
    isRecord(value.payload)
  );
}

export function isRunStreamEnd(value: unknown): value is RunStreamEnd {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.run_id) &&
    isNumber(value.sequence) &&
    Number.isInteger(value.sequence) &&
    value.sequence >= 0 &&
    value.reason === "terminal" &&
    ["succeeded", "failed", "cancelled", "interrupted"].includes(String(value.status))
  );
}

function isRunToolResult(value: unknown): value is RunToolResult {
  return (
    isRecord(value) &&
    isString(value.request_id) &&
    isString(value.run_id) &&
    isString(value.index_id) &&
    isPositiveInteger(value.completed_sequence) &&
    isString(value.kind) &&
    isString(value.tool)
  );
}

export function isRunStatusResponse(value: unknown): value is RunStatusResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isRunRecord(value.run) &&
    isRunInput(value.input) &&
    value.run.project_id === value.input.project_id &&
    value.run.session_id === value.input.session_id &&
    value.run.index_id === value.input.index_id &&
    value.run.message_end_sequence === value.input.message_end_sequence &&
    value.run.input_hash === value.input.input_hash
  );
}

export function isRunListResponse(value: unknown): value is RunListResponse {
  if (
    !isRecord(value) ||
    value.schema_version !== SCHEMA_VERSION ||
    !isString(value.project_id) ||
    !isString(value.session_id) ||
    !Array.isArray(value.runs) ||
    !value.runs.every(isRunRecord) ||
    !isNullableString(value.next_cursor) ||
    typeof value.has_more !== "boolean" ||
    value.has_more !== (value.next_cursor !== null)
  ) {
    return false;
  }
  return value.runs.every(
    (run) => run.project_id === value.project_id && run.session_id === value.session_id,
  );
}

export function isRunEventsResponse(value: unknown): value is RunEventsResponse {
  if (
    !isRecord(value) ||
    value.schema_version !== SCHEMA_VERSION ||
    !isString(value.project_id) ||
    !isString(value.session_id) ||
    !isString(value.run_id) ||
    !Array.isArray(value.events) ||
    !value.events.every(isRunEvent) ||
    !isNullableNumber(value.next_after_sequence) ||
    typeof value.has_more !== "boolean" ||
    value.has_more !== (value.next_after_sequence !== null)
  ) {
    return false;
  }
  const sequences = value.events.map((event) => event.sequence);
  return (
    value.events.every((event) => event.run_id === value.run_id) &&
    sequences.every((sequence, index) => index === 0 || sequence > sequences[index - 1])
  );
}

export function isRunToolResultsResponse(value: unknown): value is RunToolResultsResponse {
  if (
    !isRecord(value) ||
    value.schema_version !== SCHEMA_VERSION ||
    !isString(value.project_id) ||
    !isString(value.session_id) ||
    !isString(value.run_id) ||
    !Array.isArray(value.results) ||
    !value.results.every(isRunToolResult) ||
    !isNullableNumber(value.next_after_sequence) ||
    typeof value.has_more !== "boolean" ||
    value.has_more !== (value.next_after_sequence !== null)
  ) {
    return false;
  }
  const sequences = value.results.map((result) => result.completed_sequence);
  return (
    value.results.every((result) => result.run_id === value.run_id) &&
    sequences.every((sequence, index) => index === 0 || sequence > sequences[index - 1])
  );
}

function isRunCandidateLink(value: unknown): value is RunCandidateLink {
  return (
    isRecord(value) &&
    isString(value.candidate_id) &&
    isString(value.index_id) &&
    isString(value.source_symbol_id) &&
    isNullableString(value.target_symbol_id) &&
    isString(value.relation) &&
    isStringArray(value.evidence_ids)
  );
}

function isRunFinding(value: unknown): value is RunFinding {
  return (
    isRecord(value) &&
    isString(value.finding_id) &&
    ["observation", "hypothesis", "suggestion"].includes(String(value.kind)) &&
    isString(value.text) &&
    isStringArray(value.symbol_ids) &&
    isStringArray(value.evidence_ids)
  );
}

function isRunReport(value: unknown): value is RunReport {
  if (!isRecord(value) || !isRecord(value.graph)) {
    return false;
  }
  const graph = value.graph;
  return (
    value.schema_version === SCHEMA_VERSION &&
    isString(value.report_id) &&
    isString(value.run_id) &&
    isString(value.project_id) &&
    isString(value.session_id) &&
    isString(value.index_id) &&
    ["succeeded", "failed", "cancelled", "interrupted"].includes(String(value.status)) &&
    ["candidate_found", "insufficient_evidence", "failed", "cancelled", "interrupted"].includes(String(value.outcome)) &&
    typeof value.fixture_mode === "boolean" &&
    isString(value.summary) &&
    isString(graph.index_id) &&
    graph.index_id === value.index_id &&
    Array.isArray(graph.nodes) &&
    graph.nodes.every(isSymbolRecord) &&
    Array.isArray(graph.static_edges) &&
    graph.static_edges.every(isCodeEdge) &&
    Array.isArray(graph.unresolved) &&
    graph.unresolved.every(isUnresolvedReference) &&
    Array.isArray(graph.candidate_links) &&
    graph.candidate_links.every(isRunCandidateLink) &&
    typeof graph.truncated === "boolean" &&
    isNullableString(graph.truncation_reason) &&
    Array.isArray(value.evidence) &&
    value.evidence.every(isEvidence) &&
    Array.isArray(value.findings) &&
    value.findings.every(isRunFinding) &&
    Array.isArray(value.candidate_links) &&
    value.candidate_links.every(isRunCandidateLink) &&
    isPositiveInteger(value.terminal_sequence)
  );
}

export function isRunReportResponse(value: unknown): value is RunReportResponse {
  return isRecord(value) && value.schema_version === SCHEMA_VERSION && isRunReport(value.report);
}

function isImpactPath(
  value: unknown,
  indexId: string,
  direction: ImpactDirection,
  rootSymbolId: string,
  nodeIds: Set<string>,
  edgeIds: Set<string>,
): value is ImpactPath {
  if (!isRecord(value) || !isString(value.path_id) || value.index_id !== indexId) {
    return false;
  }
  const symbolIds = value.symbol_ids;
  const pathEdges = value.edges;
  if (
    !Array.isArray(symbolIds) ||
    !isStringArray(symbolIds) ||
    symbolIds.length < 2 ||
    symbolIds.length > 3 ||
    symbolIds[0] !== rootSymbolId ||
    new Set(symbolIds).size !== symbolIds.length ||
    !Array.isArray(pathEdges) ||
    !pathEdges.every(isCodeEdge) ||
    pathEdges.length !== symbolIds.length - 1 ||
    !isPositiveInteger(value.depth) ||
    value.depth !== pathEdges.length
  ) {
    return false;
  }
  return symbolIds.every((symbolId) => nodeIds.has(symbolId)) && pathEdges.every((edge, index) => {
    if (!edgeIds.has(edge.edge_id) || edge.index_id !== indexId || edge.kind !== "calls") {
      return false;
    }
    const sourceId = direction === "callers" ? symbolIds[index + 1] : symbolIds[index];
    const targetId = direction === "callers" ? symbolIds[index] : symbolIds[index + 1];
    return edge.source_id === sourceId && edge.target_id === targetId;
  });
}

function isRunImpact(value: unknown): value is RunImpactResponse {
  if (!isRecord(value)) {
    return false;
  }
  const nodes = value.nodes;
  const edges = value.edges;
  const paths = value.paths;
  const unresolved = value.unresolved;
  if (
    value.schema_version !== SCHEMA_VERSION ||
    !isString(value.project_id) ||
    !isString(value.session_id) ||
    !isString(value.run_id) ||
    !isString(value.index_id) ||
    !isNullableString(value.candidate_id) ||
    !isNullableString(value.finding_id) ||
    (value.candidate_id === null) === (value.finding_id === null) ||
    !["mapped", "unmapped"].includes(String(value.mapping_status)) ||
    !["callers", "callees"].includes(String(value.direction)) ||
    !isPositiveInteger(value.max_depth) ||
    value.max_depth > 2 ||
    !isNullableString(value.root_symbol_id) ||
    !(value.root === null || isSymbolRecord(value.root)) ||
    !Array.isArray(nodes) ||
    !nodes.every(isSymbolRecord) ||
    !Array.isArray(edges) ||
    !edges.every(isCodeEdge) ||
    !Array.isArray(paths) ||
    !Array.isArray(unresolved) ||
    !unresolved.every(isUnresolvedReference) ||
    !isNonNegativeInteger(value.reachable_node_count) ||
    !isNonNegativeInteger(value.returned_node_count) ||
    value.returned_node_count !== nodes.length ||
    value.reachable_node_count !== Math.max(0, nodes.length - 1) ||
    !isString(value.scope_note) ||
    !isTruncation(value)
  ) {
    return false;
  }
  const nodeIds = new Set(nodes.map((node) => node.symbol_id));
  if (nodeIds.size !== nodes.length || nodes.some((node) => node.index_id !== value.index_id)) {
    return false;
  }
  if (value.mapping_status === "mapped") {
    if (
      value.root === null ||
      value.root_symbol_id === null ||
      value.root.symbol_id !== value.root_symbol_id ||
      value.root.index_id !== value.index_id ||
      !nodeIds.has(value.root_symbol_id)
    ) {
      return false;
    }
  } else if (
    value.root !== null ||
    value.root_symbol_id !== null ||
    nodes.length > 0 ||
    edges.length > 0 ||
    paths.length > 0 ||
    unresolved.length > 0 ||
    value.reachable_node_count !== 0
  ) {
    return false;
  }
  const edgeIds = new Set<string>();
  if (edges.some((edge) => {
    if (edgeIds.has(edge.edge_id)) {
      return true;
    }
    edgeIds.add(edge.edge_id);
    return edge.index_id !== value.index_id ||
      edge.kind !== "calls" ||
      !nodeIds.has(edge.source_id) ||
      !nodeIds.has(edge.target_id);
  })) {
    return false;
  }
  if (value.mapping_status === "unmapped" || value.root_symbol_id === null) {
    return paths.length === 0;
  }
  const indexId = value.index_id as string;
  const direction = value.direction as ImpactDirection;
  const rootSymbolId = value.root_symbol_id as string;
  return paths.every((path) => isImpactPath(
    path,
    indexId,
    direction,
    rootSymbolId,
    nodeIds,
    edgeIds,
  ));
}

export function isRunImpactResponse(value: unknown): value is RunImpactResponse {
  return isRecord(value) && isRunImpact(value);
}

export function isCreateRunResponse(value: unknown): value is CreateRunResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.run_id) &&
    isString(value.project_id) &&
    isString(value.session_id) &&
    isString(value.index_id) &&
    value.status === "queued"
  );
}

export function isCancelRunResponse(value: unknown): value is CancelRunResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.run_id) &&
    isString(value.project_id) &&
    isString(value.session_id) &&
    isString(value.index_id) &&
    isRunStatus(value.status) &&
    (value.cancel_event_sequence === null || isPositiveInteger(value.cancel_event_sequence))
  );
}

export function isIndexResponse(value: unknown): value is IndexResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isIndexSnapshot(value.index) &&
    Array.isArray(value.issues) &&
    value.issues.every(isRecord) &&
    value.issues.every(
      (issue) =>
        isString(issue.path) &&
        isString(issue.kind) &&
        isString(issue.message) &&
        (issue.size === null || isNumber(issue.size)),
    ) &&
    isTruncation(value)
  );
}

function isSearchMatch(value: unknown): value is SearchMatch {
  return (
    isRecord(value) &&
    isNumber(value.rank) &&
    isSymbolRecord(value.symbol) &&
    isStringArray(value.matched_terms)
  );
}

export function isSearchResponse(value: unknown): value is SearchResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.index_id) &&
    Array.isArray(value.matches) &&
    value.matches.every(isSearchMatch) &&
    isTruncation(value)
  );
}

function isNeighbor(value: unknown): value is Neighbor {
  return isRecord(value) && isSymbolRecord(value.symbol) && isCodeEdge(value.edge);
}

export function isNeighborsResponse(value: unknown): value is NeighborsResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.index_id) &&
    isSymbolRecord(value.root) &&
    Array.isArray(value.nodes) &&
    value.nodes.every(isSymbolRecord) &&
    Array.isArray(value.neighbors) &&
    value.neighbors.every(isNeighbor) &&
    Array.isArray(value.unresolved) &&
    value.unresolved.every(isUnresolvedReference) &&
    isTruncation(value)
  );
}

function isOverviewEntryPoint(value: unknown): value is OverviewEntryPoint {
  return (
    isRecord(value) &&
    isLineRange(value) &&
    isString(value.entry_point_id) &&
    isString(value.index_id) &&
    isString(value.path) &&
    ["python_main_module", "python_main_guard"].includes(String(value.kind)) &&
    isString(value.rule_id) &&
    isString(value.label) &&
    (value.symbol_id === null || isString(value.symbol_id)) &&
    (value.evidence === null || isEvidence(value.evidence)) &&
    (value.evidence_error === null || isString(value.evidence_error)) &&
    isStringArray(value.direct_symbol_ids) &&
    isStringArray(value.unresolved_ids)
  );
}

function isOverviewModule(value: unknown): value is OverviewModule {
  return (
    isRecord(value) &&
    isString(value.module_id) &&
    isString(value.index_id) &&
    isSymbolRecord(value.symbol) &&
    value.module_id === value.symbol.symbol_id &&
    value.index_id === value.symbol.index_id &&
    value.symbol.kind === "module" &&
    isStringArray(value.entry_point_ids) &&
    typeof value.entry_reachable === "boolean"
  );
}

function isOverviewPath(value: unknown): value is OverviewPath {
  return (
    isRecord(value) &&
    isString(value.path_id) &&
    isString(value.index_id) &&
    isString(value.entry_point_id) &&
    isStringArray(value.symbol_ids) &&
    Array.isArray(value.edges) &&
    value.edges.every(isCodeEdge) &&
    isNumber(value.depth) &&
    value.depth === value.edges.length &&
    value.symbol_ids.length === value.edges.length + 1
  );
}

function isOverviewCoverage(value: unknown): value is OverviewCoverage {
  return (
    isRecord(value) &&
    isNumber(value.total_modules) &&
    isNumber(value.visible_modules) &&
    isNumber(value.reachable_modules) &&
    isNumber(value.unreachable_modules) &&
    isNumber(value.entry_point_count) &&
    isNumber(value.unresolved_count)
  );
}

export function isProjectOverviewResponse(value: unknown): value is ProjectOverview {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.project_id) &&
    isIndexSnapshot(value.index) &&
    isString(value.rules_version) &&
    ["identified", "not_identified", "partial"].includes(String(value.entry_status)) &&
    Array.isArray(value.entries) &&
    value.entries.every(isOverviewEntryPoint) &&
    Array.isArray(value.modules) &&
    value.modules.every(isOverviewModule) &&
    Array.isArray(value.paths) &&
    value.paths.every(isOverviewPath) &&
    Array.isArray(value.unresolved) &&
    value.unresolved.every(isUnresolvedReference) &&
    isOverviewCoverage(value.coverage) &&
    isStringArray(value.notes) &&
    isTruncation(value)
  );
}

function isFeature(value: unknown, indexId: string): value is Feature {
  return (
    isRecord(value) &&
    isString(value.feature_id) &&
    value.index_id === indexId &&
    isString(value.name) &&
    isString(value.description) &&
    isStringArray(value.entry_point_ids) &&
    isStringArray(value.module_ids) &&
    isStringArray(value.symbol_ids) &&
    isStringArray(value.evidence_ids) &&
    isStringArray(value.limitations)
  );
}

function isFeatureAssociation(value: unknown, indexId: string): value is FeatureAssociation {
  return (
    isRecord(value) &&
    isString(value.association_id) &&
    value.index_id === indexId &&
    isString(value.feature_id) &&
    ["triggered_by", "implemented_by", "related_to"].includes(String(value.relation)) &&
    ["entry_point", "module", "symbol"].includes(String(value.target_kind)) &&
    isString(value.target_id) &&
    isStringArray(value.evidence_ids)
  );
}

function isNonNegativeInteger(value: unknown): value is number {
  return isNumber(value) && Number.isInteger(value) && value >= 0;
}

function isFeatureGeneration(value: unknown): value is FeatureGeneration {
  if (!isRecord(value) || !isIndexSnapshot(value.index)) {
    return false;
  }
  const indexId = value.index.index_id;
  if (
    value.schema_version !== SCHEMA_VERSION ||
    !isString(value.generation_id) ||
    !isString(value.project_id) ||
    !["fixture", "real"].includes(String(value.mode)) ||
    !isNullableString(value.model) ||
    !isString(value.prompt_version) ||
    !isString(value.rules_version) ||
    !["entry_reachable", "visible_modules"].includes(String(value.source_scope)) ||
    !["succeeded", "partial", "insufficient_evidence", "failed"].includes(String(value.status)) ||
    !isString(value.summary) ||
    !Array.isArray(value.features) ||
    !value.features.every((item) => isFeature(item, indexId)) ||
    !Array.isArray(value.associations) ||
    !value.associations.every((item) => isFeatureAssociation(item, indexId)) ||
    !Array.isArray(value.evidence) ||
    !value.evidence.every((item) => isEvidence(item) && item.index_id === indexId) ||
    !isStringArray(value.observed_entry_point_ids) ||
    !isStringArray(value.observed_module_ids) ||
    !isStringArray(value.observed_symbol_ids) ||
    !isStringArray(value.source_paths) ||
    !isStringArray(value.omitted_module_ids) ||
    !isStringArray(value.limitations) ||
    !isRecord(value.usage) ||
    !isRecord(value.budget) ||
    !isString(value.cache_key) ||
    !isString(value.created_at) ||
    !isTruncation(value)
  ) {
    return false;
  }
  const usageKeys = [
    "tool_calls",
    "model_calls",
    "repair_calls",
    "source_lines",
    "input_chars",
    "output_chars",
    "elapsed_ms",
  ];
  if (!usageKeys.every((key) => isNonNegativeInteger((value.usage as Record<string, unknown>)[key]))) {
    return false;
  }
  const usage = value.usage as FeatureGenerationUsage;
  const modelInputChars = usage.model_input_chars;
  const modelOutputChars = usage.model_output_chars;
  if (
    !Array.isArray(modelInputChars) ||
    !modelInputChars.every(isNonNegativeInteger) ||
    !Array.isArray(modelOutputChars) ||
    !modelOutputChars.every(isNonNegativeInteger) ||
    modelInputChars.length !== usage.model_calls ||
    modelOutputChars.length > usage.model_calls ||
    modelInputChars.reduce((total, chars) => total + chars, 0) !== usage.input_chars ||
    modelOutputChars.reduce((total, chars) => total + chars, 0) !== usage.output_chars
  ) {
    return false;
  }
  if (
    !isNullableNumber((value.usage as Record<string, unknown>).input_tokens) ||
    !isNullableNumber((value.usage as Record<string, unknown>).output_tokens) ||
    !isNonNegativeInteger((value.budget as Record<string, unknown>).max_tool_calls) ||
    !isNonNegativeInteger((value.budget as Record<string, unknown>).max_model_calls) ||
    !isNonNegativeInteger((value.budget as Record<string, unknown>).max_seconds) ||
    !isNonNegativeInteger((value.budget as Record<string, unknown>).max_source_lines) ||
    !isNonNegativeInteger((value.budget as Record<string, unknown>).max_total_source_lines) ||
    !isNonNegativeInteger((value.budget as Record<string, unknown>).max_input_chars) ||
    !isNonNegativeInteger((value.budget as Record<string, unknown>).max_features) ||
    !isNonNegativeInteger((value.budget as Record<string, unknown>).max_output_chars) ||
    (value.error !== null &&
      (!isRecord(value.error) ||
        !isString(value.error.kind) ||
        !isString(value.error.message) ||
        typeof value.error.retryable !== "boolean"))
  ) {
    return false;
  }
  const evidenceIds = new Set(value.evidence.map((item) => item.evidence_id));
  const featureIds = new Set(value.features.map((item) => item.feature_id));
  if (evidenceIds.size !== value.evidence.length || featureIds.size !== value.features.length) {
    return false;
  }
  if (
    value.features.some(
      (feature) =>
        feature.evidence_ids.some((evidenceId) => !evidenceIds.has(evidenceId)) ||
        feature.index_id !== indexId,
    )
  ) {
    return false;
  }
  return value.associations.every(
    (association) =>
      featureIds.has(association.feature_id) &&
      association.evidence_ids.every((evidenceId) => evidenceIds.has(evidenceId)),
  );
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || (isNumber(value) && Number.isInteger(value) && value >= 0);
}

export function isFeatureGenerationResponse(value: unknown): value is FeatureGenerationResponse {
  return isRecord(value) && value.schema_version === SCHEMA_VERSION && isFeatureGeneration(value.generation);
}

function isFeatureGenerationUsage(value: unknown): value is FeatureGenerationUsage {
  if (!isRecord(value)) {
    return false;
  }
  const nonNegativeKeys = [
    "tool_calls",
    "model_calls",
    "repair_calls",
    "source_lines",
    "input_chars",
    "output_chars",
    "elapsed_ms",
  ];
  if (!nonNegativeKeys.every((key) => isNonNegativeInteger(value[key]))) {
    return false;
  }
  if (
    !Array.isArray(value.model_input_chars) ||
    !value.model_input_chars.every(isNonNegativeInteger) ||
    !Array.isArray(value.model_output_chars) ||
    !value.model_output_chars.every(isNonNegativeInteger) ||
    value.model_input_chars.length !== value.model_calls ||
    value.model_output_chars.length > value.model_calls ||
    value.model_input_chars.reduce((total, chars) => total + chars, 0) !== value.input_chars ||
    value.model_output_chars.reduce((total, chars) => total + chars, 0) !== value.output_chars ||
    !isNullableNumber(value.input_tokens) ||
    !isNullableNumber(value.output_tokens)
  ) {
    return false;
  }
  return true;
}

function isFeatureGenerationError(value: unknown): value is FeatureGenerationError {
  return (
    isRecord(value) &&
    isString(value.kind) &&
    isString(value.message) &&
    typeof value.retryable === "boolean"
  );
}

export function isFeatureGenerationSummary(value: unknown): value is FeatureGenerationSummary {
  if (
    !isRecord(value) ||
    value.schema_version !== SCHEMA_VERSION ||
    !isString(value.generation_id) ||
    !isString(value.project_id) ||
    !isIndexSnapshot(value.index) ||
    value.index.status !== "ready" ||
    !["fixture", "real"].includes(String(value.mode)) ||
    !isNullableString(value.model) ||
    !isString(value.prompt_version) ||
    !isString(value.rules_version) ||
    !["entry_reachable", "visible_modules"].includes(String(value.source_scope)) ||
    !["succeeded", "partial", "insufficient_evidence", "failed"].includes(String(value.status)) ||
    !isString(value.summary) ||
    !isNonNegativeInteger(value.feature_count) ||
    !isNonNegativeInteger(value.evidence_count) ||
    !isFeatureGenerationUsage(value.usage) ||
    !isString(value.created_at) ||
    !isTruncation(value)
  ) {
    return false;
  }
  if (value.error !== null && !isFeatureGenerationError(value.error)) {
    return false;
  }
  if (value.status === "failed" ? value.error === null : value.error !== null) {
    return false;
  }
  return true;
}

export function isFeatureGenerationListResponse(value: unknown): value is FeatureGenerationListResponse {
  if (
    !isRecord(value) ||
    value.schema_version !== SCHEMA_VERSION ||
    !isString(value.project_id) ||
    !isNullableString(value.index_id) ||
    !Array.isArray(value.generations) ||
    !value.generations.every(isFeatureGenerationSummary) ||
    !isNullableString(value.next_cursor) ||
    typeof value.has_more !== "boolean"
  ) {
    return false;
  }
  if (value.has_more !== (value.next_cursor !== null)) {
    return false;
  }
  return value.generations.every(
    (generation) =>
      generation.project_id === value.project_id &&
      (value.index_id === null || generation.index.index_id === value.index_id),
  );
}

function isFeatureGraphNode(
  value: unknown,
  indexId: string,
  evidenceIds: Set<string>,
): value is FeatureGraphNode {
  if (
    !isRecord(value) ||
    !isString(value.node_id) ||
    value.index_id !== indexId ||
    !["entry_point", "feature", "module", "symbol"].includes(String(value.kind)) ||
    !isString(value.label) ||
    !isNullableString(value.detail) ||
    !isNullableString(value.feature_id) ||
    !isNullableString(value.entry_point_id) ||
    !(value.symbol === null || isSymbolRecord(value.symbol)) ||
    !isNullableString(value.path) ||
    !(value.start_line === null || isNumber(value.start_line)) ||
    !(value.end_line === null || isNumber(value.end_line)) ||
    !isStringArray(value.evidence_ids) ||
    new Set(value.evidence_ids).size !== value.evidence_ids.length ||
    value.evidence_ids.some((evidenceId) => !evidenceIds.has(evidenceId))
  ) {
    return false;
  }
  if (value.kind === "feature") {
    return (
      isString(value.feature_id) &&
      value.node_id === `feature:${value.feature_id}` &&
      value.entry_point_id === null &&
      value.symbol === null &&
      value.path === null &&
      value.start_line === null &&
      value.end_line === null
    );
  }
  if (value.kind === "entry_point") {
    return (
      isString(value.entry_point_id) &&
      value.node_id === `entry:${value.entry_point_id}` &&
      value.symbol === null &&
      isString(value.path) &&
      isNumber(value.start_line) &&
      isNumber(value.end_line) &&
      value.end_line >= value.start_line &&
      value.feature_id === null
    );
  }
  return (
    value.symbol !== null &&
    value.node_id === value.symbol.symbol_id &&
    value.symbol.index_id === indexId &&
    value.symbol.kind === (value.kind === "module" ? "module" : value.symbol.kind) &&
    (value.kind !== "symbol" || value.symbol.kind !== "module") &&
    value.feature_id === null &&
    value.entry_point_id === null &&
    value.path === null &&
    value.start_line === null &&
    value.end_line === null
  );
}

function isFeatureGraphEdge(
  value: unknown,
  indexId: string,
  nodeIds: Set<string>,
  evidenceIds: Set<string>,
): value is FeatureGraphEdge {
  if (!isRecord(value)) {
    return false;
  }
  const origin = value.origin;
  const relation = value.relation;
  if (
    !isString(value.edge_id) ||
    value.index_id !== indexId ||
    !isString(value.source_id) ||
    !isString(value.target_id) ||
    !["semantic", "static"].includes(String(origin)) ||
    ![
      "triggered_by",
      "implemented_by",
      "related_to",
      "contains",
      "imports",
      "calls",
    ].includes(String(relation)) ||
    !isStringArray(value.evidence_ids) ||
    new Set(value.evidence_ids).size !== value.evidence_ids.length ||
    value.evidence_ids.some((evidenceId) => !evidenceIds.has(evidenceId)) ||
    !nodeIds.has(value.source_id) ||
    !nodeIds.has(value.target_id)
  ) {
    return false;
  }
  const semanticRelations = new Set(["triggered_by", "implemented_by", "related_to"]);
  return origin === "semantic"
    ? isString(relation) && semanticRelations.has(relation)
    : isString(relation) && ["contains", "imports", "calls"].includes(relation);
}

export function isFeatureGraphResponse(value: unknown): value is FeatureGraphResponse {
  if (!isRecord(value) || value.schema_version !== SCHEMA_VERSION || !isRecord(value.graph)) {
    return false;
  }
  const graph = value.graph;
  const graphIndexId = graph.index_id;
  if (
    graph.schema_version !== SCHEMA_VERSION ||
    !isString(graph.project_id) ||
    !isString(graphIndexId) ||
    !isString(graph.generation_id) ||
    !["succeeded", "partial", "insufficient_evidence", "failed"].includes(String(graph.status)) ||
    !Array.isArray(graph.evidence) ||
    !graph.evidence.every((evidence) => isEvidence(evidence) && evidence.index_id === graphIndexId) ||
    !Array.isArray(graph.nodes) ||
    !Array.isArray(graph.edges) ||
    !isStringArray(graph.unavailable_target_ids) ||
    !isStringArray(graph.limitations) ||
    !isTruncation(graph)
  ) {
    return false;
  }
  const evidenceIds = new Set(graph.evidence.map((evidence) => evidence.evidence_id));
  const nodeIds = new Set(graph.nodes.map((node) => (isRecord(node) ? node.node_id : undefined)));
  if (
    evidenceIds.size !== graph.evidence.length ||
    nodeIds.size !== graph.nodes.length ||
    nodeIds.has(undefined) ||
    new Set(graph.edges.map((edge) => (isRecord(edge) ? edge.edge_id : undefined))).size !==
      graph.edges.length ||
    new Set(graph.unavailable_target_ids).size !== graph.unavailable_target_ids.length
  ) {
    return false;
  }
  if (!graph.nodes.every((node) => isFeatureGraphNode(node, graphIndexId, evidenceIds))) {
    return false;
  }
  if (
    !graph.edges.every((edge) =>
      isFeatureGraphEdge(edge, graphIndexId, nodeIds as Set<string>, evidenceIds),
    )
  ) {
    return false;
  }
  return true;
}

export function isSourceResponse(value: unknown): value is SourceResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.index_id) &&
    isEvidence(value.evidence)
  );
}

export function isHealthResponse(value: unknown): value is HealthResponse {
  return (
    isRecord(value) &&
    value.status === "ok" &&
    value.service === "codetrail-api" &&
    isString(value.version)
  );
}

function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.code) &&
    isString(value.message)
  );
}

async function parseJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function requestJson<T>(
  path: string,
  validator: (value: unknown) => value is T,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${apiBaseUrl}${path}`, { ...init, headers, cache: "no-store" });
  const payload = await parseJson(response);
  if (!response.ok) {
    if (isApiErrorResponse(payload)) {
      throw new ApiRequestError(payload.message, response.status, payload.code);
    }
    throw new ApiRequestError(`请求失败（HTTP ${response.status}）`, response.status, "internal_error");
  }
  if (!validator(payload)) {
    throw new Error("后端返回了无法识别的响应");
  }
  return payload;
}

export function createIndex(
  repositoryId: string,
  revision: string,
  dirty: boolean,
  signal?: AbortSignal,
): Promise<IndexResponse> {
  return requestJson(
    "/api/indexes",
    isIndexResponse,
    {
      method: "POST",
      signal,
      body: JSON.stringify({
        schema_version: SCHEMA_VERSION,
        repository_id: repositoryId,
        revision,
        dirty,
      }),
    },
  );
}

export function createProject(
  name: string,
  repositoryId: string,
  signal?: AbortSignal,
): Promise<ProjectRecord> {
  return requestJson(
    "/api/projects",
    isProjectRecord,
    {
      method: "POST",
      signal,
      body: JSON.stringify({
        schema_version: SCHEMA_VERSION,
        name,
        repository_id: repositoryId,
      }),
    },
  );
}

export function importLocalPath(
  name: string,
  path: string,
  revision = "working-tree",
  dirty = true,
  idempotencyKey?: string,
  signal?: AbortSignal,
): Promise<LocalImportResponse> {
  return requestJson(
    "/api/imports/local",
    isLocalImportResponse,
    {
      method: "POST",
      signal,
      body: JSON.stringify({
        schema_version: SCHEMA_VERSION,
        name,
        path,
        revision,
        dirty,
        ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
      }),
    },
  );
}

export function importGitHubRepository(
  name: string,
  url: string,
  ref?: string,
  idempotencyKey?: string,
  signal?: AbortSignal,
): Promise<GitHubImportResponse> {
  return requestJson(
    "/api/imports/github",
    isGitHubImportResponse,
    {
      method: "POST",
      signal,
      body: JSON.stringify({
        schema_version: SCHEMA_VERSION,
        name,
        url,
        ...(ref ? { ref } : {}),
        ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
      }),
    },
  );
}

export function getImportStatus(
  attemptId: string,
  signal?: AbortSignal,
): Promise<ImportAttemptResponse> {
  return requestJson(
    `/api/imports/${encodeURIComponent(attemptId)}`,
    isImportAttemptResponse,
    { signal },
  );
}

export function retryLocalImport(
  attemptId: string,
  signal?: AbortSignal,
): Promise<LocalImportResponse> {
  return requestJson(
    `/api/imports/${encodeURIComponent(attemptId)}/retry`,
    isLocalImportResponse,
    { method: "POST", signal },
  );
}

export function retryGitHubImport(
  attemptId: string,
  signal?: AbortSignal,
): Promise<GitHubImportResponse> {
  return requestJson(
    `/api/imports/${encodeURIComponent(attemptId)}/retry-github`,
    isGitHubImportResponse,
    { method: "POST", signal },
  );
}

export function retryImportCleanup(
  attemptId: string,
  signal?: AbortSignal,
): Promise<GitHubImportResponse> {
  return requestJson(
    `/api/imports/${encodeURIComponent(attemptId)}/cleanup`,
    isGitHubImportResponse,
    { method: "POST", signal },
  );
}

export function listProjects(signal?: AbortSignal): Promise<ProjectListResponse> {
  return requestJson("/api/projects", isProjectListResponse, { signal });
}

export function getProject(projectId: string, signal?: AbortSignal): Promise<ProjectRecord> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}`,
    isProjectRecord,
    { signal },
  );
}

export function listProjectIndexes(
  projectId: string,
  signal?: AbortSignal,
): Promise<ProjectIndexesResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes`,
    isProjectIndexesResponse,
    { signal },
  );
}

export function listProjectSessions(
  projectId: string,
  limit = 40,
  cursor?: string,
  signal?: AbortSignal,
): Promise<SessionListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions?${params.toString()}`,
    isSessionListResponse,
    { signal },
  );
}

export function getProjectSession(
  projectId: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}`,
    isSessionResponse,
    { signal },
  );
}

export function listProjectSessionMessages(
  projectId: string,
  sessionId: string,
  limit = 100,
  cursor?: string,
  signal?: AbortSignal,
): Promise<SessionMessagesResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/messages?${params.toString()}`,
    isSessionMessagesResponse,
    { signal },
  );
}

export function createProjectSession(
  projectId: string,
  input: CreateSessionInput,
  signal?: AbortSignal,
): Promise<SessionResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions`,
    isSessionResponse,
    {
      method: "POST",
      signal,
      body: JSON.stringify({
        schema_version: SCHEMA_VERSION,
        topic: input.topic,
        ...(input.indexId ? { index_id: input.indexId } : {}),
        ...(input.featureGenerationId ? { feature_generation_id: input.featureGenerationId } : {}),
        ...(input.idempotencyKey ? { idempotency_key: input.idempotencyKey } : {}),
      }),
    },
  );
}

export function appendProjectSessionMessage(
  projectId: string,
  sessionId: string,
  source: SessionMessageSource,
  content: string,
  idempotencyKey?: string,
  signal?: AbortSignal,
): Promise<SessionMessage> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/messages`,
    (value): value is { schema_version: typeof SCHEMA_VERSION; message: SessionMessage } => (
      isRecord(value) && value.schema_version === SCHEMA_VERSION && isSessionMessage(value.message)
    ),
    {
      method: "POST",
      signal,
      body: JSON.stringify({
        schema_version: SCHEMA_VERSION,
        source,
        content,
        ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
      }),
    },
  ).then((response) => response.message);
}

export function createProjectSessionRun(
  projectId: string,
  sessionId: string,
  input: CreateRunInput,
  signal?: AbortSignal,
): Promise<CreateRunResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/runs`,
    isCreateRunResponse,
    {
      method: "POST",
      signal,
      body: JSON.stringify({
        schema_version: SCHEMA_VERSION,
        index_id: input.indexId,
        issue: {
          schema_version: SCHEMA_VERSION,
          id: input.issueId,
          title: input.title,
          body: input.body,
        },
        mode: input.mode,
        ...(input.mode === "fixture" && input.scenario ? { fixture_scenario: input.scenario } : {}),
        ...(input.featureGenerationId ? { feature_generation_id: input.featureGenerationId } : {}),
        ...(input.messageEndSequence ? { message_end_sequence: input.messageEndSequence } : {}),
        ...(input.idempotencyKey ? { idempotency_key: input.idempotencyKey } : {}),
      }),
    },
  );
}

export function listProjectSessionRuns(
  projectId: string,
  sessionId: string,
  limit = 40,
  cursor?: string,
  signal?: AbortSignal,
): Promise<RunListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/runs?${params.toString()}`,
    isRunListResponse,
    { signal },
  );
}

export function getProjectSessionRun(
  projectId: string,
  sessionId: string,
  runId: string,
  signal?: AbortSignal,
): Promise<RunStatusResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}`,
    isRunStatusResponse,
    { signal },
  );
}

export function listProjectSessionRunEvents(
  projectId: string,
  sessionId: string,
  runId: string,
  afterSequence = 0,
  limit = 100,
  signal?: AbortSignal,
): Promise<RunEventsResponse> {
  const params = new URLSearchParams({ after_sequence: String(afterSequence), limit: String(limit) });
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/events?${params.toString()}`,
    isRunEventsResponse,
    { signal },
  );
}

export function getProjectSessionRunEventsStreamUrl(
  projectId: string,
  sessionId: string,
  runId: string,
  afterSequence = 0,
): string {
  const params = new URLSearchParams({ after_sequence: String(afterSequence) });
  return `${apiBaseUrl}/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/events/stream?${params.toString()}`;
}

export function listProjectSessionRunToolResults(
  projectId: string,
  sessionId: string,
  runId: string,
  afterSequence = 0,
  limit = 40,
  signal?: AbortSignal,
): Promise<RunToolResultsResponse> {
  const params = new URLSearchParams({ after_sequence: String(afterSequence), limit: String(limit) });
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/tool-results?${params.toString()}`,
    isRunToolResultsResponse,
    { signal },
  );
}

export function getProjectSessionRunReport(
  projectId: string,
  sessionId: string,
  runId: string,
  signal?: AbortSignal,
): Promise<RunReportResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/report`,
    isRunReportResponse,
    { signal },
  );
}

export function getProjectSessionRunImpact(
  projectId: string,
  sessionId: string,
  runId: string,
  query: {
    candidateId?: string;
    findingId?: string;
    direction: ImpactDirection;
    depth?: number;
    limit?: number;
  },
  signal?: AbortSignal,
): Promise<RunImpactResponse> {
  const params = new URLSearchParams({
    direction: query.direction,
    depth: String(query.depth ?? 2),
    limit: String(query.limit ?? 40),
  });
  if (query.candidateId) {
    params.set("candidate_id", query.candidateId);
  }
  if (query.findingId) {
    params.set("finding_id", query.findingId);
  }
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/impact?${params.toString()}`,
    isRunImpactResponse,
    { signal },
  );
}

export function cancelProjectSessionRun(
  projectId: string,
  sessionId: string,
  runId: string,
  reason = "cancelled from the workbench",
  signal?: AbortSignal,
): Promise<CancelRunResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/cancel`,
    isCancelRunResponse,
    {
      method: "POST",
      signal,
      body: JSON.stringify({ schema_version: SCHEMA_VERSION, reason }),
    },
  );
}

export function createProjectIndex(
  projectId: string,
  revision: string,
  dirty: boolean,
  signal?: AbortSignal,
): Promise<IndexResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes`,
    isIndexResponse,
    {
      method: "POST",
      signal,
      body: JSON.stringify({
        schema_version: SCHEMA_VERSION,
        revision,
        dirty,
      }),
    },
  );
}

export function openProjectIndex(
  projectId: string,
  indexId: string,
  signal?: AbortSignal,
): Promise<IndexResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}`,
    isIndexResponse,
    { signal },
  );
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return requestJson("/api/health", isHealthResponse, { signal });
}

export function openIndex(indexId: string, signal?: AbortSignal): Promise<IndexResponse> {
  return requestJson(`/api/indexes/${encodeURIComponent(indexId)}`, isIndexResponse, { signal });
}

export function searchIndex(
  indexId: string,
  query: string,
  limit = 40,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const params = new URLSearchParams({ query, limit: String(limit) });
  return requestJson(
    `/api/indexes/${encodeURIComponent(indexId)}/search?${params.toString()}`,
    isSearchResponse,
    { signal },
  );
}

export function searchProjectIndex(
  projectId: string,
  indexId: string,
  query: string,
  limit = 40,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const params = new URLSearchParams({ query, limit: String(limit) });
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}/search?${params.toString()}`,
    isSearchResponse,
    { signal },
  );
}

export function getNeighbors(
  indexId: string,
  symbolId: string,
  direction: NeighborDirection,
  edgeKinds: EdgeKind[],
  signal?: AbortSignal,
): Promise<NeighborsResponse> {
  const params = new URLSearchParams({
    symbol_id: symbolId,
    direction,
    depth: "2",
    limit: "40",
  });
  for (const edgeKind of edgeKinds) {
    params.append("edge_kinds", edgeKind);
  }
  return requestJson(
    `/api/indexes/${encodeURIComponent(indexId)}/neighbors?${params.toString()}`,
    isNeighborsResponse,
    { signal },
  );
}

export function getProjectNeighbors(
  projectId: string,
  indexId: string,
  symbolId: string,
  direction: NeighborDirection,
  edgeKinds: EdgeKind[],
  signal?: AbortSignal,
): Promise<NeighborsResponse> {
  const params = new URLSearchParams({
    symbol_id: symbolId,
    direction,
    depth: "2",
    limit: "40",
  });
  for (const edgeKind of edgeKinds) {
    params.append("edge_kinds", edgeKind);
  }
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}/neighbors?${params.toString()}`,
    isNeighborsResponse,
    { signal },
  );
}

export function readSource(
  indexId: string,
  path: string,
  startLine: number,
  endLine: number,
  signal?: AbortSignal,
): Promise<SourceResponse> {
  const params = new URLSearchParams({
    path,
    start_line: String(startLine),
    end_line: String(endLine),
  });
  return requestJson(
    `/api/indexes/${encodeURIComponent(indexId)}/source?${params.toString()}`,
    isSourceResponse,
    { signal },
  );
}

export function readProjectSource(
  projectId: string,
  indexId: string,
  path: string,
  startLine: number,
  endLine: number,
  signal?: AbortSignal,
): Promise<SourceResponse> {
  const params = new URLSearchParams({
    path,
    start_line: String(startLine),
    end_line: String(endLine),
  });
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}/source?${params.toString()}`,
    isSourceResponse,
    { signal },
  );
}

export function getProjectOverview(
  projectId: string,
  indexId: string,
  maxDepth = 4,
  maxPaths = 40,
  maxModules = 100,
  signal?: AbortSignal,
): Promise<ProjectOverview> {
  const params = new URLSearchParams({
    max_depth: String(maxDepth),
    max_paths: String(maxPaths),
    max_modules: String(maxModules),
  });
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}/overview?${params.toString()}`,
    isProjectOverviewResponse,
    { signal },
  );
}

export function generateProjectFeatures(
  projectId: string,
  indexId: string,
  request: FeatureGenerationRequest,
  signal?: AbortSignal,
): Promise<FeatureGenerationResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}/features`,
    isFeatureGenerationResponse,
    {
      method: "POST",
      signal,
      body: JSON.stringify({
        schema_version: SCHEMA_VERSION,
        ...request,
      }),
    },
  );
}

export function getProjectFeatureGeneration(
  projectId: string,
  indexId: string,
  generationId: string,
  signal?: AbortSignal,
): Promise<FeatureGenerationResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}/features/${encodeURIComponent(generationId)}`,
    isFeatureGenerationResponse,
    { signal },
  );
}

export function listProjectFeatureGenerations(
  projectId: string,
  indexId?: string,
  limit = 20,
  cursor?: string,
  signal?: AbortSignal,
): Promise<FeatureGenerationListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (indexId) {
    params.set("index_id", indexId);
  }
  if (cursor) {
    params.set("cursor", cursor);
  }
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/features?${params.toString()}`,
    isFeatureGenerationListResponse,
    { signal },
  );
}

export function getProjectFeatureGraph(
  projectId: string,
  indexId: string,
  generationId: string,
  maxNodes = 40,
  maxEdges = 80,
  signal?: AbortSignal,
): Promise<FeatureGraphResponse> {
  const params = new URLSearchParams({
    max_nodes: String(maxNodes),
    max_edges: String(maxEdges),
  });
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}/features/${encodeURIComponent(generationId)}/graph?${params.toString()}`,
    isFeatureGraphResponse,
    { signal },
  );
}
