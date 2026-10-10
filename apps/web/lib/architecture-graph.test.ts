import assert from "node:assert/strict";
import test from "node:test";

import type {
  CodeEdge,
  OverviewClassificationCounts,
  OverviewGroup,
  OverviewHttpAssociation,
  OverviewEntryPoint,
  OverviewModule,
  OverviewModuleRelation,
  ProjectOverview,
  SymbolRecord,
} from "./api.ts";
import { ARCHITECTURE_MODULE_LIMIT, architectureEdgeSides, architectureEntrySummary, architectureOverviewEdgeVisible, architectureRelationEdgeType, architectureRelationsForFocusedModule, buildArchitectureGraph } from "./architecture-graph.ts";

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

function entryPoint(entryPointId: string, kind: OverviewEntryPoint["kind"]): OverviewEntryPoint {
  const handler: SymbolRecord | null = kind === "fastapi_route" ? {
    symbol_id: `handler-${entryPointId}`,
    index_id: indexId,
    path: "src/main.py",
    qualified_name: "main.health",
    kind: "function",
    start_line: 10,
    end_line: 12,
  } : null;
  return {
    entry_point_id: entryPointId,
    index_id: indexId,
    path: "src/main.py",
    start_line: 10,
    end_line: 12,
    kind,
    rule_id: kind,
    label: entryPointId,
    symbol_id: handler?.symbol_id ?? "main-module",
    http_method: kind === "fastapi_route" ? "GET" : null,
    declared_path: kind === "fastapi_route" ? "/health" : null,
    handler_symbol: handler,
    evidence: null,
    evidence_error: null,
    direct_symbol_ids: [],
    unresolved_ids: [],
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
  entries: OverviewEntryPoint[] = [],
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
    entries,
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

test("architecture containers label entry owners, not every reachable module", () => {
  const module = moduleRecord("main-module", "src/main.py");
  module.entry_point_ids = ["main-entry", "guard-entry", "missing-entry"];
  const reachableModule = moduleRecord("reachable-helper", "src/helper.py", true);
  reachableModule.entry_point_ids = ["route-entry", "main-entry", "guard-entry", "missing-entry"];
  const unmappedEntry = { ...entryPoint("unmapped-entry", "python_main_guard"), path: "src/unmapped.py", symbol_id: "absent-module" };
  const role = { ...directory("role-main", "main", [module.module_id, reachableModule.module_id]), group_kind: "responsibility" as const, group_key: "main", label: "Main", entry_point_ids: ["route-entry", "main-entry", "unmapped-entry", "missing-entry"] };
  const value = overview([module, reachableModule], [role], [], [], [
    entryPoint("route-entry", "fastapi_route"),
    entryPoint("main-entry", "python_main_module"),
    entryPoint("guard-entry", "python_main_guard"),
    unmappedEntry,
  ]);

  const folded = buildArchitectureGraph(value);
  assert.deepEqual(folded.directories[0].entryPointCounts, {
    fastapi_route: 1,
    python_main_module: 1,
    python_main_guard: 1,
    unmapped: 1,
    unreturned: 1,
  });
  assert.equal(architectureEntrySummary(folded.directories[0].entryPointCounts), "HTTP入口 1 · Python启动文件 1 · __main__启动块 1 · 入口模块未映射 1 · 入口明细未返回 1");

  const expanded = buildArchitectureGraph(value, { expandedGroupIds: [role.group_id] });
  const mainView = expanded.directories[0].visibleModules.find(({ module: item }) => item.module_id === module.module_id)!;
  const helperView = expanded.directories[0].visibleModules.find(({ module: item }) => item.module_id === reachableModule.module_id)!;
  assert.deepEqual(mainView.entryPointCounts, {
    fastapi_route: 1,
    python_main_module: 1,
    python_main_guard: 1,
    unmapped: 0,
    unreturned: 0,
  });
  assert.deepEqual(helperView.entryPointCounts, { fastapi_route: 0, python_main_module: 0, python_main_guard: 0, unmapped: 0, unreturned: 0 });
  assert.equal(mainView.position.y, 95);
  assert.equal(helperView.position.y, 95);
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
  assert.equal(apiExpanded.relations[0].source, "role-api");
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

test("architecture allows one expanded responsibility at a time", () => {
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

  assert.equal(graph.visibleModuleCount, 10);
  assert.equal(graph.directories.find((item) => item.group.group_id === selected.group.group_id)?.visibleModules.length, 10);
  assert.equal(graph.directories.find((item) => item.group.group_id === selected.group.group_id)?.omittedByCanvasLimit, 0);
  assert.equal(graph.directories.find((item) => item.group.group_id === first.group.group_id)?.visibleModules.length, 0);
  assert.equal(graph.directories.find((item) => item.group.group_id === second.group.group_id)?.visibleModules.length, 0);

  const unselected = buildArchitectureGraph(value, {
    expandedGroupIds: [first.group.group_id, second.group.group_id, selected.group.group_id],
  });
  assert.equal(unselected.visibleModuleCount, 10);
  assert.equal(unselected.directories.find((item) => item.group.group_id === selected.group.group_id)?.visibleModules.length, 10);
});

test("supporting source directories are explicit and reappear as real focused boundaries", () => {
  const roles = overview(
    [a1, b1],
    [
      { ...directory("role-api", "api", [a1.module_id]), group_kind: "responsibility", group_key: "api", label: "API" },
      directory("tests-api", "apps/api/tests", [b1.module_id]),
      directory("dir-covered", "apps/api", [a1.module_id]),
    ],
    [relation("api-test", a1, b1, ["imports"], [codeEdge("api-test-edge", a1.module_id, b1.module_id, "imports", a1.symbol.path)])],
  );

  const overviewGraph = buildArchitectureGraph(roles);
  assert.deepEqual(overviewGraph.directories.map(({ group }) => group.group_id), ["role-api"]);
  assert.equal(overviewGraph.supportingDirectoryCount, 1);
  assert.equal(overviewGraph.omittedRelationCount, 1);
  assert.deepEqual(buildArchitectureGraph(roles, { showSupportingDirectories: true }).directories.map(({ group }) => group.group_id), ["role-api", "tests-api"]);

  const focused = buildArchitectureGraph(roles, { focusedGroupId: "role-api" });
  assert.deepEqual(focused.directories.map(({ group }) => group.group_id), ["role-api", "tests-api"]);
  assert.deepEqual(focused.relations.map(({ source, target }) => [source, target]), [["role-api", "tests-api"]]);
});

test("dependency layout keeps cycles honest, layers real direction, and contains expanded modules", () => {
  const modules = Array.from({ length: 7 }, (_, index) => moduleRecord(`dense-${index}`, `src/d${index}/m.py`));
  const groups = modules.map((module, index) => ({
    ...directory(`role-${index}`, `d${index}`, [module.module_id]),
    group_kind: "responsibility" as const,
    label: `D${index}`,
  }));
  const relations = [
    relation("cycle-a-b", modules[0], modules[1], ["imports"], [codeEdge("ab", modules[0].module_id, modules[1].module_id, "imports", modules[0].symbol.path)]),
    relation("cycle-b-a", modules[1], modules[0], ["calls"], [codeEdge("ba", modules[1].module_id, modules[0].module_id, "calls", modules[1].symbol.path)]),
    relation("cycle-b-c", modules[1], modules[2], ["imports"], [codeEdge("bc", modules[1].module_id, modules[2].module_id, "imports", modules[1].symbol.path)]),
    relation("c-to-d", modules[2], modules[3], ["calls"], [codeEdge("cd", modules[2].module_id, modules[3].module_id, "calls", modules[2].symbol.path)]),
    relation("d-to-e", modules[3], modules[4], ["imports"], [codeEdge("de", modules[3].module_id, modules[4].module_id, "imports", modules[3].symbol.path)]),
  ];
  const graph = buildArchitectureGraph(overview(modules, groups, relations), { expandedGroupIds: ["role-4"] });
  assert.equal(graph.dependencyCycleCount, 1);
  assert.equal(graph.directories.find(({ group }) => group.group_id === "role-0")?.dependencyCycleGroupSize, 2);
  assert.equal(graph.directories.find(({ group }) => group.group_id === "role-1")?.dependencyCycleGroupSize, 2);
  const positions = new Map(graph.directories.map(({ group, position }) => [group.group_id, position]));
  assert.ok(positions.get("role-2")!.x > positions.get("role-0")!.x);
  assert.ok(positions.get("role-3")!.x > positions.get("role-2")!.x);

  for (let index = 0; index < graph.directories.length; index += 1) {
    const current = graph.directories[index];
    for (const module of current.visibleModules) {
      assert.ok(module.position.x + 156 <= current.width);
      assert.ok(module.position.y + 68 <= current.height);
    }
    for (let other = index + 1; other < graph.directories.length; other += 1) {
      const next = graph.directories[other];
      const overlaps = current.position.x < next.position.x + next.width
        && current.position.x + current.width > next.position.x
        && current.position.y < next.position.y + next.height
        && current.position.y + current.height > next.position.y;
      assert.equal(overlaps, false, `${current.group.group_id} overlaps ${next.group.group_id}`);
    }
  }
  const nodeIds = new Set([...graph.directories.map(({ group }) => group.group_id), ...graph.directories.flatMap(({ visibleModules }) => visibleModules.map(({ module }) => module.module_id))]);
  assert.ok(graph.relations.every(({ source, target }) => nodeIds.has(source) && nodeIds.has(target)));
});

test("architecture edges choose center handles along the dominant dependency direction", () => {
  assert.deepEqual(architectureEdgeSides({ x: 0, y: 0 }, { x: 200, y: 30 }), { source: "right", target: "left" });
  assert.deepEqual(architectureEdgeSides({ x: 100, y: 100 }, { x: 142, y: 114 }), { source: "right", target: "left" });
  assert.deepEqual(architectureEdgeSides({ x: 0, y: 0 }, { x: 20, y: 180 }), { source: "bottom", target: "top" });
  assert.deepEqual(architectureEdgeSides({ x: 200, y: 0 }, { x: 0, y: 0 }), { source: "left", target: "right" });
});

test("focused architecture keeps short internal module edges straight", () => {
  assert.equal(architectureRelationEdgeType(true, true), "straight");
  assert.equal(architectureRelationEdgeType(true, false), "smoothstep");
  assert.equal(architectureRelationEdgeType(false, false), "straight");
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
  assert.equal(expanded.httpRelations[0].source, "group-web");
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
  assert.equal(bothKinds.relations.length, 2);
  assert.equal(bothKinds.relations.some(({ source, target }) => source === "role-storage" && target === "role-web"), false);
  assert.equal(bothKinds.focusHiddenGroupCount, 1);
  assert.equal(bothKinds.focusHiddenRelationCount, 2);
  assert.equal(bothKinds.focusHiddenHttpAssociationCount, 0);
});

test("focused dependency view centers the responsibility and keeps one-hop edge corridors clear", () => {
  const modules = [
    moduleRecord("focus-center", "center/main.py"),
    moduleRecord("focus-a", "a/main.py"),
    moduleRecord("focus-b", "b/main.py"),
    moduleRecord("focus-c", "c/main.py"),
    moduleRecord("focus-d", "d/main.py"),
    moduleRecord("focus-e", "e/main.py"),
  ];
  const [center, a, b, c, d, e] = modules;
  const roles = modules.map((module, index) => ({
    ...directory(`role-${String.fromCharCode(97 + index)}`, `role-${index}`, [module.module_id]),
    group_kind: "responsibility" as const,
    label: index === 0 ? "Center" : `Neighbor ${String.fromCharCode(96 + index)}`,
  }));
  const edges = [
    relation("a-to-center", a, center, ["imports"], [codeEdge("a-center", a.module_id, center.module_id, "imports", a.symbol.path)]),
    relation("c-to-center", c, center, ["imports"], [codeEdge("c-center", c.module_id, center.module_id, "imports", c.symbol.path)]),
    relation("center-to-b", center, b, ["calls"], [codeEdge("center-b", center.module_id, b.module_id, "calls", center.symbol.path)]),
    relation("center-to-d", center, d, ["imports"], [codeEdge("center-d", center.module_id, d.module_id, "imports", center.symbol.path)]),
    relation("e-to-center", e, center, ["imports"], [codeEdge("e-center", e.module_id, center.module_id, "imports", e.symbol.path)]),
    relation("center-to-e", center, e, ["calls"], [codeEdge("center-e", center.module_id, e.module_id, "calls", center.symbol.path)]),
    relation("neighbor-cross", a, d, ["imports"], [codeEdge("a-d", a.module_id, d.module_id, "imports", a.symbol.path)]),
  ];
  const model = buildArchitectureGraph(overview(modules, roles, edges), {
    focusedGroupId: "role-a",
    showHttpAssociations: false,
  });
  const positions = new Map(model.directories.map(({ group, position, width, height }) => [group.group_id, { ...position, width, height }]));
  const focus = positions.get("role-a")!;
  assert.ok(positions.get("role-b")!.x < focus.x);
  assert.ok(positions.get("role-c")!.x > focus.x);
  assert.ok(positions.get("role-d")!.x < focus.x);
  assert.ok(positions.get("role-e")!.x > focus.x);
  assert.ok(positions.get("role-f")!.x < focus.x, "bidirectional neighbor has one stable left-column node");
  assert.equal(model.relations.some(({ source, target }) => source === "role-b" && target === "role-d"), false);
  assert.equal(model.focusHiddenRelationCount, 1);

  for (const edge of model.relations) {
    const source = positions.get(edge.source)!;
    const target = positions.get(edge.target)!;
    const sourceCenter = { x: source.x + source.width / 2, y: source.y + source.height / 2 };
    const targetCenter = { x: target.x + target.width / 2, y: target.y + target.height / 2 };
    assert.equal(architectureEdgeSides(sourceCenter, targetCenter).source, sourceCenter.x < targetCenter.x ? "right" : "left");
    const corridorLeft = Math.min(source.x + source.width, target.x + target.width);
    const corridorRight = Math.max(source.x, target.x);
    for (const [groupId, box] of positions) {
      if (groupId === edge.source || groupId === edge.target) continue;
      assert.ok(
        box.x + box.width <= corridorLeft || box.x >= corridorRight,
        `${groupId} occupies the ${edge.source} → ${edge.target} edge corridor`,
      );
    }
  }
});

test("expanded internal edges require an explicitly focused module", () => {
  const a2 = moduleRecord("a2", "api/handlers.py");
  const b2 = moduleRecord("b2", "web/routes.py");
  const value = overview(
    [a1, a2, b1, b2],
    [
      { ...directory("role-api", "api", [a1.module_id, a2.module_id]), group_kind: "responsibility", group_key: "api", label: "API" },
      { ...directory("role-web", "web", [b1.module_id, b2.module_id]), group_kind: "responsibility", group_key: "web", label: "Web" },
    ],
    [
      relation("api-web-one", a1, b1, ["imports"], [codeEdge("api-web-import-one", a1.module_id, b1.module_id, "imports", a1.symbol.path)]),
      relation("api-web-two", a2, b2, ["calls"], [codeEdge("api-web-call-two", a2.module_id, b2.module_id, "calls", a2.symbol.path)]),
      relation("api-internal", a1, a2, ["calls"], [codeEdge("api-internal-call", a1.module_id, a2.module_id, "calls", a1.symbol.path)]),
    ],
  );

  const model = buildArchitectureGraph(value, { expandedGroupIds: ["role-api"] });
  const boundary = model.relations.find(({ source, target }) => source === "role-api" && target === "role-web");
  assert.equal(boundary?.relationCount, 2);
  assert.equal(boundary?.edgeCount, 2);
  assert.deepEqual(boundary?.relationKinds, ["calls", "imports"]);
  assert.equal(model.relations.some(({ source, target }) => source === "a1" && target === "a2"), true);
  assert.equal(model.relations.some(({ source, target }) => source === "a1" && target === "b1"), false);
  assert.deepEqual(model.relations.map(({ source, target }) => [source, target]), [["a1", "a2"], ["role-api", "role-web"]]);
  const visibleModuleIds = new Set(model.directories.flatMap((directory) => directory.visibleModules.map(({ module }) => module.module_id)));
  assert.deepEqual(
    architectureRelationsForFocusedModule(model.relations, visibleModuleIds, null).map(({ source, target }) => [source, target]),
    [["role-api", "role-web"]],
  );
  assert.deepEqual(
    architectureRelationsForFocusedModule(model.relations, visibleModuleIds, "a1").map(({ source, target }) => [source, target]),
    [["a1", "a2"], ["role-api", "role-web"]],
  );
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


test("overview folds obstructed edges without deleting graph relations", () => {
  const value = overview([a1, b1], [directory("a", "api", [a1.module_id]), directory("b", "web", [b1.module_id])], [relation("ab", a1, b1, ["calls"], [codeEdge("ab-call", a1.module_id, b1.module_id, "calls", a1.symbol.path)])]);
  const model = buildArchitectureGraph(value);
  const a = { ...model.directories[0], position: { x: 0, y: 0 } };
  const b = { ...model.directories[1], position: { x: 900, y: 0 } };
  const obstacle = { ...a, group: { ...a.group, group_id: "obstacle" }, position: { x: 450, y: 0 } };
  assert.equal(architectureOverviewEdgeVisible("a", "b", [a, b, obstacle]), false);
  assert.equal(architectureOverviewEdgeVisible("b", "a", [a, b, obstacle]), false);
  assert.equal(architectureOverviewEdgeVisible("a", "b", [a, b, { ...obstacle, position: { x: 450, y: 200 } }]), true);
  const verticalB = { ...b, position: { x: 0, y: 600 } };
  assert.equal(architectureOverviewEdgeVisible("a", "b", [a, verticalB, { ...obstacle, position: { x: 0, y: 300 } }]), false);
  assert.equal(model.relations.length, 1);
  assert.equal(model.relations[0].edgeCount, 1);
});


test("directory focus restores related dependencies without a responsibility manifest", () => {
  const unrelated = moduleRecord("unrelated", "other/unused.py");
  const value = overview([a1, b1, unrelated], [directory("a", "api", [a1.module_id]), directory("b", "web", [b1.module_id]), directory("other", "other", [unrelated.module_id])], [relation("ab", a1, b1, ["calls"], [codeEdge("ab-call", a1.module_id, b1.module_id, "calls", a1.symbol.path)])]);
  const model = buildArchitectureGraph(value, { focusedGroupId: "a", expandedGroupIds: ["a"], selectedGroupId: "a" });
  assert.deepEqual(model.directories.map(({ group }) => group.group_id), ["a", "b"]);
  assert.equal(model.focusHiddenGroupCount, 1);
  assert.equal(model.directories.find(({ group }) => group.group_id === "a")?.expanded, true);
  assert.equal(model.relations.length, 1);
  assert.equal(model.relations[0].source, "a");
  assert.equal(model.relations[0].target, "b");
  assert.equal(model.relations[0].edgeCount, 1);
});
