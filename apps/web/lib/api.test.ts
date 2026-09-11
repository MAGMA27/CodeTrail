import assert from "node:assert/strict";
import test from "node:test";

import { isProjectOverviewResponse, SCHEMA_VERSION } from "./api.ts";

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
