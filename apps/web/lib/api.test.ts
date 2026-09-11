import assert from "node:assert/strict";
import test from "node:test";

import {
  isFeatureGenerationListResponse,
  isFeatureGenerationResponse,
  isFeatureGraphResponse,
  isProjectOverviewResponse,
  isSessionListResponse,
  isSessionMessagesResponse,
  isSessionResponse,
  SCHEMA_VERSION,
} from "./api.ts";

const indexId = "index-overview-test";
const moduleId = "symbol-module";
const functionId = "symbol-main";
const entryId = "entry-main";

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
      },
    ],
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
    },
    notes: [],
    truncated: false,
    truncation_reason: null,
  };
}

test("project overview validator accepts bounded snapshot-linked data", () => {
  assert.equal(isProjectOverviewResponse(overview()), true);
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
