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
  rules_version: string | null;
  configuration_hash: string | null;
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
  unresolved_truncated: boolean | null;
  unresolved_truncation_reason: string | null;
};

export type FreshnessStatus = "unchecked" | "consistent" | "changed" | "unknown";
export type RemoteFreshnessStatus = "not_applicable" | "not_checked" | "consistent" | "changed" | "unknown";
export type FreshnessReasonCode =
  | "missing_index_metadata"
  | "rules_changed"
  | "configuration_changed"
  | "source_changed"
  | "source_read_error"
  | "source_unavailable"
  | "decode_error"
  | "file_too_large"
  | "symlink"
  | "outside_root"
  | "file_changed_during_check"
  | "budget_exceeded"
  | "remote_not_checked"
  | "remote_metadata_missing";

export type IndexFreshness = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  baseline_index_id: string;
  status: FreshnessStatus;
  checked_at: string | null;
  added_count: number;
  modified_count: number;
  deleted_count: number;
  changed_paths: string[];
  changed_paths_truncated: boolean;
  scanned_files: number;
  scanned_bytes: number;
  scan_complete: boolean;
  scan_truncated: boolean;
  scan_truncation_reason: string | null;
  current_rules_version: string | null;
  current_configuration_hash: string | null;
  reasons: FreshnessReasonCode[];
  remote_status: RemoteFreshnessStatus;
  remote_source_url: string | null;
  remote_requested_ref: string | null;
  remote_resolved_ref: string | null;
  remote_resolved_commit: string | null;
};

export type IndexRefreshMode = "reused" | "historical_reused" | "created";

export type IndexRefreshResponse = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  baseline_index_id: string;
  mode: IndexRefreshMode;
  index: IndexResponse;
  freshness: IndexFreshness;
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

export type TrashListResponse = {
  schema_version: typeof SCHEMA_VERSION;
  projects: ProjectRecord[];
  sessions: Session[];
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
  resolved_ref: string | null;
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
  resolved_ref: string | null;
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
export type RunMode = "fixture" | "real" | "external";
export type RunAnalysisKind = "issue" | "understanding";
export type RunExecutionSource = "internal" | "codex";
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
  analysis_kind: RunAnalysisKind;
  execution_source: RunExecutionSource;
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

export type ExplanationStep = {
  step_id: string;
  kind: "confirmed" | "inferred" | "unknown";
  title: string;
  description: string;
  evidence_ids: string[];
  symbol_ids: string[];
};

export type CodeExplanation = {
  coverage: "complete" | "partial";
  steps: ExplanationStep[];
};

export type RunReport = {
  schema_version: typeof SCHEMA_VERSION;
  report_id: string;
  run_id: string;
  project_id: string;
  session_id: string;
  index_id: string;
  status: "succeeded" | "failed" | "cancelled" | "interrupted";
  outcome: "candidate_found" | "insufficient_evidence" | "explanation" | "failed" | "cancelled" | "interrupted";
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
  explanation: CodeExplanation | null;
  limitations: string[];
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
  index_truncated: boolean;
  index_truncation_reason: string | null;
  index_unresolved_truncated: boolean | null;
  index_unresolved_truncation_reason: string | null;
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

export type ModuleSymbolsPageResponse = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  index_id: string;
  module: SymbolRecord;
  symbols: SymbolRecord[];
  next_cursor: string | null;
  has_more: boolean;
};

export type Neighbor = {
  symbol: SymbolRecord;
  edge: CodeEdge;
};

export type ContextualCall = {
  context_id: string;
  index_id: string;
  activation_edge_id: string;
  activation_source: SymbolRecord;
  activation_location: SourceLocation;
  source: SymbolRecord;
  target: SymbolRecord;
  source_location: SourceLocation;
  receiver_constructor_location: SourceLocation;
  field_assignment_location: SourceLocation;
  field_name: string;
  binding_mutation_after_escape_unverified: boolean;
};

export type NeighborsResponse = {
  schema_version: typeof SCHEMA_VERSION;
  index_id: string;
  root: SymbolRecord;
  nodes: SymbolRecord[];
  neighbors: Neighbor[];
  contextual_calls?: ContextualCall[];
  unresolved: UnresolvedReference[];
  truncated: boolean;
  truncation_reason: string | null;
  index_truncated: boolean;
  index_truncation_reason: string | null;
  index_unresolved_truncated: boolean | null;
  index_unresolved_truncation_reason: string | null;
};

export type OverviewEntryPoint = {
  start_line: number;
  end_line: number;
  entry_point_id: string;
  index_id: string;
  path: string;
  kind: "python_main_module" | "python_main_guard" | "fastapi_route";
  rule_id: string;
  label: string;
  symbol_id: string | null;
  http_method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "OPTIONS" | "HEAD" | "TRACE" | null;
  declared_path?: string | null;
  handler_symbol?: SymbolRecord | null;
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
  classification_counts: OverviewClassificationCounts;
};

export type OverviewClassificationCategory = "application_entry" | "public_api" | "extension_point" | "unknown";

export type OverviewClassificationCounts = Record<OverviewClassificationCategory, number>;

export type OverviewClassification = {
  classification_id: string;
  index_id: string;
  category: OverviewClassificationCategory;
  module_id: string;
  symbol_id: string | null;
  symbol_name: string;
  label: string;
  rule_id: string;
  entry_point_id: string | null;
  evidence: Evidence[];
  evidence_error: string | null;
};

export type OverviewClassificationCoverage = {
  total: number;
  visible: number;
  category_counts: OverviewClassificationCounts;
  visible_category_counts: OverviewClassificationCounts;
  truncated: boolean;
};

export type OverviewGroup = {
  group_id: string;
  index_id: string;
  group_kind: "http_path" | "source_directory" | "responsibility";
  group_key: string;
  label: string;
  module_ids: string[];
  entry_point_ids: string[];
};

export type OverviewModuleRelation = {
  relation_id: string;
  index_id: string;
  source_module_id: string;
  target_module_id: string;
  source_module: SymbolRecord;
  target_module: SymbolRecord;
  relation_kinds: Array<"imports" | "calls">;
  edge_count: number;
  edges: CodeEdge[];
};

export type OverviewHttpAssociationCoverage = {
  analysis_status: "complete" | "incomplete" | "not_analyzed";
  path_analysis: "parameterized" | "literal_only" | "not_analyzed";
  client_request_count: number;
  matched_count: number;
  visible_match_count: number;
  unmatched_count: number;
  ambiguous_count: number;
  unknown_count: number;
};

export type OverviewHttpAssociation = {
  association_id: string;
  index_id: string;
  match_kind: "method_path_shape";
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "OPTIONS" | "HEAD" | "TRACE";
  path: string;
  client_path_kind: "literal" | "parameterized";
  client_path_template: string | null;
  client_name: string;
  client_module: SymbolRecord;
  server_module: SymbolRecord;
  route_entry_point_id: string;
  client_evidence: Evidence;
  route_evidence: Evidence;
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
  total_groups: number;
  visible_groups: number;
  total_module_relations: number;
  visible_module_relations: number;
};

export type ProjectOverview = {
  schema_version: typeof SCHEMA_VERSION;
  project_id: string;
  index: IndexSnapshot;
  rules_version: string;
  entry_status: "identified" | "not_identified" | "partial";
  responsibility_status: "not_configured" | "ready" | "partial" | "invalid";
  responsibility_evidence: Evidence | null;
  entries: OverviewEntryPoint[];
  classifications: OverviewClassification[];
  classification_coverage: OverviewClassificationCoverage;
  groups: OverviewGroup[];
  modules: OverviewModule[];
  module_relations: OverviewModuleRelation[];
  http_associations: OverviewHttpAssociation[];
  http_association_coverage: OverviewHttpAssociationCoverage;
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

export class RequestTimeoutError extends Error {
  constructor() {
    super("请求超时，服务端操作可能仍在处理；请刷新导入状态确认，或使用相同内容重新连接。");
    this.name = "TimeoutError";
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

function isIndexTruncation(value: Record<string, unknown>): boolean {
  return (
    typeof value.index_truncated === "boolean" &&
    (value.index_truncated
      ? isString(value.index_truncation_reason)
      : value.index_truncation_reason === null)
  );
}

function isOptionalUnresolvedTruncation(
  value: Record<string, unknown>,
  flagKey: string,
  reasonKey: string,
): boolean {
  const flag = value[flagKey];
  const reason = value[reasonKey];
  if (flag === undefined && reason === undefined) {
    return true;
  }
  return (
    (typeof flag === "boolean" || flag === null) &&
    (reason === null || isString(reason)) &&
    (flag === true ? isString(reason) : reason === null)
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
    ["ready", "building", "failed"].includes(String(value.status)) &&
    (value.rules_version === undefined || value.rules_version === null || isString(value.rules_version)) &&
    (value.configuration_hash === undefined || value.configuration_hash === null || isString(value.configuration_hash))
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

export function isTrashListResponse(value: unknown): value is TrashListResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    Array.isArray(value.projects) &&
    value.projects.every(isProjectRecord) &&
    Array.isArray(value.sessions) &&
    value.sessions.every(isSession)
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
    isNullableString(value.resolved_ref) &&
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
    isNullableString(value.resolved_ref) &&
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
    ["fixture", "real", "external"].includes(String(record.mode)) &&
    ["issue", "understanding"].includes(String(record.analysis_kind)) &&
    ["internal", "codex"].includes(String(record.execution_source)) &&
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

function isExplanationStep(value: unknown): value is ExplanationStep {
  if (!isRecord(value)) {
    return false;
  }
  const step = value as Record<string, unknown>;
  return (
    isString(step.step_id) &&
    ["confirmed", "inferred", "unknown"].includes(String(step.kind)) &&
    isString(step.title) &&
    isString(step.description) &&
    isStringArray(step.evidence_ids) &&
    isStringArray(step.symbol_ids) &&
    new Set(step.evidence_ids).size === step.evidence_ids.length &&
    new Set(step.symbol_ids).size === step.symbol_ids.length &&
    (step.kind !== "confirmed" || step.evidence_ids.length > 0)
  );
}

function isCodeExplanation(value: unknown): value is CodeExplanation {
  if (!isRecord(value) || !["complete", "partial"].includes(String(value.coverage))) {
    return false;
  }
  if (!Array.isArray(value.steps) || value.steps.length < 1 || value.steps.length > 12) {
    return false;
  }
  if (!value.steps.every(isExplanationStep)) {
    return false;
  }
  const stepIds = value.steps.map((step) => step.step_id);
  return (
    new Set(stepIds).size === stepIds.length &&
    (value.coverage !== "complete" || value.steps.every((step) => step.kind !== "unknown"))
  );
}

function isRunReport(value: unknown): value is RunReport {
  if (!isRecord(value) || !isRecord(value.graph)) {
    return false;
  }
  const graph = value.graph;
  const explanation = value.explanation;
  return (
    value.schema_version === SCHEMA_VERSION &&
    isString(value.report_id) &&
    isString(value.run_id) &&
    isString(value.project_id) &&
    isString(value.session_id) &&
    isString(value.index_id) &&
    ["succeeded", "failed", "cancelled", "interrupted"].includes(String(value.status)) &&
    ["candidate_found", "insufficient_evidence", "explanation", "failed", "cancelled", "interrupted"].includes(String(value.outcome)) &&
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
    ((value.outcome === "explanation" && isCodeExplanation(explanation)) ||
      (value.outcome !== "explanation" && explanation === null)) &&
    isStringArray(value.limitations) &&
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
    !isTruncation(value) ||
    !isIndexTruncation(value) ||
    !isOptionalUnresolvedTruncation(
      value,
      "index_unresolved_truncated",
      "index_unresolved_truncation_reason",
    )
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
    isTruncation(value) &&
    isOptionalUnresolvedTruncation(
      value,
      "unresolved_truncated",
      "unresolved_truncation_reason",
    )
  );
}

export function isIndexFreshness(value: unknown): value is IndexFreshness {
  const reasons = [
    "missing_index_metadata",
    "rules_changed",
    "configuration_changed",
    "source_changed",
    "source_read_error",
    "source_unavailable",
    "decode_error",
    "file_too_large",
    "symlink",
    "outside_root",
    "file_changed_during_check",
    "budget_exceeded",
    "remote_not_checked",
    "remote_metadata_missing",
  ];
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.project_id) &&
    isString(value.baseline_index_id) &&
    ["unchecked", "consistent", "changed", "unknown"].includes(String(value.status)) &&
    (value.checked_at === null || isString(value.checked_at)) &&
    isNonNegativeInteger(value.added_count) &&
    isNonNegativeInteger(value.modified_count) &&
    isNonNegativeInteger(value.deleted_count) &&
    isStringArray(value.changed_paths) &&
    typeof value.changed_paths_truncated === "boolean" &&
    isNonNegativeInteger(value.scanned_files) &&
    isNonNegativeInteger(value.scanned_bytes) &&
    typeof value.scan_complete === "boolean" &&
    typeof value.scan_truncated === "boolean" &&
    (value.scan_truncation_reason === null || isString(value.scan_truncation_reason)) &&
    (value.current_rules_version === null || isString(value.current_rules_version)) &&
    (value.current_configuration_hash === null || isString(value.current_configuration_hash)) &&
    Array.isArray(value.reasons) &&
    value.reasons.every((reason) => reasons.includes(String(reason))) &&
    ["not_applicable", "not_checked", "consistent", "changed", "unknown"].includes(String(value.remote_status)) &&
    (value.remote_status !== "unknown" || value.status === "unknown") &&
    (value.remote_status !== "changed" || value.status !== "consistent") &&
    isNullableString(value.remote_source_url) &&
    isNullableString(value.remote_requested_ref) &&
    isNullableString(value.remote_resolved_ref) &&
    isNullableString(value.remote_resolved_commit)
  );
}

export function isIndexRefreshResponse(value: unknown): value is IndexRefreshResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.project_id) &&
    isString(value.baseline_index_id) &&
    ["reused", "historical_reused", "created"].includes(String(value.mode)) &&
    isIndexResponse(value.index) &&
    isIndexFreshness(value.freshness) &&
    value.freshness.baseline_index_id === value.baseline_index_id &&
    (value.mode !== "reused" || value.index.index.index_id === value.baseline_index_id) &&
    (value.mode === "reused" || value.index.index.index_id !== value.baseline_index_id)
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

export function isModuleSymbolsPageResponse(value: unknown): value is ModuleSymbolsPageResponse {
  if (
    !isRecord(value) ||
    value.schema_version !== SCHEMA_VERSION ||
    !isString(value.project_id) ||
    !isString(value.index_id) ||
    !isSymbolRecord(value.module) ||
    value.module.kind !== "module" ||
    value.module.index_id !== value.index_id ||
    !Array.isArray(value.symbols) ||
    value.symbols.length > 40 ||
    !value.symbols.every(isSymbolRecord) ||
    !isNullableString(value.next_cursor) ||
    (typeof value.next_cursor === "string" && value.next_cursor.length > 500) ||
    typeof value.has_more !== "boolean" ||
    value.has_more !== (value.next_cursor !== null)
  ) {
    return false;
  }
  const module = value.module as SymbolRecord;
  const indexId = value.index_id as string;
  const symbolIds = new Set<string>();
  return value.symbols.every((symbol) => {
    if (
      symbol.index_id !== indexId ||
      symbol.path !== module.path ||
      !["class", "function", "test"].includes(symbol.kind) ||
      symbolIds.has(symbol.symbol_id)
    ) {
      return false;
    }
    symbolIds.add(symbol.symbol_id);
    return true;
  });
}

function isNeighbor(value: unknown): value is Neighbor {
  return isRecord(value) && isSymbolRecord(value.symbol) && isCodeEdge(value.edge);
}

function isValidSymbolRange(symbol: SymbolRecord): boolean {
  return (
    Number.isInteger(symbol.start_line) && symbol.start_line > 0 &&
    Number.isInteger(symbol.end_line) && symbol.start_line <= symbol.end_line
  );
}

function isValidLocationForSymbol(location: SourceLocation, symbol: SymbolRecord): boolean {
  return (
    isValidSymbolRange(symbol) &&
    location.path === symbol.path &&
    Number.isInteger(location.start_line) && location.start_line > 0 &&
    Number.isInteger(location.end_line) &&
    location.start_line <= location.end_line &&
    location.start_line >= symbol.start_line &&
    location.end_line <= symbol.end_line
  );
}

function isSafeRelativePath(value: string): boolean {
  return (
    value.length > 0 &&
    value.length <= 2048 &&
    !value.startsWith("/") &&
    !/^[a-zA-Z]:/.test(value) &&
    !value.includes("\\") &&
    value.split("/").every((part) => part !== "" && part !== "." && part !== "..")
  );
}

function isValidSourceLocation(location: SourceLocation): boolean {
  return (
    isSafeRelativePath(location.path) &&
    Number.isInteger(location.start_line) && location.start_line > 0 &&
    Number.isInteger(location.end_line) &&
    location.start_line <= location.end_line
  );
}

function isPythonFieldName(value: unknown): value is string {
  return typeof value === "string" && [...value].length <= 200 && /^[_\p{ID_Start}][\p{ID_Continue}_]*$/u.test(value);
}

function sameSymbol(left: SymbolRecord, right: SymbolRecord): boolean {
  return (
    left.symbol_id === right.symbol_id &&
    left.index_id === right.index_id &&
    left.path === right.path &&
    left.qualified_name === right.qualified_name &&
    left.kind === right.kind &&
    left.start_line === right.start_line &&
    left.end_line === right.end_line
  );
}

function isContextualCall(
  value: unknown,
  indexId: string,
  nodes: SymbolRecord[],
  neighbors: Neighbor[],
  root: SymbolRecord,
): value is ContextualCall {
  if (
    !isRecord(value) ||
    !isString(value.context_id) || value.context_id.length === 0 || value.context_id.length > 500 ||
    !isString(value.index_id) || value.index_id !== indexId ||
    !isString(value.activation_edge_id) ||
    !isSymbolRecord(value.activation_source) ||
    !isSourceLocation(value.activation_location) ||
    !isSymbolRecord(value.source) ||
    !isSymbolRecord(value.target) ||
    !isSourceLocation(value.source_location) ||
    !isSourceLocation(value.receiver_constructor_location) ||
    !isSourceLocation(value.field_assignment_location) ||
    !isPythonFieldName(value.field_name) ||
    typeof value.binding_mutation_after_escape_unverified !== "boolean"
  ) {
    return false;
  }

  const call = value as ContextualCall;
  const activations = neighbors.filter(({ edge }) => edge.edge_id === call.activation_edge_id);
  if (activations.length !== 1) {
    return false;
  }
  const activation = activations[0];
  const returnedSymbols = [root, ...nodes, ...neighbors.map(({ symbol }) => symbol)];
  const matchesReturnedSymbol = (candidate: SymbolRecord) => {
    const matches = returnedSymbols.filter((symbol) => symbol.symbol_id === candidate.symbol_id);
    return matches.length > 0 && matches.every((symbol) => sameSymbol(symbol, candidate));
  };
  return (
    call.activation_source.index_id === indexId &&
    call.source.index_id === indexId &&
    call.target.index_id === indexId &&
    isValidSymbolRange(call.target) &&
    isSafeRelativePath(call.target.path) &&
    matchesReturnedSymbol(call.activation_source) &&
    matchesReturnedSymbol(call.source) &&
    isValidSourceLocation(call.activation_location) &&
    isValidLocationForSymbol(call.activation_location, call.activation_source) &&
    isValidSourceLocation(call.source_location) &&
    isValidLocationForSymbol(call.source_location, call.source) &&
    isValidSourceLocation(call.receiver_constructor_location) &&
    isValidSourceLocation(call.field_assignment_location) &&
    call.field_assignment_location.path === call.source.path &&
    activation !== undefined &&
    activation.edge.index_id === indexId &&
    activation.edge.kind === "calls" &&
    activation.edge.source_id === call.activation_source.symbol_id &&
    activation.edge.target_id === call.source.symbol_id &&
    activation.edge.source_location.path === call.activation_location.path &&
    activation.edge.source_location.start_line === call.activation_location.start_line &&
    activation.edge.source_location.end_line === call.activation_location.end_line
  );
}

export function isNeighborsResponse(value: unknown): value is NeighborsResponse {
  if (!(
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
    isIndexTruncation(value) &&
    isOptionalUnresolvedTruncation(
      value,
      "index_unresolved_truncated",
      "index_unresolved_truncation_reason",
    ) &&
    isTruncation(value)
  )) {
    return false;
  }

  if (value.contextual_calls === undefined) {
    return true;
  }
  if (!Array.isArray(value.contextual_calls)) {
    return false;
  }
  const contextIds = new Set<string>();
  return value.contextual_calls.every((call) => {
    if (
      !isContextualCall(
        call,
        value.index_id as string,
        value.nodes as SymbolRecord[],
        value.neighbors as Neighbor[],
        value.root as SymbolRecord,
      ) ||
      contextIds.has(call.context_id)
    ) {
      return false;
    }
    contextIds.add(call.context_id);
    return true;
  });
}

function isOverviewEntryPoint(value: unknown): value is OverviewEntryPoint {
  if (!isRecord(value) || !isLineRange(value)) {
    return false;
  }
  const kind = String(value.kind);
  const routeFieldsValid = kind === "fastapi_route"
    ? (
      ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"].includes(String(value.http_method)) &&
      isString(value.declared_path) &&
      value.declared_path.startsWith("/") &&
      isSymbolRecord(value.handler_symbol) &&
      value.handler_symbol.kind === "function" &&
      value.handler_symbol.index_id === value.index_id &&
      value.handler_symbol.path === value.path &&
      value.symbol_id === value.handler_symbol.symbol_id &&
      isNumber(value.start_line) &&
      isNumber(value.end_line) &&
      value.handler_symbol.start_line >= value.start_line &&
      value.handler_symbol.end_line <= value.end_line
    )
    : (
      (value.http_method === undefined || value.http_method === null) &&
      (value.declared_path === undefined || value.declared_path === null) &&
      (value.handler_symbol === undefined || value.handler_symbol === null)
    );
  return (
    isString(value.entry_point_id) &&
    isString(value.index_id) &&
    isString(value.path) &&
    ["python_main_module", "python_main_guard", "fastapi_route"].includes(kind) &&
    isString(value.rule_id) &&
    isString(value.label) &&
    (value.symbol_id === null || isString(value.symbol_id)) &&
    routeFieldsValid &&
    (value.evidence === null || isEvidence(value.evidence)) &&
    (value.evidence_error === null || isString(value.evidence_error)) &&
    isStringArray(value.direct_symbol_ids) &&
    isStringArray(value.unresolved_ids)
  );
}

const overviewClassificationCategories: OverviewClassificationCategory[] = ["application_entry", "public_api", "extension_point", "unknown"];

function isOverviewClassificationCounts(value: unknown): value is OverviewClassificationCounts {
  return isRecord(value) && overviewClassificationCategories.every((category) => isNonNegativeInteger(value[category]));
}

function isOverviewClassification(
  value: unknown,
  indexId: string,
  modules: OverviewModule[],
  entries: OverviewEntryPoint[],
): value is OverviewClassification {
  if (!isRecord(value) || value.index_id !== indexId || !isString(value.module_id)) return false;
  const module = modules.find((item) => item.module_id === value.module_id);
  if (!module || module.index_id !== indexId) return false;
  if (!overviewClassificationCategories.includes(value.category as OverviewClassificationCategory)) return false;
  const entry = isString(value.entry_point_id) ? entries.find((item) => item.entry_point_id === value.entry_point_id) ?? null : null;
  const applicationEntryValid = value.category === "application_entry"
    ? entry !== null && entry.index_id === indexId && Array.isArray(value.evidence) && value.evidence.length === 0
    : value.entry_point_id === null;
  const categoryFieldsValid = value.category === "unknown"
    ? value.symbol_id === null
    : value.category === "public_api" || value.category === "extension_point"
      ? isString(value.symbol_id)
      : true;
  return (
    isString(value.classification_id) &&
    (value.symbol_id === null || isString(value.symbol_id)) &&
    isString(value.symbol_name) &&
    isString(value.label) &&
    isString(value.rule_id) &&
    (value.entry_point_id === null || entry !== null) &&
    applicationEntryValid &&
    categoryFieldsValid &&
    Array.isArray(value.evidence) &&
    value.evidence.length <= 4 &&
    (value.category === "application_entry" || value.evidence.length > 0 || (value.evidence_error !== null && isString(value.evidence_error))) &&
    value.evidence.every((evidence) => isEvidence(evidence) && evidence.index_id === indexId && evidence.kind === "source" && evidence.path === module.symbol.path) &&
    (value.evidence_error === null || isString(value.evidence_error))
  );
}

function isOverviewClassificationsConsistent(classifications: OverviewClassification[], modules: OverviewModule[]): boolean {
  const ids = new Set<string>();
  const countsByModule = new Map<string, OverviewClassificationCounts>();
  for (const item of classifications) {
    if (ids.has(item.classification_id)) return false;
    ids.add(item.classification_id);
    const counts = countsByModule.get(item.module_id) ?? { application_entry: 0, public_api: 0, extension_point: 0, unknown: 0 };
    counts[item.category] += 1;
    countsByModule.set(item.module_id, counts);
  }
  return modules.every((module) => {
    const visible = countsByModule.get(module.module_id);
    return !visible || overviewClassificationCategories.every((category) => visible[category] <= module.classification_counts[category]);
  });
}

function isOverviewClassificationCoverage(value: unknown, classifications: OverviewClassification[]): value is OverviewClassificationCoverage {
  if (
    !isRecord(value) ||
    !isNonNegativeInteger(value.total) ||
    !isNonNegativeInteger(value.visible) ||
    value.visible > value.total ||
    value.visible !== classifications.length ||
    (value.visible < value.total && value.truncated !== true) ||
    typeof value.truncated !== "boolean" ||
    !isOverviewClassificationCounts(value.category_counts) ||
    !isOverviewClassificationCounts(value.visible_category_counts)
  ) return false;
  const categoryCounts = value.category_counts as OverviewClassificationCounts;
  const visibleCategoryCounts = value.visible_category_counts as OverviewClassificationCounts;
  const totalCount = overviewClassificationCategories.reduce((sum, category) => sum + categoryCounts[category], 0);
  const visibleCount = overviewClassificationCategories.reduce((sum, category) => sum + visibleCategoryCounts[category], 0);
  const visibleByCategory = Object.fromEntries(overviewClassificationCategories.map((category) => [
    category,
    classifications.filter((item) => item.category === category).length,
  ])) as OverviewClassificationCounts;
  return overviewClassificationCategories.every((category) =>
    visibleCategoryCounts[category] === visibleByCategory[category] &&
    visibleCategoryCounts[category] <= categoryCounts[category]
  ) && totalCount === value.total && visibleCount === value.visible;
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
    typeof value.entry_reachable === "boolean" &&
    isOverviewClassificationCounts(value.classification_counts)
  );
}

function isOverviewGroup(value: unknown): value is OverviewGroup {
  return (
    isRecord(value) &&
    isString(value.group_id) &&
    isString(value.index_id) &&
    ["http_path", "source_directory", "responsibility"].includes(String(value.group_kind)) &&
    isString(value.group_key) &&
    isString(value.label) &&
    isStringArray(value.module_ids) &&
    isStringArray(value.entry_point_ids) &&
    (value.group_kind !== "http_path" || (value.group_key.startsWith("/") && value.entry_point_ids.length > 0)) &&
    (value.group_kind !== "source_directory" || value.module_ids.length > 0) &&
    (value.group_kind !== "responsibility" || value.entry_point_ids.length === 0)
  );
}

function isOverviewModuleRelation(value: unknown): value is OverviewModuleRelation {
  return (
    isRecord(value) &&
    isString(value.relation_id) &&
    isString(value.index_id) &&
    isString(value.source_module_id) &&
    isString(value.target_module_id) &&
    value.source_module_id !== value.target_module_id &&
    isSymbolRecord(value.source_module) &&
    isSymbolRecord(value.target_module) &&
    value.source_module.symbol_id === value.source_module_id &&
    value.target_module.symbol_id === value.target_module_id &&
    value.source_module.index_id === value.index_id &&
    value.target_module.index_id === value.index_id &&
    value.source_module.kind === "module" &&
    value.target_module.kind === "module" &&
    Array.isArray(value.relation_kinds) &&
    value.relation_kinds.length > 0 &&
    value.relation_kinds.length <= 2 &&
    value.relation_kinds.every((kind) => ["imports", "calls"].includes(String(kind))) &&
    isNumber(value.edge_count) &&
    value.edge_count >= 1 &&
    Array.isArray(value.edges) &&
    value.edges.length > 0 &&
    value.edges.length <= 8 &&
    value.edges.every((edge) => isCodeEdge(edge) && edge.index_id === value.index_id && (value.relation_kinds as Array<"imports" | "calls">).includes(edge.kind as "imports" | "calls"))
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
    isNumber(value.unresolved_count) &&
    isNumber(value.total_groups) &&
    isNumber(value.visible_groups) &&
    value.visible_groups <= value.total_groups &&
    isNumber(value.total_module_relations) &&
    isNumber(value.visible_module_relations) &&
    value.visible_module_relations <= value.total_module_relations
  );
}

function isOverviewHttpAssociationCoverage(value: unknown): value is OverviewHttpAssociationCoverage {
  if (!isRecord(value) || !["complete", "incomplete", "not_analyzed"].includes(String(value.analysis_status)) ||
    !["parameterized", "literal_only", "not_analyzed"].includes(String(value.path_analysis))) {
    return false;
  }
  if (
    !isNonNegativeInteger(value.client_request_count) ||
    !isNonNegativeInteger(value.matched_count) ||
    !isNonNegativeInteger(value.visible_match_count) ||
    !isNonNegativeInteger(value.unmatched_count) ||
    !isNonNegativeInteger(value.ambiguous_count) ||
    !isNonNegativeInteger(value.unknown_count)
  ) return false;
  return (
    value.visible_match_count <= value.matched_count &&
    value.matched_count + value.unmatched_count + value.ambiguous_count + value.unknown_count === value.client_request_count
  );
}

function isOverviewHttpAssociation(value: unknown, indexId: string): value is OverviewHttpAssociation {
  if (!isRecord(value)) return false;
  const methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"];
  return (
    isString(value.association_id) &&
    value.index_id === indexId &&
    value.match_kind === "method_path_shape" &&
    methods.includes(String(value.method)) &&
    isString(value.path) && value.path.startsWith("/api/") && !/[?#]/.test(value.path) &&
    (value.client_path_kind === "literal"
      ? value.client_path_template === null
      : value.client_path_kind === "parameterized" &&
        isString(value.client_path_template) &&
        value.client_path_template.startsWith("/api/") &&
        !/[?#]/.test(value.client_path_template) &&
        value.client_path_template.split("/").includes("{*}") &&
        value.client_path_template.split("/").every((segment) =>
          segment === "{*}" || (!segment.includes("{") && !segment.includes("}")))) &&
    isString(value.client_name) &&
    isSymbolRecord(value.client_module) && value.client_module.kind === "module" && value.client_module.index_id === indexId &&
    isSymbolRecord(value.server_module) && value.server_module.kind === "module" && value.server_module.index_id === indexId &&
    isString(value.route_entry_point_id) &&
    isEvidence(value.client_evidence) && value.client_evidence.index_id === indexId && value.client_evidence.kind === "source" && value.client_evidence.path === value.client_module.path &&
    isEvidence(value.route_evidence) && value.route_evidence.index_id === indexId && value.route_evidence.kind === "source" && value.route_evidence.path === value.server_module.path
  );
}

export function isProjectOverviewResponse(value: unknown): value is ProjectOverview {
  const validGroups = isRecord(value) && Array.isArray(value.groups) && value.groups.every(isOverviewGroup);
  const hasResponsibilityGroups = isRecord(value) && Array.isArray(value.groups) &&
    value.groups.some((group) => isRecord(group) && group.group_kind === "responsibility");
  const responsibilityStatus = isRecord(value) ? value.responsibility_status : null;
  const responsibilityEvidence = isRecord(value) ? value.responsibility_evidence : undefined;
  const responsibilityGroupsMatchStatus =
    (responsibilityStatus === "not_configured" && !hasResponsibilityGroups && responsibilityEvidence === null) ||
    (responsibilityStatus === "invalid" && !hasResponsibilityGroups) ||
    ((responsibilityStatus === "ready" || responsibilityStatus === "partial") && hasResponsibilityGroups);

  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.project_id) &&
    isIndexSnapshot(value.index) &&
    isString(value.rules_version) &&
    ["identified", "not_identified", "partial"].includes(String(value.entry_status)) &&
    responsibilityGroupsMatchStatus &&
    (value.responsibility_evidence === null || (
      isEvidence(value.responsibility_evidence) &&
      value.responsibility_evidence.index_id === value.index.index_id &&
      value.responsibility_evidence.path === "codetrail-subsystems.json"
    )) &&
    Array.isArray(value.entries) &&
    value.entries.every(isOverviewEntryPoint) &&
    validGroups &&
    Array.isArray(value.modules) &&
    value.modules.every(isOverviewModule) &&
    Array.isArray(value.classifications) &&
    value.classifications.every((classification) => isOverviewClassification(classification, (value.index as IndexSnapshot).index_id, value.modules as OverviewModule[], value.entries as OverviewEntryPoint[])) &&
    isOverviewClassificationsConsistent(value.classifications as OverviewClassification[], value.modules as OverviewModule[]) &&
    isOverviewClassificationCoverage(value.classification_coverage, value.classifications as OverviewClassification[]) &&
    Array.isArray(value.module_relations) &&
    value.module_relations.every(isOverviewModuleRelation) &&
    Array.isArray(value.http_associations) &&
    value.http_associations.every((item) => isOverviewHttpAssociation(item, (value.index as IndexSnapshot).index_id)) &&
    isOverviewHttpAssociationCoverage(value.http_association_coverage) &&
    value.http_association_coverage.visible_match_count === value.http_associations.length &&
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
  init: RequestInit & { timeoutMs?: number } = {},
): Promise<T> {
  const { timeoutMs, ...requestInit } = init;
  if (timeoutMs !== undefined && (!Number.isFinite(timeoutMs) || timeoutMs <= 0)) {
    throw new Error("request timeout must be a positive finite number");
  }
  const headers = new Headers(requestInit.headers);
  headers.set("Accept", "application/json");
  if (requestInit.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let timeoutController: AbortController | null = null;
  let timeoutId: ReturnType<typeof setTimeout> | null = null;
  let onAbort: (() => void) | null = null;
  if (timeoutMs !== undefined) {
    timeoutController = new AbortController();
    if (requestInit.signal?.aborted) {
      timeoutController.abort(requestInit.signal.reason);
    } else if (requestInit.signal) {
      onAbort = () => timeoutController?.abort(requestInit.signal?.reason);
      requestInit.signal.addEventListener("abort", onAbort, { once: true });
    }
    timeoutId = setTimeout(() => {
      timeoutController?.abort(new RequestTimeoutError());
    }, timeoutMs);
  }
  try {
    const response = await fetch(`${apiBaseUrl}${path}`, {
      ...requestInit,
      signal: timeoutController?.signal ?? requestInit.signal,
      headers,
      cache: "no-store",
    });
    const payload = await parseJson(response);
    const activeSignal = timeoutController?.signal ?? requestInit.signal;
    if (activeSignal?.aborted) {
      if (activeSignal.reason instanceof Error) {
        throw activeSignal.reason;
      }
      throw new DOMException("The operation was aborted", "AbortError");
    }
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
  } finally {
    if (timeoutId !== null) {
      clearTimeout(timeoutId);
    }
    if (onAbort && requestInit.signal) {
      requestInit.signal.removeEventListener("abort", onAbort);
    }
  }
}

export const IMPORT_REQUEST_TIMEOUT_MS = 60_000;
export const IMPORT_STATUS_REQUEST_TIMEOUT_MS = 10_000;

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
      timeoutMs: IMPORT_REQUEST_TIMEOUT_MS,
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
      timeoutMs: IMPORT_REQUEST_TIMEOUT_MS,
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
    { signal, timeoutMs: IMPORT_STATUS_REQUEST_TIMEOUT_MS },
  );
}

export function retryLocalImport(
  attemptId: string,
  signal?: AbortSignal,
): Promise<LocalImportResponse> {
  return requestJson(
    `/api/imports/${encodeURIComponent(attemptId)}/retry`,
    isLocalImportResponse,
    { method: "POST", signal, timeoutMs: IMPORT_REQUEST_TIMEOUT_MS },
  );
}

export function retryGitHubImport(
  attemptId: string,
  signal?: AbortSignal,
): Promise<GitHubImportResponse> {
  return requestJson(
    `/api/imports/${encodeURIComponent(attemptId)}/retry-github`,
    isGitHubImportResponse,
    { method: "POST", signal, timeoutMs: IMPORT_REQUEST_TIMEOUT_MS },
  );
}

export function retryImportCleanup(
  attemptId: string,
  signal?: AbortSignal,
): Promise<GitHubImportResponse> {
  return requestJson(
    `/api/imports/${encodeURIComponent(attemptId)}/cleanup`,
    isGitHubImportResponse,
    { method: "POST", signal, timeoutMs: IMPORT_STATUS_REQUEST_TIMEOUT_MS },
  );
}

export function listProjects(signal?: AbortSignal): Promise<ProjectListResponse> {
  return requestJson("/api/projects", isProjectListResponse, { signal });
}

export function listTrash(signal?: AbortSignal): Promise<TrashListResponse> {
  return requestJson("/api/trash", isTrashListResponse, { signal });
}

export function moveProjectToTrash(projectId: string, signal?: AbortSignal): Promise<ProjectRecord> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}`, isProjectRecord, {
    method: "DELETE",
    signal,
  });
}

export function restoreProject(projectId: string, signal?: AbortSignal): Promise<ProjectRecord> {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/restore`, isProjectRecord, {
    method: "POST",
    signal,
  });
}

export function moveSessionToTrash(
  projectId: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}`,
    isSessionResponse,
    { method: "DELETE", signal },
  );
}

export function restoreSession(
  projectId: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/restore`,
    isSessionResponse,
    { method: "POST", signal },
  );
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
  timeoutMs?: number,
): Promise<IndexResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}`,
    isIndexResponse,
    { signal, timeoutMs },
  );
}

export function checkProjectIndexFreshness(
  projectId: string,
  indexId: string,
  signal?: AbortSignal,
): Promise<IndexFreshness> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}/freshness`,
    isIndexFreshness,
    { method: "POST", signal },
  );
}

export function refreshProjectIndex(
  projectId: string,
  indexId: string,
  revision: string,
  dirty: boolean,
  signal?: AbortSignal,
): Promise<IndexRefreshResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}/refresh`,
    isIndexRefreshResponse,
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

export function refreshGitHubSource(
  projectId: string,
  indexId: string,
  signal?: AbortSignal,
): Promise<IndexRefreshResponse> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}/github-refresh`,
    isIndexRefreshResponse,
    { method: "POST", signal },
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

export function listProjectModuleSymbols(
  projectId: string,
  indexId: string,
  moduleId: string,
  limit = 40,
  cursor?: string,
  signal?: AbortSignal,
): Promise<ModuleSymbolsPageResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}` +
      `/modules/${encodeURIComponent(moduleId)}/symbols?${params.toString()}`,
    isModuleSymbolsPageResponse,
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
  depth = 2,
): Promise<NeighborsResponse> {
  const params = new URLSearchParams({
    symbol_id: symbolId,
    direction,
    depth: String(depth),
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
  maxGroups = 80,
  maxModuleRelations = 200,
  signal?: AbortSignal,
): Promise<ProjectOverview> {
  const params = new URLSearchParams({
    max_depth: String(maxDepth),
    max_paths: String(maxPaths),
    max_modules: String(maxModules),
    max_groups: String(maxGroups),
    max_module_relations: String(maxModuleRelations),
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
