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
