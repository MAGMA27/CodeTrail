import assert from "node:assert/strict";
import test from "node:test";

import type {
  CodeEdge,
  OverviewGroup,
  OverviewHttpAssociation,
  OverviewModule,
  OverviewModuleRelation,
  ProjectOverview,
  SymbolRecord,
} from "./api.ts";
import { ARCHITECTURE_MODULE_LIMIT, buildArchitectureGraph } from "./architecture-graph.ts";

const indexId = "index-architecture-test";

function moduleRecord(id: string, path: string, entryReachable = false): OverviewModule {
  const symbol: SymbolRecord = {
    symbol_id: id,
    index_id: indexId,
    path,
    qualified_name: path.replaceAll("/", ".").replace(/\.py$/, ""),
    kind: "module",
    start_line: 1,
    end_line: 40,
  };
  return { module_id: id, index_id: indexId, symbol, entry_point_ids: [], entry_reachable: entryReachable };
}

function directory(id: string, path: string, moduleIds: string[]): OverviewGroup {
  return {
    group_id: id,
    index_id: indexId,
    group_kind: "source_directory",
    group_key: path,
    label: `SOURCE ${path}`,
    module_ids: moduleIds,
    entry_point_ids: [],
  };
}

function codeEdge(id: string, source: string, target: string, kind: "imports" | "calls", path: string): CodeEdge {
  return {
    edge_id: id,
    index_id: indexId,
    source_id: source,
    target_id: target,
    kind,
    extraction_method: "ast",
    source_location: { path, start_line: 12, end_line: 12 },
  };
}

function relation(
  id: string,
  source: OverviewModule,
  target: OverviewModule,
  kinds: Array<"imports" | "calls">,
  edges: CodeEdge[],
): OverviewModuleRelation {
  return {
    relation_id: id,
    index_id: indexId,
    source_module_id: source.module_id,
    target_module_id: target.module_id,
    source_module: source.symbol,
    target_module: target.symbol,
    relation_kinds: kinds,
    edge_count: edges.length,
    edges,
  };
}

function httpAssociation(id: string, client: OverviewModule, server: OverviewModule): OverviewHttpAssociation {
  const evidence = (module: OverviewModule, side: string) => ({
    evidence_id: `evidence-${id}-${side}`,
    index_id: indexId,
    kind: "source" as const,
    path: module.symbol.path,
    start_line: 3,
    end_line: 5,
    excerpt: "request or route",
    content_hash: "hash",
  });
  return {
    association_id: id,
    index_id: indexId,
    match_kind: "method_path_shape",
    method: "GET",
    path: "/api/health",
    client_path_kind: "literal",
    client_path_template: null,
    client_name: "getHealth",
    client_module: client.symbol,
    server_module: server.symbol,
    route_entry_point_id: "route-health",
    client_evidence: evidence(client, "client"),
    route_evidence: evidence(server, "route"),
  };
}

function overview(
  modules: OverviewModule[],
  groups: OverviewGroup[],
  moduleRelations: OverviewModuleRelation[],
  httpAssociations: OverviewHttpAssociation[] = [],
): ProjectOverview {
  return {
    schema_version: "0.1.0",
    project_id: "project-architecture-test",
    index: {
      index_id: indexId,
      repository_id: "repo-test",
      revision: "fixture",
      snapshot_hash: "snapshot-test",
      status: "ready",
      dirty: false,
      configuration_hash: null,
      rules_version: "python-entry-v5+http-shape-v1",
    },
    rules_version: "python-entry-v5+http-shape-v1",
    entry_status: "identified",
    responsibility_status: groups.some((group) => group.group_kind === "responsibility") ? "ready" : "not_configured",
    responsibility_evidence: groups.some((group) => group.group_kind === "responsibility") ? {
      evidence_id: "evidence-manifest",
      index_id: indexId,
      path: "codetrail-subsystems.json",
      start_line: 1,
      end_line: 1,
      excerpt: "{}",
      content_hash: "hash",
      kind: "source",
    } : null,
    entries: [],
    groups,
    modules,
    module_relations: moduleRelations,
    http_associations: httpAssociations,
    http_association_coverage: {
      analysis_status: "complete",
      path_analysis: "parameterized",
      client_request_count: httpAssociations.length,
      matched_count: httpAssociations.length,
      visible_match_count: httpAssociations.length,
      unmatched_count: 0,
      ambiguous_count: 0,
      unknown_count: 0,
    },
    paths: [],
    unresolved: [],
    coverage: {
      total_modules: modules.length,
      visible_modules: modules.length,
      reachable_modules: 0,
      unreachable_modules: modules.length,
      entry_point_count: 0,
      unresolved_count: 0,
      total_groups: groups.length,
      visible_groups: groups.length,
      total_module_relations: moduleRelations.length,
      visible_module_relations: moduleRelations.length,
    },
    notes: [],
    truncated: false,
    truncation_reason: null,
  };
}

const a1 = moduleRecord("a1", "apps/api/a.py", true);
const a2 = moduleRecord("a2", "apps/api/b.py");
const b1 = moduleRecord("b1", "apps/web/c.py");
const baseOverview = overview(
  [a1, a2, b1],
  [
    directory("group-api", "apps/api", [a1.module_id, a2.module_id]),
    directory("group-web", "apps/web", [b1.module_id]),
    {
      ...directory("route-api", "/api", [a1.module_id]),
      group_kind: "http_path",
      entry_point_ids: ["entry-api"],
    },
  ],
  [
    relation("relation-internal", a1, a2, ["imports", "calls"], [
      codeEdge("internal-import", a1.module_id, a2.module_id, "imports", a1.symbol.path),
      codeEdge("internal-call", a1.module_id, a2.module_id, "calls", a1.symbol.path),
    ]),
    relation("relation-api-web-one", a1, b1, ["imports"], [
      codeEdge("api-web-one", a1.module_id, b1.module_id, "imports", a1.symbol.path),
    ]),
    relation("relation-api-web-two", a2, b1, ["calls"], [
      codeEdge("api-web-two", a2.module_id, b1.module_id, "calls", a2.symbol.path),
    ]),
  ],
);

test("folded directories aggregate unique module-pair relations and internal edges once", () => {
  const model = buildArchitectureGraph(baseOverview);
  assert.equal(model.directories.length, 2);
  assert.equal(model.visibleModuleCount, 0);
  assert.equal(model.directories[0].internalRelationCount, 1);
  assert.equal(model.directories[0].internalEdgeCount, 2);
  const external = model.relations.find((item) => item.source === "group-api" && item.target === "group-web");
  assert.equal(external?.relationCount, 2);
  assert.equal(external?.edgeCount, 2);
  assert.deepEqual(external?.relationKinds, ["calls", "imports"]);
  assert.equal(model.relations.every((item) => item.source !== item.target), true);
});

test("responsibility containers preserve source-to-target dependency direction", () => {
  const roles = overview(
    [a1, b1],
    [
      { ...directory("role-api", "api", [a1.module_id]), group_kind: "responsibility", group_key: "api", label: "API" },
      { ...directory("role-web", "web", [b1.module_id]), group_kind: "responsibility", group_key: "web", label: "Web" },
    ],
    [relation("api-to-web", a1, b1, ["imports"], [codeEdge("api-imports-web", a1.module_id, b1.module_id, "imports", a1.symbol.path)])],
  );

  const folded = buildArchitectureGraph(roles);
  assert.deepEqual(folded.directories.map((item) => item.group.label), ["API", "Web"]);
  assert.equal(folded.relations[0].source, "role-api");
  assert.equal(folded.relations[0].target, "role-web");

  const apiExpanded = buildArchitectureGraph(roles, { expandedGroupIds: ["role-api"] });
  assert.equal(apiExpanded.relations[0].source, "a1");
  assert.equal(apiExpanded.relations[0].target, "role-web");
});

test("empty or relation-free overviews do not fabricate architecture nodes or edges", () => {
  const empty = buildArchitectureGraph(overview([], [], []));
  assert.deepEqual(empty.directories, []);
  assert.deepEqual(empty.relations, []);
  const relationFree = buildArchitectureGraph(overview([a1], [directory("group-api", "apps/api", [a1.module_id])], []));
  assert.equal(relationFree.directories.length, 1);
  assert.deepEqual(relationFree.relations, []);
});

test("expanded modules keep valid endpoints and the global 40-node ceiling", () => {
  const expanded = buildArchitectureGraph(baseOverview, { expandedGroupIds: ["group-api"] });
  assert.deepEqual(expanded.directories[0].visibleModules.map(({ module }) => module.module_id), ["a1", "a2"]);
  assert.equal(expanded.relations.some((item) => item.source === "a1" && item.target === "a2"), true);
  assert.equal(expanded.relations.every((item) => {
    const ids = new Set([
      ...expanded.directories.map((directory) => directory.group.group_id),
      ...expanded.directories.flatMap((directory) => directory.visibleModules.map(({ module }) => module.module_id)),
    ]);
    return ids.has(item.source) && ids.has(item.target);
  }), true);

  const modules = Array.from({ length: ARCHITECTURE_MODULE_LIMIT + 5 }, (_, index) =>
    moduleRecord(`module-${index}`, `src/mod${index}.py`),
  );
  const large = overview(modules, [directory("group-large", "src", modules.map((module) => module.module_id))], []);
  const bounded = buildArchitectureGraph(large, { expandedGroupIds: ["group-large"] });
  assert.equal(bounded.visibleModuleCount, ARCHITECTURE_MODULE_LIMIT);
  assert.equal(bounded.directories[0].omittedByCanvasLimit, 5);
});

test("HTTP entry focus highlights its source module without changing static relation counts", () => {
  const model = buildArchitectureGraph(baseOverview, {
    expandedGroupIds: ["group-api"],
    selectedHttpGroupId: "route-api",
    relationKinds: ["imports"],
  });
  assert.equal(model.directories.find((item) => item.group.group_id === "group-api")?.highlighted, true);
  assert.equal(model.directories.find((item) => item.group.group_id === "group-web")?.highlighted, false);
  assert.equal(model.focusNodeIds.includes("group-api"), true);
  assert.equal(model.focusNodeIds.includes("a1"), true);
  assert.equal(model.focusNodeIds.includes("group-web"), false);
  const mixed = model.relations.find((item) => item.source === "a1" && item.target === "a2");
  assert.deepEqual(mixed?.relationKinds, ["calls", "imports"]);
  assert.equal(mixed?.edgeCount, 2);
  assert.equal(mixed?.highlighted, true);
  assert.equal(model.relations.some((item) => item.source === "a2" && item.target === "group-web"), false);
});

test("HTTP shape edges stay separate from imports/calls and map through folded or expanded groups", () => {
  const value = overview(
    [a1, a2, b1],
    [directory("group-api", "apps/api", [a1.module_id, a2.module_id]), directory("group-web", "apps/web", [b1.module_id])],
    [relation("relation-api-web-one", a1, b1, ["imports"], [codeEdge("edge-api-web", a1.module_id, b1.module_id, "imports", a1.symbol.path)])],
    [httpAssociation("http-api-web", b1, a1)],
  );
  const folded = buildArchitectureGraph(value);
  assert.equal(folded.relations.length, 1);
  assert.equal(folded.httpRelations.length, 1);
  assert.equal(folded.httpRelations[0].source, "group-web");
  assert.equal(folded.httpRelations[0].target, "group-api");
  assert.equal(folded.httpRelations[0].labels[0], "GET /api/health");
  assert.equal(buildArchitectureGraph(value, { showHttpAssociations: false }).httpRelations.length, 0);

  const expanded = buildArchitectureGraph(value, { expandedGroupIds: ["group-web"] });
  assert.equal(expanded.httpRelations[0].source, b1.module_id);
  assert.equal(expanded.httpRelations[0].target, "group-api");
  assert.equal(expanded.httpRelations[0].representative?.client_evidence.path, b1.symbol.path);
});

test("HTTP associations count endpoints hidden by the 40-module canvas ceiling", () => {
  const modules = Array.from({ length: ARCHITECTURE_MODULE_LIMIT }, (_, index) =>
    moduleRecord(`large-${index}`, `src/mod${index}.ts`),
  );
  const source = moduleRecord("large-hidden", "src/z-hidden.ts");
  const value = overview(
    [...modules, source, b1],
    [directory("group-large", "src", [...modules, source].map((module) => module.module_id)), directory("group-web", "apps/web", [b1.module_id])],
    [],
    [httpAssociation("http-hidden-endpoint", source, b1)],
  );
  const model = buildArchitectureGraph(value, { expandedGroupIds: ["group-large"] });
  assert.equal(model.summarizedHttpAssociationCount, 1);
  assert.equal(model.omittedHttpAssociationCount, 0);
  assert.equal(model.httpRelations[0].source, "group-large");
  assert.equal(model.httpRelations[0].target, "group-web");
});
