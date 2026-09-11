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

export function isSourceResponse(value: unknown): value is SourceResponse {
  return (
    isRecord(value) &&
    value.schema_version === SCHEMA_VERSION &&
    isString(value.index_id) &&
    isRecord(value.evidence) &&
    isString(value.evidence.evidence_id) &&
    isString(value.evidence.index_id) &&
    isString(value.evidence.path) &&
    isNumber(value.evidence.start_line) &&
    isNumber(value.evidence.end_line) &&
    isString(value.evidence.excerpt) &&
    isString(value.evidence.content_hash) &&
    isString(value.evidence.kind)
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
