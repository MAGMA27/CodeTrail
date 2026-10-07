import assert from "node:assert/strict";
import test from "node:test";

import type {
  CodeEdge,
  OverviewClassificationCounts,
  OverviewGroup,
  OverviewHttpAssociation,
  OverviewModule,
  OverviewModuleRelation,
  ProjectOverview,
  SymbolRecord,
} from "./api.ts";
import { ARCHITECTURE_MODULE_LIMIT, buildArchitectureGraph } from "./architecture-graph.ts";

const indexId = "index-architecture-test";

function moduleRecord(id: string, path: string, entryReachable = false, classificationCounts: Partial<OverviewClassificationCounts> = {}): OverviewModule {
  const symbol: SymbolRecord = {
    symbol_id: id,
    index_id: indexId,
    path,
    qualified_name: path.replaceAll("/", ".").replace(/\.py$/, ""),
    kind: "module",
    start_line: 1,
    end_line: 40,
  };
  return {
    module_id: id,
    index_id: indexId,
    symbol,
    entry_point_ids: [],
    entry_reachable: entryReachable,
    classification_counts: { application_entry: 0, public_api: 0, extension_point: 0, unknown: 0, ...classificationCounts },
  };
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
    classifications: [],
    classification_coverage: {
      total: 0,
      visible: 0,
      category_counts: { application_entry: 0, public_api: 0, extension_point: 0, unknown: 0 },
      visible_category_counts: { application_entry: 0, public_api: 0, extension_point: 0, unknown: 0 },
      truncated: false,
    },
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

test("folded directories summarize returned module classifications without adding graph nodes", () => {
  const first = moduleRecord("class-a", "src/a.py", false, { application_entry: 1, public_api: 2 });
  const second = moduleRecord("class-b", "src/b.py", false, { extension_point: 1, unknown: 1 });
  const value = overview([first, second], [directory("group-src", "src", [first.module_id, second.module_id])], []);

  const folded = buildArchitectureGraph(value);
  assert.deepEqual(folded.directories[0].classificationCounts, {
    application_entry: 1,
    public_api: 2,
    extension_point: 1,
    unknown: 1,
  });
  assert.equal(folded.directories.length, 1);
  assert.equal(folded.visibleModuleCount, 0);
  assert.deepEqual(folded.relations, []);

  const expanded = buildArchitectureGraph(value, { expandedGroupIds: ["group-src"] });
  assert.deepEqual(expanded.directories[0].visibleModules.map(({ module }) => module.classification_counts), [
    { application_entry: 1, public_api: 2, extension_point: 0, unknown: 0 },
    { application_entry: 0, public_api: 0, extension_point: 1, unknown: 1 },
  ]);
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

test("an expanded selected responsibility gets module slots before earlier expanded groups", () => {
  const makeRole = (role: string, count: number) => {
    const modules = Array.from({ length: count }, (_, index) =>
      moduleRecord(`${role}-${index}`, `${role}/mod${index}.py`),
    );
    return {
      modules,
      group: { ...directory(`role-${role}`, role, modules.map((module) => module.module_id)), group_kind: "responsibility" as const },
    };
  };
  const first = makeRole("first", 30);
  const second = makeRole("second", 15);
  const selected = makeRole("selected", 10);
  const value = overview(
    [...first.modules, ...second.modules, ...selected.modules],
    [first.group, second.group, selected.group],
    [],
  );
  const graph = buildArchitectureGraph(value, {
    expandedGroupIds: [first.group.group_id, second.group.group_id, selected.group.group_id],
    selectedGroupId: selected.group.group_id,
  });

  assert.equal(graph.visibleModuleCount, ARCHITECTURE_MODULE_LIMIT);
  assert.equal(graph.directories.find((item) => item.group.group_id === selected.group.group_id)?.visibleModules.length, 10);
  assert.equal(graph.directories.find((item) => item.group.group_id === selected.group.group_id)?.omittedByCanvasLimit, 0);
  assert.equal(graph.directories.find((item) => item.group.group_id === first.group.group_id)?.visibleModules.length, 30);
  assert.equal(graph.directories.find((item) => item.group.group_id === second.group.group_id)?.visibleModules.length, 0);

  const unselected = buildArchitectureGraph(value, {
    expandedGroupIds: [first.group.group_id, second.group.group_id, selected.group.group_id],
  });
  assert.equal(unselected.directories.find((item) => item.group.group_id === first.group.group_id)?.visibleModules.length, 30);
  assert.equal(unselected.directories.find((item) => item.group.group_id === second.group.group_id)?.visibleModules.length, 10);
  assert.equal(unselected.directories.find((item) => item.group.group_id === selected.group.group_id)?.visibleModules.length, 0);
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

test("responsibility focus keeps only filtered bidirectional one-hop groups and real relations", () => {
  const c1 = moduleRecord("c1", "storage/db.py");
  const d1 = moduleRecord("d1", "workers/job.py");
  const value = overview(
    [a1, b1, c1, d1],
    [
      { ...directory("role-api", "api", [a1.module_id]), group_kind: "responsibility", group_key: "api", label: "API" },
      { ...directory("role-web", "web", [b1.module_id]), group_kind: "responsibility", group_key: "web", label: "Web" },
      { ...directory("role-storage", "storage", [c1.module_id]), group_kind: "responsibility", group_key: "storage", label: "Storage" },
      { ...directory("role-worker", "workers", [d1.module_id]), group_kind: "responsibility", group_key: "workers", label: "Workers" },
    ],
    [
      relation("api-web", a1, b1, ["imports"], [codeEdge("api-web-import", a1.module_id, b1.module_id, "imports", a1.symbol.path)]),
      relation("storage-api", c1, a1, ["calls"], [codeEdge("storage-api-call", c1.module_id, a1.module_id, "calls", c1.symbol.path)]),
      relation("web-storage", b1, c1, ["calls"], [codeEdge("web-storage-call", b1.module_id, c1.module_id, "calls", b1.symbol.path)]),
      relation("storage-worker", c1, d1, ["imports"], [codeEdge("storage-worker-import", c1.module_id, d1.module_id, "imports", c1.symbol.path)]),
    ],
  );

  const importsOnly = buildArchitectureGraph(value, {
    focusedGroupId: "role-api",
    relationKinds: ["imports"],
    showHttpAssociations: false,
  });
  assert.deepEqual(importsOnly.directories.map(({ group }) => group.group_id), ["role-api", "role-web"]);
  assert.deepEqual(importsOnly.relations.map(({ id }) => id), [`architecture:${JSON.stringify(["role-api", "role-web"])}`]);
  assert.equal(importsOnly.focusHiddenGroupCount, 2);

  const bothKinds = buildArchitectureGraph(value, {
    focusedGroupId: "role-api",
    relationKinds: ["imports", "calls"],
    showHttpAssociations: false,
  });
  assert.deepEqual(bothKinds.directories.map(({ group }) => group.group_id), ["role-api", "role-storage", "role-web"]);
  assert.equal(bothKinds.relations.length, 3);
  assert.equal(bothKinds.relations.some(({ source, target }) => source === "role-storage" && target === "role-worker"), false);
  assert.equal(bothKinds.focusHiddenGroupCount, 1);
  assert.equal(bothKinds.focusHiddenRelationCount, 1);
  assert.equal(bothKinds.focusHiddenHttpAssociationCount, 0);
});

test("HTTP focus neighbors stay independent from static filters and preserve folded endpoints", () => {
  const value = overview(
    [a1, b1],
    [
      { ...directory("role-api", "api", [a1.module_id]), group_kind: "responsibility", group_key: "api", label: "API" },
      { ...directory("role-web", "web", [b1.module_id]), group_kind: "responsibility", group_key: "web", label: "Web" },
    ],
    [],
    [httpAssociation("http-api-web", a1, b1)],
  );
  const enabled = buildArchitectureGraph(value, { focusedGroupId: "role-api", showHttpAssociations: true });
  assert.deepEqual(enabled.directories.map(({ group }) => group.group_id), ["role-api", "role-web"]);
  assert.equal(enabled.relations.length, 0);
  assert.equal(enabled.httpRelations.length, 1);
  assert.equal(enabled.httpRelations[0].source, "role-api");
  assert.equal(enabled.httpRelations[0].target, "role-web");

  const disabled = buildArchitectureGraph(value, { focusedGroupId: "role-api", showHttpAssociations: false });
  assert.deepEqual(disabled.directories.map(({ group }) => group.group_id), ["role-api"]);
  assert.equal(disabled.httpRelations.length, 0);
  assert.equal(disabled.focusHiddenGroupCount, 1);
  assert.equal(disabled.focusHiddenHttpAssociationCount, 0);
});

test("focused adjacency uses available module ownership before the 40-module canvas allocation", () => {
  const modules = Array.from({ length: ARCHITECTURE_MODULE_LIMIT }, (_, index) =>
    moduleRecord(`large-${index}`, `src/mod${index.toString().padStart(2, "0")}.py`),
  );
  const hiddenSource = moduleRecord("large-hidden", "src/z-hidden.py");
  const value = overview(
    [...modules, hiddenSource, b1],
    [
      { ...directory("role-large", "src", [...modules, hiddenSource].map((module) => module.module_id)), group_kind: "responsibility", group_key: "src", label: "Large" },
      { ...directory("role-web", "web", [b1.module_id]), group_kind: "responsibility", group_key: "web", label: "Web" },
    ],
    [relation("hidden-to-web", hiddenSource, b1, ["imports"], [codeEdge("hidden-import", hiddenSource.module_id, b1.module_id, "imports", hiddenSource.symbol.path)])],
  );

  const model = buildArchitectureGraph(value, {
    expandedGroupIds: ["role-large"],
    focusedGroupId: "role-large",
    selectedGroupId: "role-large",
  });
  assert.equal(model.visibleModuleCount, ARCHITECTURE_MODULE_LIMIT);
  assert.deepEqual(model.directories.map(({ group }) => group.group_id), ["role-large", "role-web"]);
  assert.equal(model.relations[0].source, "role-large");
  assert.equal(model.relations[0].target, "role-web");
  assert.equal(model.focusHiddenGroupCount, 0);
  assert.equal(model.focusHiddenRelationCount, 0);
});
