import assert from "node:assert/strict";
import test from "node:test";

import { isFeatureGenerationResponse, isProjectOverviewResponse, SCHEMA_VERSION } from "./api.ts";

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
