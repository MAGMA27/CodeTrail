import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_GITHUB_REQUEST_TIMEOUT_MS,
  GITHUB_REQUEST_TIMEOUT_MS,
  RequestTimeoutError,
  isFeatureGenerationListResponse,
  isFeatureGenerationResponse,
  isFeatureGraphResponse,
  isIndexFreshness,
  isIndexResponse,
  isIndexRefreshResponse,
  isModuleSymbolsPageResponse,
  isNeighborsResponse,
  isProjectOverviewResponse,
  isProjectJourneyCatalogResponse,
  isProjectJourneyResponse,
  isRunImpactResponse,
  isRunReportResponse,
  isSessionListResponse,
  isSessionMessagesResponse,
  isSessionResponse,
  isTrashListResponse,
  createProjectSessionRun,
  cancelJob,
  getJob,
  getProjectNeighbors,
  openProjectIndex,
  importGitHubRepository,
  refreshGitHubSource,
  retryGitHubImport,
  parseGitHubRequestTimeoutMs,
  requestJson,
  SCHEMA_VERSION,
} from "./api.ts";

const indexId = "index-overview-test";
const moduleId = "symbol-module";
const functionId = "symbol-main";
const entryId = "entry-main";

function journeyFixture() {
  const index = {
    index_id: indexId,
    repository_id: "journey-repo",
    revision: "v1",
    dirty: false,
    snapshot_hash: "journey-snapshot",
    status: "ready",
    rules_version: "typescript-facts-v5",
    configuration_hash: "config-hash",
  };
  const summary = {
    journey_id: "journey-import",
    index_id: indexId,
    action_label: "导入并建立索引",
    label_candidates: ["导入并建立索引", "建立中"],
    label_dynamic: true,
    event_name: "onSubmit",
    action_path: "apps/web/app/workbench-ui.tsx",
    action_start_line: 120,
    method: "POST",
    path: "/api/imports/local",
  };
  const coverage = {
    parser_status: "ready",
    http_association_status: "complete",
    scanned_file_count: 3,
    action_count: 1,
    journey_count: 1,
    unsupported_action_count: 0,
    action_facts_omitted_count: 0,
    prop_facts_omitted_count: 0,
    action_facts_truncated: false,
    prop_facts_truncated: false,
    relation_truncated: false,
    truncated: false,
    limitations: [],
  };
  return {
    index,
    summary,
    coverage,
    catalog: {
      schema_version: SCHEMA_VERSION,
      project_id: "journey-project",
      index,
      rules_version: "feature-journey-v1",
      journeys: [summary],
      coverage,
    },
    detail: {
      schema_version: SCHEMA_VERSION,
      project_id: "journey-project",
      index,
      rules_version: "feature-journey-v1",
      journey_id: summary.journey_id,
      summary,
      steps: [{
        step_id: "trigger",
        role: "trigger",
        title: "提交导入表单",
        relation_kind: "ui_action",
        certainty: "static",
        explanation: "JSX submit action",
        symbols: [],
        evidence: [],
      }],
      coverage,
      markers: [],
    },
  };
}

test("journey response validators bind steps to one index and cap details at eight", () => {
  const fixture = journeyFixture();
  assert.equal(isProjectJourneyCatalogResponse(fixture.catalog), true);
  assert.equal(isProjectJourneyResponse(fixture.detail), true);
  const unlinkedSummary = { ...fixture.summary, method: null, path: null };
  assert.equal(isProjectJourneyCatalogResponse({
    ...fixture.catalog,
    journeys: [unlinkedSummary],
  }), true);
  assert.equal(isProjectJourneyResponse({
    ...fixture.detail,
    summary: unlinkedSummary,
  }), true);
  assert.equal(isProjectJourneyCatalogResponse({
    ...fixture.catalog,
    journeys: [{ ...unlinkedSummary, path: "/api/imports/local" }],
  }), false);
  assert.equal(isProjectJourneyResponse({
    ...fixture.detail,
    summary: { ...unlinkedSummary, method: "POST" },
  }), false);
  assert.equal(isProjectJourneyCatalogResponse({
    ...fixture.catalog,
    journeys: [{ ...fixture.summary, index_id: "other-index" }],
  }), false);
  assert.equal(isProjectJourneyResponse({
    ...fixture.detail,
    steps: Array.from({ length: 9 }, (_unused, index) => ({
      ...fixture.detail.steps[0],
      step_id: `step-${index}`,
    })),
  }), false);
  assert.equal(isProjectJourneyResponse({
    ...fixture.detail,
    coverage: { ...fixture.coverage, action_facts_omitted_count: -1 },
  }), false);
});

test("module symbol page validator enforces fixed module scope and cursor consistency", () => {
  const module = {
    symbol_id: moduleId,
    index_id: indexId,
    path: "pkg/__main__.py",
    qualified_name: "pkg.__main__",
    kind: "module",
    start_line: 1,
    end_line: 2,
  };
  const symbol = {
    symbol_id: functionId,
    index_id: indexId,
    path: module.path,
    qualified_name: "pkg.__main__.main",
    kind: "function",
    start_line: 2,
    end_line: 2,
  };
  const page = {
    schema_version: SCHEMA_VERSION,
    project_id: "project-overview-test",
    index_id: indexId,
    module,
    symbols: [symbol],
    next_cursor: "opaque-cursor",
    has_more: true,
  };
  assert.equal(isModuleSymbolsPageResponse(page), true);
  assert.equal(isModuleSymbolsPageResponse({ ...page, symbols: [{ ...symbol, path: "other.py" }] }), false);
  assert.equal(isModuleSymbolsPageResponse({ ...page, symbols: [symbol, symbol] }), false);
  assert.equal(isModuleSymbolsPageResponse({ ...page, has_more: false }), false);
});

function overview() {
  return {
    schema_version: SCHEMA_VERSION,
    project_id: "project-overview-test",
    index: {
      index_id: indexId,
      repository_id: "repo",
      revision: "v1",
      dirty: false,
      snapshot_hash: "hash",
      status: "ready",
    },
    rules_version: "python-entry-v1",
    entry_status: "identified",
    responsibility_status: "not_configured",
    responsibility_evidence: null,
    entries: [
      {
        entry_point_id: entryId,
        index_id: indexId,
        path: "pkg/__main__.py",
        start_line: 1,
        end_line: 2,
        kind: "python_main_guard",
        rule_id: "python.ast.main_guard",
        label: "main guard",
        symbol_id: moduleId,
        evidence: {
          evidence_id: "evidence-main",
          index_id: indexId,
          path: "pkg/__main__.py",
          start_line: 1,
          end_line: 2,
          excerpt: "if __name__ == '__main__':\n    main()",
          content_hash: "content-hash",
          kind: "source",
        },
        evidence_error: null,
        direct_symbol_ids: [functionId],
        unresolved_ids: [],
      },
    ],
    groups: [
      {
        group_id: "group-source",
        index_id: indexId,
        group_kind: "source_directory",
        group_key: "pkg",
        label: "SOURCE pkg",
        module_ids: [moduleId],
        entry_point_ids: [entryId],
      },
    ],
    modules: [
      {
        module_id: moduleId,
        index_id: indexId,
        symbol: {
          symbol_id: moduleId,
          index_id: indexId,
          path: "pkg/__main__.py",
          qualified_name: "pkg.__main__",
          kind: "module",
          start_line: 1,
          end_line: 2,
        },
        entry_point_ids: [entryId],
        entry_reachable: true,
        classification_counts: { application_entry: 0, public_api: 0, extension_point: 0, unknown: 0 },
      },
    ],
    classifications: [],
    classification_coverage: {
      total: 0,
      visible: 0,
      category_counts: { application_entry: 0, public_api: 0, extension_point: 0, unknown: 0 },
      visible_category_counts: { application_entry: 0, public_api: 0, extension_point: 0, unknown: 0 },
      truncated: false,
    },
    module_relations: [],
    http_associations: [],
    http_association_coverage: {
      analysis_status: "complete",
      path_analysis: "parameterized",
      client_request_count: 0,
      matched_count: 0,
      visible_match_count: 0,
      unmatched_count: 0,
      ambiguous_count: 0,
      unknown_count: 0,
    },
    paths: [
      {
        path_id: "path-main",
        index_id: indexId,
        entry_point_id: entryId,
        symbol_ids: [moduleId, functionId],
        edges: [
          {
            edge_id: "edge-main",
            index_id: indexId,
            source_id: moduleId,
            target_id: functionId,
            kind: "calls",
            extraction_method: "ast",
            source_location: {
              path: "pkg/__main__.py",
              start_line: 2,
              end_line: 2,
            },
          },
        ],
        depth: 1,
      },
    ],
    unresolved: [],
    coverage: {
      total_modules: 1,
      visible_modules: 1,
      reachable_modules: 1,
      unreachable_modules: 0,
      entry_point_count: 1,
      unresolved_count: 0,
      total_groups: 1,
      visible_groups: 1,
      total_module_relations: 0,
      visible_module_relations: 0,
    },
    notes: [],
    truncated: false,
    truncation_reason: null,
  };
}

test("project overview validator accepts bounded snapshot-linked data", () => {
  assert.equal(isProjectOverviewResponse(overview()), true);
});

test("project overview classification validator enforces snapshot evidence and coverage counts", () => {
  const value = structuredClone(overview()) as any;
  value.modules[0].classification_counts = { application_entry: 1, public_api: 1, extension_point: 0, unknown: 1 };
  value.classifications = [
    {
      classification_id: "class-entry",
      index_id: indexId,
      category: "application_entry",
      module_id: moduleId,
      symbol_id: moduleId,
      symbol_name: "pkg.__main__",
      label: "main guard",
      rule_id: "python.ast.main_guard",
      entry_point_id: entryId,
      evidence: [],
      evidence_error: null,
    },
    {
      classification_id: "class-api",
      index_id: indexId,
      category: "public_api",
      module_id: moduleId,
      symbol_id: functionId,
      symbol_name: "pkg.__main__.main",
      label: "exported function",
      rule_id: "python.exports.all",
      entry_point_id: null,
      evidence: Array.from({ length: 4 }, (_, index) => ({ ...overview().entries[0].evidence, evidence_id: `evidence-api-${index}` })),
      evidence_error: null,
    },
    {
      classification_id: "class-unknown",
      index_id: indexId,
      category: "unknown",
      module_id: moduleId,
      symbol_id: null,
      symbol_name: "pkg.__main__",
      label: "module-level unknown",
      rule_id: "python.classification.unknown",
      entry_point_id: null,
      evidence: [],
      evidence_error: "No source evidence",
    },
  ];
  value.classification_coverage = {
    total: 4,
    visible: 3,
    category_counts: { application_entry: 1, public_api: 1, extension_point: 1, unknown: 1 },
    visible_category_counts: { application_entry: 1, public_api: 1, extension_point: 0, unknown: 1 },
    truncated: true,
  };
  assert.equal(isProjectOverviewResponse(value), true);

  const evidenceIncomplete = structuredClone(value);
  evidenceIncomplete.classification_coverage.total = 3;
  evidenceIncomplete.classification_coverage.category_counts = { application_entry: 1, public_api: 1, extension_point: 0, unknown: 1 };
  assert.equal(isProjectOverviewResponse(evidenceIncomplete), true);

  const wrongEvidenceIndex = structuredClone(value);
  wrongEvidenceIndex.classifications[1].evidence[0].index_id = "index-other";
  assert.equal(isProjectOverviewResponse(wrongEvidenceIndex), false);

  const excessiveEvidence = structuredClone(value);
  excessiveEvidence.classifications[1].evidence.push({ ...excessiveEvidence.classifications[1].evidence[0], evidence_id: "evidence-api-excess" });
  assert.equal(isProjectOverviewResponse(excessiveEvidence), false);

  const negativeModuleCount = structuredClone(value);
  negativeModuleCount.modules[0].classification_counts.public_api = -1;
  assert.equal(isProjectOverviewResponse(negativeModuleCount), false);

  const inconsistentCoverage = structuredClone(value);
  inconsistentCoverage.classification_coverage.visible_category_counts.unknown = 0;
  assert.equal(isProjectOverviewResponse(inconsistentCoverage), false);
});

test("project overview responsibility status matches declared groups", () => {
  const group = {
    group_id: "group-api-responsibility",
    index_id: indexId,
    group_kind: "responsibility",
    group_key: "api",
    label: "API",
    module_ids: [moduleId],
    entry_point_ids: [],
  };
  const invalidUnconfigured = structuredClone(overview());
  invalidUnconfigured.groups.push(group);
  invalidUnconfigured.coverage.total_groups = 2;
  invalidUnconfigured.coverage.visible_groups = 2;
  assert.equal(isProjectOverviewResponse(invalidUnconfigured), false);

  const ready = structuredClone(invalidUnconfigured);
  ready.responsibility_status = "ready";
  assert.equal(isProjectOverviewResponse(ready), true);

  const partial = structuredClone(ready);
  partial.responsibility_status = "partial";
  assert.equal(isProjectOverviewResponse(partial), true);

  const invalidWithGroup = structuredClone(ready);
  invalidWithGroup.responsibility_status = "invalid";
  assert.equal(isProjectOverviewResponse(invalidWithGroup), false);

  const missingReadyGroup = structuredClone(overview());
  missingReadyGroup.responsibility_status = "ready";
  assert.equal(isProjectOverviewResponse(missingReadyGroup), false);

  const invalidWithoutGroup = structuredClone(overview());
  invalidWithoutGroup.responsibility_status = "invalid";
  assert.equal(isProjectOverviewResponse(invalidWithoutGroup), true);
});

test("project overview validator accepts aggregated module relations and rejects mismatched edge kinds", () => {
  const value = structuredClone(overview()) as any;
  const targetModule = {
    ...overview().modules[0].symbol,
    symbol_id: "symbol-target-module",
    path: "pkg/service.py",
    qualified_name: "pkg.service",
  };
  value.modules.push({
    module_id: targetModule.symbol_id,
    index_id: indexId,
    symbol: targetModule,
    entry_point_ids: [],
    entry_reachable: false,
    classification_counts: { application_entry: 0, public_api: 0, extension_point: 0, unknown: 0 },
  });
  value.coverage.total_modules = 2;
  value.coverage.visible_modules = 2;
  value.coverage.unreachable_modules = 1;
  value.module_relations = [
    {
      relation_id: "module-relation-main-service",
      index_id: indexId,
      source_module_id: moduleId,
      target_module_id: targetModule.symbol_id,
      source_module: overview().modules[0].symbol,
      target_module: targetModule,
      relation_kinds: ["imports"],
      edge_count: 1,
      edges: [
        {
          edge_id: "edge-module-import",
          index_id: indexId,
          source_id: moduleId,
          target_id: targetModule.symbol_id,
          kind: "imports",
          extraction_method: "ast",
          source_location: { path: "pkg/__main__.py", start_line: 1, end_line: 1 },
        },
      ],
    },
  ];
  value.coverage.total_module_relations = 1;
  value.coverage.visible_module_relations = 1;
  assert.equal(isProjectOverviewResponse(value), true);

  value.module_relations[0].edges[0].kind = "calls";
  assert.equal(isProjectOverviewResponse(value), false);
});

test("project overview validator rejects broken path or evidence shape", () => {
  const brokenPath = structuredClone(overview());
  brokenPath.paths[0].symbol_ids = [moduleId];
  assert.equal(isProjectOverviewResponse(brokenPath), false);

  const brokenEvidence = structuredClone(overview()) as unknown as {
    entries: Array<{ evidence: Record<string, unknown> }>;
  };
  brokenEvidence.entries[0].evidence.content_hash = 42;
  assert.equal(isProjectOverviewResponse(brokenEvidence), false);
});

test("project overview validator accepts a FastAPI route while preserving legacy entries", () => {
  const routeOverview = structuredClone(overview()) as unknown as {
    entries: Array<Record<string, unknown>>;
  } & Record<string, unknown>;
  routeOverview.entries[0] = {
    ...routeOverview.entries[0],
    kind: "fastapi_route",
    label: "GET /health → pkg.__main__.health",
    symbol_id: functionId,
    http_method: "GET",
    declared_path: "/health",
    handler_symbol: {
      ...overview().modules[0].symbol,
      symbol_id: functionId,
      qualified_name: "pkg.__main__.health",
      kind: "function",
      start_line: 2,
      end_line: 2,
    },
  };
  assert.equal(isProjectOverviewResponse(routeOverview), true);

  routeOverview.entries[0].declared_path = "dynamic-path";
  assert.equal(isProjectOverviewResponse(routeOverview), false);
});

test("neighbor validator keeps query and index truncation states separate", () => {
  const symbol = {
    symbol_id: "symbol-neighbor",
    index_id: indexId,
    path: "pkg/app.py",
    qualified_name: "pkg.app.load",
    kind: "function",
    start_line: 4,
    end_line: 7,
  };
  const value: Record<string, unknown> = {
    schema_version: SCHEMA_VERSION,
    index_id: indexId,
    root: symbol,
    nodes: [symbol],
    neighbors: [],
    unresolved: [],
    truncated: false,
    truncation_reason: null,
    index_truncated: false,
    index_truncation_reason: null,
    index_unresolved_truncated: false,
    index_unresolved_truncation_reason: null,
  };
  assert.equal(isNeighborsResponse(value), true);

  const incompleteIndex = structuredClone(value);
  incompleteIndex.index_truncated = true;
  incompleteIndex.index_truncation_reason = "max_unresolved reached";
  assert.equal(isNeighborsResponse(incompleteIndex), true);

  const missingReason = structuredClone(incompleteIndex);
  missingReason.index_truncation_reason = null;
  assert.equal(isNeighborsResponse(missingReason), false);

  const strayReason = structuredClone(value);
  strayReason.index_truncation_reason = "not-truncated";
  assert.equal(isNeighborsResponse(strayReason), false);

  const incompleteUnresolved = structuredClone(value);
  incompleteUnresolved.index_unresolved_truncated = true;
  incompleteUnresolved.index_unresolved_truncation_reason = "max_unresolved reached";
  assert.equal(isNeighborsResponse(incompleteUnresolved), true);

  const missingUnresolvedReason = structuredClone(incompleteUnresolved);
  missingUnresolvedReason.index_unresolved_truncation_reason = null;
  assert.equal(isNeighborsResponse(missingUnresolvedReason), false);
});

test("neighbor contextual calls require same-snapshot symbols and matching call evidence", () => {
  const activationSource = {
    symbol_id: "symbol-activation",
    index_id: indexId,
    path: "pkg/main.py",
    qualified_name: "pkg.main.create_app",
    kind: "function",
    start_line: 10,
    end_line: 20,
  };
  const source = {
    symbol_id: "symbol-source",
    index_id: indexId,
    path: "pkg/services.py",
    qualified_name: "pkg.services.WorkspaceService.read_source",
    kind: "function",
    start_line: 30,
    end_line: 40,
  };
  const target = {
    symbol_id: "symbol-target",
    index_id: indexId,
    path: "pkg/index.py",
    qualified_name: "pkg.index.IndexService.read_source",
    kind: "function",
    start_line: 60,
    end_line: 80,
  };
  const activationEdge = {
    edge_id: "edge-activation",
    index_id: indexId,
    source_id: activationSource.symbol_id,
    target_id: source.symbol_id,
    kind: "calls",
    extraction_method: "ast",
    source_location: { path: activationSource.path, start_line: 12, end_line: 12 },
  };
  const call = {
    context_id: "context-main-to-service",
    index_id: indexId,
    activation_edge_id: activationEdge.edge_id,
    activation_source: activationSource,
    activation_location: activationEdge.source_location,
    source,
    target,
    source_location: { path: source.path, start_line: 35, end_line: 35 },
    receiver_constructor_location: { path: "pkg/main.py", start_line: 71, end_line: 74 },
    field_assignment_location: { path: source.path, start_line: 25, end_line: 25 },
    field_name: "依赖",
    binding_mutation_after_escape_unverified: false,
  };
  const value: Record<string, unknown> = {
    schema_version: SCHEMA_VERSION,
    index_id: indexId,
    root: activationSource,
    nodes: [activationSource, source],
    neighbors: [{ symbol: source, edge: activationEdge }],
    contextual_calls: [call],
    unresolved: [],
    truncated: false,
    truncation_reason: null,
    index_truncated: false,
    index_truncation_reason: null,
    index_unresolved_truncated: false,
    index_unresolved_truncation_reason: null,
  };

  assert.equal(isNeighborsResponse(value), true);
  assert.equal(isNeighborsResponse({
    ...value,
    root: source,
    nodes: [source, activationSource],
    neighbors: [{ symbol: activationSource, edge: activationEdge }],
  }), true);
  const legacy = structuredClone(value);
  delete legacy.contextual_calls;
  assert.equal(isNeighborsResponse(legacy), true);

  assert.equal(isNeighborsResponse({ ...value, contextual_calls: [{ ...call, target: { ...target, index_id: "another-index" } }] }), false);
  assert.equal(isNeighborsResponse({ ...value, contextual_calls: [{ ...call, activation_location: { path: activationSource.path, start_line: 13, end_line: 13 } }] }), false);
  assert.equal(isNeighborsResponse({ ...value, contextual_calls: [{ ...call, source_location: { path: source.path, start_line: 41, end_line: 41 } }] }), false);
  assert.equal(isNeighborsResponse({ ...value, contextual_calls: [{ ...call, receiver_constructor_location: { path: "../private.py", start_line: 1, end_line: 1 } }] }), false);
  assert.equal(isNeighborsResponse({ ...value, contextual_calls: [{ ...call, receiver_constructor_location: { path: "C:/private.py", start_line: 1, end_line: 1 } }] }), false);
  assert.equal(isNeighborsResponse({ ...value, contextual_calls: [{ ...call, field_name: "<script>alert(1)</script>" }] }), false);
  assert.equal(isNeighborsResponse({ ...value, contextual_calls: [{ ...call, activation_edge_id: "missing-edge" }] }), false);
  assert.equal(isNeighborsResponse({
    ...value,
    neighbors: [{ symbol: source, edge: { ...activationEdge, kind: "imports" } }],
  }), false);
  assert.equal(isNeighborsResponse({ ...value, nodes: [{ ...source, qualified_name: "forged.symbol" }] }), false);
  assert.equal(isNeighborsResponse({ ...value, contextual_calls: [call, call] }), false);
  assert.equal(isNeighborsResponse({ ...value, contextual_calls: [{ ...call, binding_mutation_after_escape_unverified: true }] }), true);
  const missingMutationState = { ...call } as Record<string, unknown>;
  delete missingMutationState.binding_mutation_after_escape_unverified;
  assert.equal(isNeighborsResponse({ ...value, contextual_calls: [missingMutationState] }), false);
});

function runImpact() {
  const root = {
    symbol_id: "symbol-root",
    index_id: indexId,
    path: "pkg/app.py",
    qualified_name: "pkg.app.load",
    kind: "function",
    start_line: 4,
    end_line: 7,
  };
  const caller = {
    ...root,
    symbol_id: "symbol-caller",
    qualified_name: "pkg.main.run",
    path: "pkg/main.py",
    start_line: 2,
    end_line: 5,
  };
  const edge = {
    edge_id: "edge-caller-root",
    index_id: indexId,
    source_id: caller.symbol_id,
    target_id: root.symbol_id,
    kind: "calls",
    extraction_method: "fixture",
    source_location: { path: "pkg/main.py", start_line: 4, end_line: 4 },
  };
  return {
    schema_version: SCHEMA_VERSION,
    project_id: "project-impact-test",
    session_id: "session-impact-test",
    run_id: "run-impact-test",
    index_id: indexId,
    candidate_id: "candidate-impact",
    finding_id: null,
    mapping_status: "mapped",
    direction: "callers",
    max_depth: 2,
    root_symbol_id: root.symbol_id,
    root,
    nodes: [root, caller],
    edges: [edge],
    paths: [{ path_id: "impact-path-1", index_id: indexId, symbol_ids: [root.symbol_id, caller.symbol_id], edges: [edge], depth: 1 }],
    unresolved: [],
    layers: [
      {
        kind: "report_anchor",
        determination: "saved_report_symbol",
        symbol_ids: [root.symbol_id],
        unresolved_ids: [],
        summary: "报告锚点不是已确认影响。",
      },
      {
        kind: "static_direct",
        determination: "static_calls",
        symbol_ids: [caller.symbol_id],
        unresolved_ids: [],
        summary: "调用可达不等于已确认影响。",
      },
      {
        kind: "static_indirect",
        determination: "static_calls",
        symbol_ids: [],
        unresolved_ids: [],
        summary: "调用可达不等于已确认影响。",
      },
      {
        kind: "unknown",
        determination: "unresolved_reference",
        symbol_ids: [],
        unresolved_ids: [],
        summary: "它们保持未知。",
      },
    ],
    reachable_node_count: 1,
    returned_node_count: 2,
    scope_note: "Static calls only.",
    truncated: false,
    truncation_reason: null,
    index_truncated: false,
    index_truncation_reason: null,
    index_unresolved_truncated: false,
    index_unresolved_truncation_reason: null,
  };
}

test("index validator accepts explicit unresolved coverage and legacy omission", () => {
  const value = {
    schema_version: SCHEMA_VERSION,
    index: {
      index_id: indexId,
      repository_id: "repo",
      revision: "v1",
      dirty: false,
      snapshot_hash: "hash",
      status: "ready",
    },
    issues: [],
    truncated: false,
    truncation_reason: null,
    unresolved_truncated: true,
    unresolved_truncation_reason: "max_unresolved reached",
  };
  assert.equal(isIndexResponse(value), true);
  const legacy = structuredClone(value);
  delete (legacy as Record<string, unknown>).unresolved_truncated;
  delete (legacy as Record<string, unknown>).unresolved_truncation_reason;
  assert.equal(isIndexResponse(legacy), true);
});

test("index freshness validators keep scan state and refresh mode explicit", () => {
  const freshness = {
    schema_version: SCHEMA_VERSION,
    project_id: "project-freshness",
    baseline_index_id: indexId,
    status: "changed",
    checked_at: "2026-09-15T00:00:00Z",
    added_count: 1,
    modified_count: 2,
    deleted_count: 0,
    changed_paths: ["pkg/changed.py"],
    changed_paths_truncated: false,
    scanned_files: 12,
    scanned_bytes: 2048,
    scan_complete: true,
    scan_truncated: false,
    scan_truncation_reason: null,
    current_rules_version: "python-source-v1+python-3.13+relations-v2",
    current_configuration_hash: "a".repeat(64),
    reasons: ["source_changed"],
    remote_status: "not_applicable",
    remote_source_url: null,
    remote_requested_ref: null,
    remote_resolved_ref: null,
    remote_resolved_commit: null,
  };
  assert.equal(isIndexFreshness(freshness), true);

  const refreshed = {
    schema_version: SCHEMA_VERSION,
    project_id: "project-freshness",
    baseline_index_id: indexId,
    mode: "created",
    index: {
      schema_version: SCHEMA_VERSION,
      index: {
        ...structuredClone(overview()).index,
        index_id: "index-freshness-new",
        snapshot_hash: "b".repeat(64),
      },
      issues: [],
      truncated: false,
      truncation_reason: null,
      unresolved_truncated: false,
      unresolved_truncation_reason: null,
    },
    freshness,
  };
  assert.equal(isIndexRefreshResponse(refreshed), true);

  const historicalReuse = { ...refreshed, mode: "historical_reused" };
  assert.equal(isIndexRefreshResponse(historicalReuse), true);

  const forgedReuse = { ...refreshed, mode: "reused" };
  assert.equal(isIndexRefreshResponse(forgedReuse), false);

  const forgedHistoricalReuse = structuredClone(historicalReuse);
  forgedHistoricalReuse.index.index.index_id = indexId;
  assert.equal(isIndexRefreshResponse(forgedHistoricalReuse), false);
});

test("Run impact validator accepts same-snapshot paths and rejects forged direction", () => {
  assert.equal(isRunImpactResponse(runImpact()), true);
  const broken = structuredClone(runImpact());
  broken.paths[0].edges[0].source_id = broken.root_symbol_id;
  assert.equal(isRunImpactResponse(broken), false);
});

test("Run impact validator accepts an explicitly unmapped empty result", () => {
  const value = runImpact() as any;
  value.candidate_id = null;
  value.finding_id = "finding-impact";
  value.mapping_status = "unmapped";
  value.root_symbol_id = null;
  value.root = null;
  value.nodes = [];
  value.edges = [];
  value.paths = [];
  value.reachable_node_count = 0;
  value.returned_node_count = 0;
  value.layers = value.layers.map((layer: { symbol_ids: string[]; unresolved_ids: string[] }) => ({
    ...layer,
    symbol_ids: [],
    unresolved_ids: [],
  }));
  assert.equal(isRunImpactResponse(value), true);
  value.layers[0].symbol_ids = ["symbol-root"];
  assert.equal(isRunImpactResponse(value), false);
});

test("Run impact validator rejects a symbol placed in the wrong layer", () => {
  const value = runImpact();
  value.layers[1].symbol_ids = [];
  value.layers[2].symbol_ids = ["symbol-caller"];
  assert.equal(isRunImpactResponse(value), false);
});

function explanationReport() {
  return {
    schema_version: SCHEMA_VERSION,
    report: {
      schema_version: SCHEMA_VERSION,
      report_id: "report-understanding",
      run_id: "run-understanding",
      project_id: "project-understanding",
      session_id: "session-understanding",
      index_id: indexId,
      status: "succeeded",
      outcome: "explanation",
      fixture_mode: false,
      summary: "The visible flow is partially explained.",
      graph: {
        index_id: indexId,
        nodes: [],
        static_edges: [],
        unresolved: [],
        candidate_links: [],
        truncated: false,
        truncation_reason: null,
      },
      evidence: [
        {
          evidence_id: "evidence-understanding",
          index_id: indexId,
          path: "pkg/app.py",
          start_line: 4,
          end_line: 5,
          excerpt: "load()",
          content_hash: "content-hash",
          kind: "source",
        },
      ],
      findings: [],
      candidate_links: [],
      explanation: {
        coverage: "partial",
        steps: [
          {
            step_id: "step-entry",
            kind: "confirmed",
            title: "入口",
            description: "源码支持入口步骤。",
            evidence_ids: ["evidence-understanding"],
            symbol_ids: ["symbol-entry"],
          },
          {
            step_id: "step-gap",
            kind: "unknown",
            title: "运行时交接",
            description: "当前快照无法确认。",
            evidence_ids: [],
            symbol_ids: [],
          },
        ],
      },
      limitations: ["静态范围有限。"],
      terminal_sequence: 4,
    },
  };
}

test("Run report validator accepts explanation coverage and rejects invalid completion", () => {
  const value = explanationReport();
  assert.equal(isRunReportResponse(value), true);

  const completeWithUnknown = structuredClone(value);
  completeWithUnknown.report.explanation.coverage = "complete";
  assert.equal(isRunReportResponse(completeWithUnknown), false);

  const missingExplanation = structuredClone(value) as unknown as { report: { explanation: unknown } };
  missingExplanation.report.explanation = null;
  assert.equal(isRunReportResponse(missingExplanation), false);
});

function featureGeneration() {
  const evidence = {
    evidence_id: "evidence-feature",
    index_id: indexId,
    path: "pkg/__main__.py",
    start_line: 1,
    end_line: 2,
    excerpt: "def main():\n    return 1",
    content_hash: "content-hash",
    kind: "source",
  };
  return {
    schema_version: SCHEMA_VERSION,
    generation: {
      schema_version: SCHEMA_VERSION,
      generation_id: "feature-generation-1",
      project_id: "project-overview-test",
      index: overview().index,
      mode: "fixture",
      source: "server",
      external_run_id: null,
      external_session_id: null,
      model: "fixture-feature-v1",
      prompt_version: "feature-summary-v2",
      rules_version: "python-entry-v1",
      source_scope: "entry_reachable",
      status: "succeeded",
      summary: "One bounded feature.",
      features: [
        {
          feature_id: "feature-1",
          index_id: indexId,
          name: "Main flow",
          description: "A feature supported by one observed source range.",
          entry_point_ids: [entryId],
          module_ids: [moduleId],
          symbol_ids: [functionId],
          evidence_ids: [evidence.evidence_id],
          limitations: ["Static evidence only."],
        },
      ],
      associations: [
        {
          association_id: "association-1",
          index_id: indexId,
          feature_id: "feature-1",
          relation: "implemented_by",
          target_kind: "symbol",
          target_id: functionId,
          evidence_ids: [evidence.evidence_id],
        },
      ],
      evidence: [evidence],
      observed_entry_point_ids: [entryId],
      observed_module_ids: [moduleId],
      observed_symbol_ids: [functionId],
      source_paths: [evidence.path],
      omitted_module_ids: [],
      limitations: ["Only queried symbols are included."],
      usage: {
        tool_calls: 3,
        model_calls: 1,
        repair_calls: 0,
        source_lines: 2,
        input_chars: 300,
        output_chars: 500,
        model_input_chars: [300],
        model_output_chars: [500],
        input_tokens: null,
        output_tokens: null,
        elapsed_ms: 10,
      },
      budget: {
        max_tool_calls: 8,
        max_model_calls: 1,
        max_seconds: 20,
        max_source_lines: 120,
        max_total_source_lines: 800,
        max_input_chars: 60_000,
        max_features: 8,
        max_output_chars: 20_000,
      },
      cache_key: "a".repeat(64),
      created_at: "2026-09-11T00:00:00Z",
      error: null,
      truncated: false,
      truncation_reason: null,
    },
  };
}

test("feature generation validator accepts snapshot-linked fixture output", () => {
  assert.equal(isFeatureGenerationResponse(featureGeneration()), true);
});

test("feature generation validator keeps host model claims outside server usage", () => {
  const external = structuredClone(featureGeneration()) as unknown as {
    generation: {
      source: string;
      external_run_id: string | null;
      external_session_id: string | null;
      mode: string;
      source_scope: string;
      usage: {
        model_calls: number;
        repair_calls: number;
        input_chars: number;
        output_chars: number;
        model_input_chars: number[];
        model_output_chars: number[];
        input_tokens: number | null;
        output_tokens: number | null;
      };
    };
  };
  external.generation.source = "external";
  external.generation.mode = "real";
  external.generation.source_scope = "external_run";
  external.generation.usage.model_calls = 0;
  external.generation.usage.repair_calls = 0;
  external.generation.usage.input_chars = 0;
  external.generation.usage.output_chars = 0;
  external.generation.usage.model_input_chars = [];
  external.generation.usage.model_output_chars = [];
  external.generation.usage.input_tokens = null;
  external.generation.usage.output_tokens = null;
  assert.equal(isFeatureGenerationResponse(external), true);

  external.generation.external_run_id = "run-external-1";
  external.generation.external_session_id = "session-external-1";
  assert.equal(isFeatureGenerationResponse(external), true);
  external.generation.external_session_id = null;
  assert.equal(isFeatureGenerationResponse(external), false);
  external.generation.external_session_id = "session-external-1";

  external.generation.usage.model_calls = 1;
  assert.equal(isFeatureGenerationResponse(external), false);
});

test("feature generation validator rejects broken evidence or association references", () => {
  const brokenEvidence = structuredClone(featureGeneration()) as unknown as {
    generation: { features: Array<{ evidence_ids: string[] }> };
  };
  brokenEvidence.generation.features[0].evidence_ids = ["evidence-missing"];
  assert.equal(isFeatureGenerationResponse(brokenEvidence), false);

  const brokenAssociation = structuredClone(featureGeneration()) as unknown as {
    generation: { associations: Array<{ feature_id: string }> };
  };
  brokenAssociation.generation.associations[0].feature_id = "feature-missing";
  assert.equal(isFeatureGenerationResponse(brokenAssociation), false);
});

test("feature history and graph validators keep saved snapshot references explicit", () => {
  const generation = featureGeneration().generation;
  const summary = {
    schema_version: SCHEMA_VERSION,
    generation_id: generation.generation_id,
    project_id: generation.project_id,
    index: generation.index,
    mode: generation.mode,
    source: generation.source,
    external_run_id: generation.external_run_id,
    external_session_id: generation.external_session_id,
    model: generation.model,
    prompt_version: generation.prompt_version,
    rules_version: generation.rules_version,
    source_scope: generation.source_scope,
    status: generation.status,
    summary: generation.summary,
    feature_count: generation.features.length,
    evidence_count: generation.evidence.length,
    usage: generation.usage,
    created_at: generation.created_at,
    error: generation.error,
    truncated: generation.truncated,
    truncation_reason: generation.truncation_reason,
  };
  assert.equal(
    isFeatureGenerationListResponse({
      schema_version: SCHEMA_VERSION,
      project_id: generation.project_id,
      index_id: indexId,
      generations: [summary],
      next_cursor: null,
      has_more: false,
    }),
    true,
  );

  const graph = {
    schema_version: SCHEMA_VERSION,
    project_id: generation.project_id,
    index_id: indexId,
    generation_id: generation.generation_id,
    status: generation.status,
    nodes: [
      {
        node_id: "feature:feature-1",
        index_id: indexId,
        kind: "feature",
        label: "Main flow",
        detail: "A bounded feature.",
        feature_id: "feature-1",
        entry_point_id: null,
        symbol: null,
        path: null,
        start_line: null,
        end_line: null,
        evidence_ids: ["evidence-feature"],
      },
      {
        node_id: `entry:${entryId}`,
        index_id: indexId,
        kind: "entry_point",
        label: "main guard",
        detail: "python_main_guard · python.ast.main_guard",
        feature_id: null,
        entry_point_id: entryId,
        symbol: null,
        path: "pkg/__main__.py",
        start_line: 1,
        end_line: 2,
        evidence_ids: ["evidence-feature"],
      },
      {
        node_id: moduleId,
        index_id: indexId,
        kind: "module",
        label: "pkg.__main__",
        detail: "pkg/__main__.py:L1–L2",
        feature_id: null,
        entry_point_id: null,
        symbol: overview().modules[0].symbol,
        path: null,
        start_line: null,
        end_line: null,
        evidence_ids: ["evidence-feature"],
      },
      {
        node_id: functionId,
        index_id: indexId,
        kind: "symbol",
        label: "pkg.__main__.main",
        detail: "pkg/__main__.py:L1–L2",
        feature_id: null,
        entry_point_id: null,
        symbol: {
          ...overview().modules[0].symbol,
          symbol_id: functionId,
          qualified_name: "pkg.__main__.main",
          kind: "function",
        },
        path: null,
        start_line: null,
        end_line: null,
        evidence_ids: ["evidence-feature"],
      },
    ],
    edges: [
      {
        edge_id: "feature-semantic",
        index_id: indexId,
        source_id: "feature:feature-1",
        target_id: `entry:${entryId}`,
        origin: "semantic",
        relation: "triggered_by",
        evidence_ids: ["evidence-feature"],
      },
      {
        edge_id: "feature-static",
        index_id: indexId,
        source_id: moduleId,
        target_id: functionId,
        origin: "static",
        relation: "calls",
        evidence_ids: [],
      },
    ],
    evidence: generation.evidence,
    unavailable_target_ids: [],
    limitations: [],
    truncated: false,
    truncation_reason: null,
  };
  assert.equal(isFeatureGraphResponse({ schema_version: SCHEMA_VERSION, graph }), true);

  const broken = structuredClone(graph) as typeof graph;
  broken.edges[0].origin = "static";
  assert.equal(isFeatureGraphResponse({ schema_version: SCHEMA_VERSION, graph: broken }), false);
});

function sessionPayload() {
  const index = overview().index;
  const session = {
    schema_version: SCHEMA_VERSION,
    session_id: "session-one",
    project_id: "project-overview-test",
    index,
    feature_generation_id: null,
    topic: "Why does the timeout path fail?",
    created_at: "2026-09-12T00:00:00Z",
    updated_at: "2026-09-12T00:00:00Z",
    message_count: 1,
  };
  const message = {
    schema_version: SCHEMA_VERSION,
    message_id: "message-one",
    session_id: session.session_id,
    sequence: 1,
    source: "human",
    content: "Trace the timeout configuration.",
    created_at: "2026-09-12T00:01:00Z",
  };
  return {
    session,
    message,
    list: {
      schema_version: SCHEMA_VERSION,
      project_id: session.project_id,
      sessions: [session],
      next_cursor: null,
      has_more: false,
    },
    messages: {
      schema_version: SCHEMA_VERSION,
      project_id: session.project_id,
      session_id: session.session_id,
      messages: [message],
      next_cursor: null,
      has_more: false,
    },
  };
}

test("session validators keep project, snapshot, and sequence boundaries explicit", () => {
  const payload = sessionPayload();
  assert.equal(isSessionResponse({ schema_version: SCHEMA_VERSION, session: payload.session }), true);
  assert.equal(isSessionListResponse(payload.list), true);
  assert.equal(isSessionMessagesResponse(payload.messages), true);

  const crossProject = structuredClone(payload.list);
  crossProject.sessions[0].project_id = "project-other";
  assert.equal(isSessionListResponse(crossProject), false);

  const reverseSequence = structuredClone(payload.messages);
  reverseSequence.messages.push({ ...payload.message, message_id: "message-two", sequence: 0 });
  assert.equal(isSessionMessagesResponse(reverseSequence), false);
});

test("trash validator accepts recoverable projects and sessions only", () => {
  const payload = sessionPayload();
  const project = {
    schema_version: SCHEMA_VERSION,
    project_id: "project-removed",
    name: "Recoverable project",
    repository_id: "repo-one",
    created_at: "2026-09-12T00:00:00Z",
    available_index_ids: [indexId],
    latest_index_id: indexId,
    building_index_id: null,
  };
  const trash = { schema_version: SCHEMA_VERSION, projects: [project], sessions: [payload.session] };
  assert.equal(isTrashListResponse(trash), true);
  assert.equal(isTrashListResponse({ ...trash, sessions: [{ ...payload.session, message_count: "1" }] }), false);
});

test("project neighbor requests support a direct one-hop depth and retain the default", async () => {
  const originalFetch = globalThis.fetch;
  const depths: string[] = [];
  const root = {
    symbol_id: moduleId,
    index_id: indexId,
    path: "pkg/mod.py",
    qualified_name: "pkg.mod",
    kind: "module",
    start_line: 1,
    end_line: 20,
  };
  const payload = {
    schema_version: SCHEMA_VERSION,
    index_id: indexId,
    root,
    nodes: [root],
    neighbors: [],
    unresolved: [],
    truncated: false,
    truncation_reason: null,
    index_truncated: false,
    index_truncation_reason: null,
    index_unresolved_truncated: false,
    index_unresolved_truncation_reason: null,
  };
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    depths.push(new URL(String(input), "http://localhost").searchParams.get("depth") ?? "");
    return new Response(JSON.stringify(payload), { status: 200 });
  }) as typeof fetch;

  try {
    await getProjectNeighbors("project-a", indexId, moduleId, "callees", ["imports"], undefined, 1);
    await getProjectNeighbors("project-a", indexId, moduleId, "both", ["calls"]);
    assert.deepEqual(depths, ["1", "2"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("project session Run requests send the selected analysis kind", async () => {
  const originalFetch = globalThis.fetch;
  const capturedBodies: Record<string, unknown>[] = [];
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    capturedBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
    return new Response(JSON.stringify({
      schema_version: SCHEMA_VERSION,
      run_id: "run-understanding",
      project_id: "project-a",
      session_id: "session-a",
      index_id: indexId,
      status: "queued",
    }), { status: 200 });
  }) as typeof fetch;

  try {
    await createProjectSessionRun("project-a", "session-a", {
      indexId,
      issueId: "understanding-session-a",
      title: "解释代码",
      body: "解释这个功能的流程",
      mode: "real",
      analysisKind: "understanding",
    });
    assert.equal(capturedBodies[0]?.analysis_kind, "understanding");
    assert.equal(capturedBodies[0]?.mode, "real");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("requestJson applies an explicit timeout to import-specific callers", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = ((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
  })) as typeof fetch;

  try {
    await assert.rejects(
      requestJson("/api/imports/local", (value): value is object => Boolean(value), { timeoutMs: 5 }),
      (error: unknown) => error instanceof RequestTimeoutError,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("requestJson preserves a timeout when response headers arrive before the body", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    const response = new Response("{}", { status: 200 });
    response.json = () => new Promise<unknown>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
    });
    return response;
  }) as typeof fetch;

  try {
    await assert.rejects(
      requestJson("/api/imports/local", (value): value is object => Boolean(value), { timeoutMs: 5 }),
      (error: unknown) => error instanceof RequestTimeoutError,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("GitHub job submissions use a short per-request timeout", async () => {
  assert.equal(GITHUB_REQUEST_TIMEOUT_MS, 30_000);
  assert.equal(DEFAULT_GITHUB_REQUEST_TIMEOUT_MS, 30_000);
  assert.equal(parseGitHubRequestTimeoutMs(undefined), DEFAULT_GITHUB_REQUEST_TIMEOUT_MS);
  assert.equal(parseGitHubRequestTimeoutMs(String(DEFAULT_GITHUB_REQUEST_TIMEOUT_MS + 60_000)), DEFAULT_GITHUB_REQUEST_TIMEOUT_MS + 60_000);
  assert.equal(parseGitHubRequestTimeoutMs("320000"), 320_000);
  assert.equal(parseGitHubRequestTimeoutMs("1"), 1);
  assert.equal(parseGitHubRequestTimeoutMs("2147483647"), 2_147_483_647);
  assert.equal(parseGitHubRequestTimeoutMs("2147483648"), DEFAULT_GITHUB_REQUEST_TIMEOUT_MS);
  assert.equal(parseGitHubRequestTimeoutMs("0"), DEFAULT_GITHUB_REQUEST_TIMEOUT_MS);
  assert.equal(parseGitHubRequestTimeoutMs("-1"), DEFAULT_GITHUB_REQUEST_TIMEOUT_MS);
  assert.equal(parseGitHubRequestTimeoutMs("Infinity"), DEFAULT_GITHUB_REQUEST_TIMEOUT_MS);
  assert.equal(parseGitHubRequestTimeoutMs("1.5"), DEFAULT_GITHUB_REQUEST_TIMEOUT_MS);
  assert.equal(parseGitHubRequestTimeoutMs("invalid"), DEFAULT_GITHUB_REQUEST_TIMEOUT_MS);

  const originalFetch = globalThis.fetch;
  const originalSetTimeout = globalThis.setTimeout;
  const timeoutDelays: number[] = [];
  type TimeoutCallback = Parameters<typeof globalThis.setTimeout>[0];
  globalThis.setTimeout = ((callback: TimeoutCallback, timeout?: number) => {
    timeoutDelays.push(Number(timeout));
    return originalSetTimeout(callback, 0);
  }) as typeof globalThis.setTimeout;
  globalThis.fetch = ((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    const signal = init?.signal;
    signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
  })) as typeof fetch;

  try {
    const results = await Promise.allSettled([
      importGitHubRepository("project", "https://github.com/org/repo"),
      retryGitHubImport("attempt"),
      refreshGitHubSource("project", "index"),
    ]);
    assert.deepEqual(results.map((result) => result.status), ["rejected", "rejected", "rejected"]);
    assert.deepEqual(timeoutDelays, [
      GITHUB_REQUEST_TIMEOUT_MS,
      GITHUB_REQUEST_TIMEOUT_MS,
      GITHUB_REQUEST_TIMEOUT_MS,
    ]);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.fetch = originalFetch;
  }
});

test("job polling and cancellation use project scoped routes when available", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; method: string }> = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), method: init?.method ?? "GET" });
    return Response.json({
      schema_version: SCHEMA_VERSION, job_id: "job-1", project_id: "project-1", kind: "github_refresh",
      status: "cancelled", phase: "cancelled", progress: {}, attempts: [], result: null,
      error_code: null, summary: null, created_at: "now", updated_at: "now", deadline_at: "later",
      execution_deadline_at: null, merged_into: null,
    });
  }) as typeof fetch;
  try {
    await getJob("job-1", "project-1");
    await cancelJob("job-1", "project-1");
    assert.deepEqual(calls.map((call) => call.url), [
      "http://localhost:8000/api/projects/project-1/jobs/job-1",
      "http://localhost:8000/api/projects/project-1/jobs/job-1/cancel",
    ]);
    assert.deepEqual(calls.map((call) => call.method), ["GET", "POST"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("import recovery can bound opening a ready snapshot without changing default index reads", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = ((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
  })) as typeof fetch;

  try {
    await assert.rejects(
      openProjectIndex("project-a", "index-a", undefined, 5),
      (error: unknown) => error instanceof RequestTimeoutError,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
