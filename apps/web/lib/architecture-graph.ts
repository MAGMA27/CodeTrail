import type {
  OverviewGroup,
  OverviewClassificationCounts,
  OverviewHttpAssociation,
  OverviewModule,
  OverviewModuleRelation,
  OverviewEntryPoint,
  ProjectOverview,
} from "./api.ts";

export const ARCHITECTURE_MODULE_LIMIT = 40;

export type ArchitecturePosition = { x: number; y: number };
export type ArchitectureSide = "left" | "right" | "top" | "bottom";
export type ArchitectureEntryCounts = Record<OverviewEntryPoint["kind"], number> & { unreturned: number; unmapped: number };

export function architectureEntryModule(overview: ProjectOverview, entry: OverviewEntryPoint): OverviewModule | null {
  const indexId = overview.index.index_id;
  if (entry.index_id !== indexId) return null;
  if (entry.kind === "fastapi_route") {
    const handler = entry.handler_symbol;
    if (!handler || handler.index_id !== indexId || handler.kind !== "function" || handler.path !== entry.path) return null;
    return overview.modules.find((module) =>
      module.index_id === indexId && module.symbol.index_id === indexId && module.symbol.kind === "module" && module.symbol.path === handler.path,
    ) ?? null;
  }
  if (!entry.symbol_id) return null;
  return overview.modules.find((module) =>
    module.index_id === indexId && module.symbol.index_id === indexId && module.symbol.kind === "module" && module.symbol.path === entry.path &&
    (module.module_id === entry.symbol_id || module.symbol.symbol_id === entry.symbol_id),
  ) ?? null;
}

export function summarizeArchitectureEntries(
  entryPointIds: string[],
  entriesById: ReadonlyMap<string, OverviewEntryPoint>,
  unmappedEntryPointIds: ReadonlySet<string> = new Set(),
): ArchitectureEntryCounts {
  const counts: ArchitectureEntryCounts = { fastapi_route: 0, python_main_module: 0, python_main_guard: 0, unreturned: 0, unmapped: 0 };
  for (const entryPointId of new Set(entryPointIds)) {
    const entry = entriesById.get(entryPointId);
    if (entry && unmappedEntryPointIds.has(entryPointId)) counts.unmapped += 1;
    else if (entry) counts[entry.kind] += 1;
    else counts.unreturned += 1;
  }
  return counts;
}

export function architectureEntrySummary(counts: ArchitectureEntryCounts): string {
  return [
    counts.fastapi_route > 0 ? `HTTP入口 ${counts.fastapi_route}` : null,
    counts.python_main_module > 0 ? `Python启动文件 ${counts.python_main_module}` : null,
    counts.python_main_guard > 0 ? `__main__启动块 ${counts.python_main_guard}` : null,
    counts.unmapped > 0 ? `入口模块未映射 ${counts.unmapped}` : null,
    counts.unreturned > 0 ? `入口明细未返回 ${counts.unreturned}` : null,
  ].filter((item): item is string => item !== null).join(" · ");
}

// ponytail: overview uses unobstructed straight edges; inspect a responsibility for folded dependencies.
export function architectureOverviewEdgeVisible(source: string, target: string, directories: ArchitectureDirectory[]): boolean {
  const from = directories.find((item) => item.group.group_id === source);
  const to = directories.find((item) => item.group.group_id === target);
  if (!from || !to) return true;
  const start = { x: from.position.x + from.width / 2, y: from.position.y + from.height / 2 };
  const end = { x: to.position.x + to.width / 2, y: to.position.y + to.height / 2 };
  const sides = architectureEdgeSides(start, end);
  for (const [point, box, side] of [[start, from, sides.source], [end, to, sides.target]] as const) {
    if (side === "left") point.x = box.position.x;
    if (side === "right") point.x = box.position.x + box.width;
    if (side === "top") point.y = box.position.y;
    if (side === "bottom") point.y = box.position.y + box.height;
  }
  return !directories.some((box) => {
    if (box === from || box === to) return false;
    let low = 0;
    let high = 1;
    for (const axis of ["x", "y"] as const) {
      const delta = end[axis] - start[axis];
      const min = box.position[axis] - 8;
      const max = box.position[axis] + (axis === "x" ? box.width : box.height) + 8;
      if (delta === 0) {
        if (start[axis] < min || start[axis] > max) return false;
      } else {
        const a = (min - start[axis]) / delta;
        const b = (max - start[axis]) / delta;
        low = Math.max(low, Math.min(a, b));
        high = Math.min(high, Math.max(a, b));
        if (low > high) return false;
      }
    }
    return low <= high;
  });
}

const AUXILIARY_DIRECTORY_NAMES = new Set(["test", "tests", "fixture", "fixtures", "eval", "evals", "docs", "examples", "benchmarks"]);

export function architectureEdgeSides(source: ArchitecturePosition, target: ArchitecturePosition): { source: ArchitectureSide; target: ArchitectureSide } {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0 ? { source: "right", target: "left" } : { source: "left", target: "right" };
  }
  return dy >= 0 ? { source: "bottom", target: "top" } : { source: "top", target: "bottom" };
}

export type ArchitectureDirectory = {
  group: OverviewGroup;
  expanded: boolean;
  position: ArchitecturePosition;
  width: number;
  height: number;
  visibleModules: Array<{ module: OverviewModule; position: ArchitecturePosition; highlighted: boolean; entryPointCounts: ArchitectureEntryCounts }>;
  loadedModuleCount: number;
  classificationCounts: OverviewClassificationCounts;
  omittedIndexModuleCount: number;
  omittedByCanvasLimit: number;
  internalRelationCount: number;
  internalEdgeCount: number;
  internalHttpAssociationCount: number;
  entryPointCounts: ArchitectureEntryCounts;
  highlighted: boolean;
  dependencyCycleGroupSize: number | null;
};

export type ArchitectureRelation = {
  id: string;
  source: string;
  target: string;
  relationCount: number;
  edgeCount: number;
  relationKinds: Array<"imports" | "calls">;
  representative: { path: string; startLine: number; endLine: number } | null;
  highlighted: boolean;
};

export type ArchitectureHttpRelation = {
  id: string;
  source: string;
  target: string;
  associationCount: number;
  labels: string[];
  representative: OverviewHttpAssociation | null;
  highlighted: boolean;
};

export type ArchitectureGraphModel = {
  directories: ArchitectureDirectory[];
  relations: ArchitectureRelation[];
  httpRelations: ArchitectureHttpRelation[];
  visibleModuleCount: number;
  omittedRelationCount: number;
  omittedRelationEdgeCount: number;
  omittedHttpAssociationCount: number;
  summarizedHttpAssociationCount: number;
  unmatchedHttpModuleCount: number;
  focusNodeIds: string[];
  focusHiddenGroupCount: number;
  focusHiddenRelationCount: number;
  focusHiddenHttpAssociationCount: number;
  supportingDirectoryCount: number;
  dependencyCycleCount: number;
};

export function visibleArchitectureRelations(
  relations: ArchitectureRelation[],
  visibleModuleIds: ReadonlySet<string>,
  activeModuleId?: string | null,
): ArchitectureRelation[] {
  return relations.filter((relation) => {
    const sourceIsModule = visibleModuleIds.has(relation.source);
    const targetIsModule = visibleModuleIds.has(relation.target);
    if (!sourceIsModule || !targetIsModule) return true;
    return Boolean(activeModuleId && (relation.source === activeModuleId || relation.target === activeModuleId));
  });
}

function isSupportingDirectory(group: OverviewGroup): boolean {
  return group.group_kind === "source_directory" && group.group_key.split(/[\\/]+/).some((part) => AUXILIARY_DIRECTORY_NAMES.has(part.toLowerCase()));
}

function layoutByDependency(
  groups: OverviewGroup[],
  heights: Map<string, number>,
  relations: Array<{ source: string; target: string }>,
): { positions: Map<string, ArchitecturePosition>; cycleSizes: Map<string, number>; cycleCount: number } {
  const groupIds = groups.map((group) => group.group_id);
  const outgoing = new Map(groupIds.map((id) => [id, [] as string[]]));
  for (const { source, target } of relations) {
    if (source !== target && outgoing.has(source) && outgoing.has(target)) outgoing.get(source)!.push(target);
  }

  let nextIndex = 0;
  const indexById = new Map<string, number>();
  const lowById = new Map<string, number>();
  const stack: string[] = [];
  const onStack = new Set<string>();
  const components: string[][] = [];
  const visit = (id: string) => {
    indexById.set(id, nextIndex);
    lowById.set(id, nextIndex++);
    stack.push(id);
    onStack.add(id);
    for (const target of outgoing.get(id) ?? []) {
      if (!indexById.has(target)) {
        visit(target);
        lowById.set(id, Math.min(lowById.get(id)!, lowById.get(target)!));
      } else if (onStack.has(target)) {
        lowById.set(id, Math.min(lowById.get(id)!, indexById.get(target)!));
      }
    }
    if (lowById.get(id) === indexById.get(id)) {
      const component: string[] = [];
      let member = "";
      do {
        member = stack.pop()!;
        onStack.delete(member);
        component.push(member);
      } while (member !== id);
      components.push(component);
    }
  };
  for (const id of groupIds) if (!indexById.has(id)) visit(id);

  const componentById = new Map<string, number>();
  components.forEach((component, componentIndex) => component.forEach((id) => componentById.set(id, componentIndex)));
  const componentEdges = new Map(components.map((_, index) => [index, new Set<number>()]));
  const indegree = new Array(components.length).fill(0) as number[];
  for (const [source, targets] of outgoing) {
    for (const target of targets) {
      const from = componentById.get(source)!;
      const to = componentById.get(target)!;
      if (from !== to && !componentEdges.get(from)!.has(to)) {
        componentEdges.get(from)!.add(to);
        indegree[to] += 1;
      }
    }
  }
  const rank = new Array(components.length).fill(0) as number[];
  const queue = components.map((_, index) => index).filter((index) => indegree[index] === 0);
  while (queue.length > 0) {
    const component = queue.shift()!;
    for (const target of componentEdges.get(component) ?? []) {
      rank[target] = Math.max(rank[target], rank[component] + 1);
      indegree[target] -= 1;
      if (indegree[target] === 0) queue.push(target);
    }
  }

  const groupById = new Map(groups.map((group) => [group.group_id, group]));
  const layers = new Map<number, string[]>();
  for (const group of groups) {
    const layer = rank[componentById.get(group.group_id)!];
    const members = layers.get(layer) ?? [];
    members.push(group.group_id);
    layers.set(layer, members);
  }
  const positions = new Map<string, ArchitecturePosition>();
  const cycleSizes = new Map<string, number>();
  for (const component of components) {
    if (component.length > 1) for (const id of component) cycleSizes.set(id, component.length);
  }
  for (const [layer, members] of layers) {
    let y = 24;
    const sortedMembers = members.sort((left, right) => groupById.get(left)!.label.localeCompare(groupById.get(right)!.label) || left.localeCompare(right));
    for (let index = 0; index < sortedMembers.length; index += 2) {
      const row = sortedMembers.slice(index, index + 2);
      row.forEach((id, column) => positions.set(id, { x: 24 + layer * 820 + column * 410, y }));
      y += Math.max(...row.map((id) => heights.get(id) ?? 96)) + 36;
    }
  }
  return { positions, cycleSizes, cycleCount: components.filter((component) => component.length > 1).length };
}

function layoutFocusedBoundary(
  groups: OverviewGroup[],
  heights: Map<string, number>,
  relations: Array<{ source: string; target: string }>,
  focusedGroupId: string,
  groupWidth: number,
): Map<string, ArchitecturePosition> {
  const incoming = new Set<string>();
  const outgoing = new Set<string>();
  for (const { source, target } of relations) {
    if (target === focusedGroupId && source !== focusedGroupId) incoming.add(source);
    if (source === focusedGroupId && target !== focusedGroupId) outgoing.add(target);
  }

  const left: OverviewGroup[] = [];
  const right: OverviewGroup[] = [];
  const heightOf = (group: OverviewGroup) => heights.get(group.group_id) ?? 96;
  const totalHeight = (column: OverviewGroup[]) => column.reduce((total, group) => total + heightOf(group), Math.max(0, column.length - 1) * 36);
  const neighbors = groups
    .filter((group) => group.group_id !== focusedGroupId)
    .sort((a, b) => a.label.localeCompare(b.label) || a.group_id.localeCompare(b.group_id));

  for (const group of neighbors) {
    const hasIncoming = incoming.has(group.group_id);
    const hasOutgoing = outgoing.has(group.group_id);
    if (hasIncoming && !hasOutgoing) {
      left.push(group);
    } else if (hasOutgoing && !hasIncoming) {
      right.push(group);
    } else if (totalHeight(left) <= totalHeight(right)) {
      // Bidirectional neighbors stay a single node; stable left-on-tie placement
      // keeps both real directions in the same clear center corridor.
      left.push(group);
    } else {
      right.push(group);
    }
  }

  const focusHeight = heights.get(focusedGroupId) ?? 96;
  const leftHeight = totalHeight(left);
  const rightHeight = totalHeight(right);
  const canvasHeight = Math.max(focusHeight, leftHeight, rightHeight);
  const centerGap = Math.max(groupWidth + 160, Math.ceil(Math.max(leftHeight, rightHeight) / 2) + 80);
  const positions = new Map<string, ArchitecturePosition>();
  positions.set(focusedGroupId, {
    x: 24 + centerGap,
    y: 24 + (canvasHeight - focusHeight) / 2,
  });

  const placeColumn = (column: OverviewGroup[], x: number, columnHeight: number) => {
    let y = 24 + (canvasHeight - columnHeight) / 2;
    for (const group of column) {
      positions.set(group.group_id, { x, y });
      y += heightOf(group) + 36;
    }
  };
  placeColumn(left, 24, leftHeight);
  placeColumn(right, 24 + centerGap * 2, rightHeight);
  return positions;
}

export function buildArchitectureGraph(
  overview: ProjectOverview,
  options: {
    expandedGroupIds?: string[];
    showSupportingDirectories?: boolean;
    focusedGroupId?: string | null;
    selectedHttpGroupId?: string | null;
    selectedGroupId?: string | null;
    relationKinds?: Array<"imports" | "calls">;
    showHttpAssociations?: boolean;
    maxVisibleModules?: number;
  } = {},
): ArchitectureGraphModel {
  const indexId = overview.index.index_id;
  const allDirectoryGroups = overview.groups
    .filter((group) => ["source_directory", "responsibility"].includes(group.group_kind) && group.index_id === indexId)
    .sort((left, right) => {
      const leftKind = left.group_kind === "responsibility" ? 0 : 1;
      const rightKind = right.group_kind === "responsibility" ? 0 : 1;
      return leftKind - rightKind || left.label.localeCompare(right.label) || left.group_id.localeCompare(right.group_id);
    });
  const responsibilityGroups = allDirectoryGroups.filter((group) => group.group_kind === "responsibility");
  const assignedModuleIds = new Set(responsibilityGroups.flatMap((group) => group.module_ids));
  const hasResponsibilityManifest = responsibilityGroups.length > 0;
  const directoryGroups = allDirectoryGroups
    .filter((group) => group.group_kind === "responsibility" || !hasResponsibilityManifest || group.module_ids.some((moduleId) => !assignedModuleIds.has(moduleId)))
    .map((group) => group.group_kind === "source_directory" && hasResponsibilityManifest
      ? { ...group, module_ids: group.module_ids.filter((moduleId) => !assignedModuleIds.has(moduleId)) }
      : group);
  const supportingGroups = directoryGroups.filter(isSupportingDirectory);
  const selectedGroupId = options.selectedGroupId;
  const showSupportingDirectories = options.showSupportingDirectories === true || supportingGroups.some((group) => group.group_id === selectedGroupId);
  const defaultDirectoryGroups = directoryGroups.filter((group) => showSupportingDirectories || !isSupportingDirectory(group));
  const groupById = new Map(directoryGroups.map((group) => [group.group_id, group]));
  const moduleById = new Map(
    overview.modules
      .filter((module) => module.index_id === indexId && module.symbol.index_id === indexId)
      .map((module) => [module.module_id, module]),
  );
  const entriesById = new Map(
    overview.entries
      .filter((entry) => entry.index_id === indexId)
      .map((entry) => [entry.entry_point_id, entry]),
  );
  const entryPointIdsByModule = new Map<string, Set<string>>();
  for (const moduleId of moduleById.keys()) entryPointIdsByModule.set(moduleId, new Set());
  for (const entry of entriesById.values()) {
    const owner = architectureEntryModule(overview, entry);
    if (owner) entryPointIdsByModule.get(owner.module_id)?.add(entry.entry_point_id);
  }
  const groupByModuleId = new Map<string, string>();
  const moduleIdsByGroup = new Map<string, string[]>();
  for (const group of directoryGroups) {
    const moduleIds = [...new Set(group.module_ids)];
    moduleIdsByGroup.set(group.group_id, moduleIds);
    for (const moduleId of moduleIds) {
      if (!groupByModuleId.has(moduleId)) {
        groupByModuleId.set(moduleId, group.group_id);
      }
    }
  }
  const entryPointIdsByGroup = new Map(directoryGroups.map((group) => {
    const entryPointIds = new Set<string>();
    const unmappedEntryPointIds = new Set<string>();
    const groupModuleIds = new Set(moduleIdsByGroup.get(group.group_id) ?? []);
    for (const moduleId of moduleIdsByGroup.get(group.group_id) ?? []) {
      for (const entryPointId of entryPointIdsByModule.get(moduleId) ?? []) entryPointIds.add(entryPointId);
    }
    for (const entryPointId of group.entry_point_ids) {
      const entry = entriesById.get(entryPointId);
      if (!entry) entryPointIds.add(entryPointId);
      else {
        const owner = architectureEntryModule(overview, entry);
        if (!owner) {
          entryPointIds.add(entryPointId);
          unmappedEntryPointIds.add(entryPointId);
        } else if (groupModuleIds.has(owner.module_id)) {
          entryPointIds.add(entryPointId);
        }
      }
    }
    return [group.group_id, { entryPointIds, unmappedEntryPointIds }] as const;
  }));

  const selectedHttpGroup = overview.groups.find(
    (group) => group.group_kind === "http_path" && group.group_id === options.selectedHttpGroupId && group.index_id === indexId,
  ) ?? null;
  const httpModuleIds = new Set(selectedHttpGroup?.module_ids ?? []);
  const relationKinds = new Set(options.relationKinds ?? ["imports", "calls"]);
  const focusedGroup = directoryGroups.find((group) => group.group_id === options.focusedGroupId) ?? null;
  const retainedGroupIds = new Set<string>();
  let focusHiddenRelationCount = 0;
  let focusHiddenHttpAssociationCount = 0;
  if (focusedGroup) {
    retainedGroupIds.add(focusedGroup.group_id);
    const includeNeighbor = (sourceModuleId: string, targetModuleId: string) => {
      const sourceGroupId = groupByModuleId.get(sourceModuleId);
      const targetGroupId = groupByModuleId.get(targetModuleId);
      if (sourceGroupId === focusedGroup.group_id && targetGroupId) retainedGroupIds.add(targetGroupId);
      if (targetGroupId === focusedGroup.group_id && sourceGroupId) retainedGroupIds.add(sourceGroupId);
    };
    for (const relation of overview.module_relations) {
      if (relation.index_id !== indexId || relation.source_module.index_id !== indexId || relation.target_module.index_id !== indexId) continue;
      if (relationKinds.size > 0 && !relation.relation_kinds.some((kind) => relationKinds.has(kind))) continue;
      includeNeighbor(relation.source_module_id, relation.target_module_id);
    }
    if (options.showHttpAssociations !== false) {
      for (const association of overview.http_associations) {
        if (association.index_id !== indexId || association.client_module.index_id !== indexId || association.server_module.index_id !== indexId) continue;
        includeNeighbor(association.client_module.symbol_id, association.server_module.symbol_id);
      }
    }
  }
  const visibleDirectoryGroups = focusedGroup
    ? directoryGroups.filter((group) => retainedGroupIds.has(group.group_id))
    : defaultDirectoryGroups;
  const visibleGroupIds = new Set(visibleDirectoryGroups.map((group) => group.group_id));
  const focusHiddenGroupCount = focusedGroup ? directoryGroups.length - visibleDirectoryGroups.length : 0;

  const requestedExpandedGroupIds = [...new Set(options.expandedGroupIds ?? [])].filter((groupId) => visibleGroupIds.has(groupId));
  const preferredExpandedGroupId = [focusedGroup?.group_id, selectedGroupId].find((id) => id && requestedExpandedGroupIds.includes(id));
  const expandedGroupId = preferredExpandedGroupId ?? requestedExpandedGroupIds.at(-1);
  const expandedGroupIds = expandedGroupId ? [expandedGroupId] : [];
  const expandedGroups = new Set(expandedGroupIds);
  const maxVisibleModules = Math.max(0, Math.min(options.maxVisibleModules ?? ARCHITECTURE_MODULE_LIMIT, ARCHITECTURE_MODULE_LIMIT));
  const visibleModuleIdsByGroup = new Map<string, string[]>();
  let remainingModuleSlots = maxVisibleModules;
  const priorityGroupId = focusedGroup?.group_id ?? options.selectedGroupId;
  const allocationOrder = priorityGroupId && expandedGroups.has(priorityGroupId)
    ? [priorityGroupId, ...expandedGroupIds.filter((groupId) => groupId !== priorityGroupId)]
    : expandedGroupIds;
  for (const groupId of allocationOrder) {
    const moduleIds = (moduleIdsByGroup.get(groupId) ?? [])
      .filter((moduleId) => moduleById.has(moduleId))
      .sort((left, right) => {
        const leftModule = moduleById.get(left)?.symbol;
        const rightModule = moduleById.get(right)?.symbol;
        return (leftModule?.path ?? "").localeCompare(rightModule?.path ?? "")
          || (leftModule?.qualified_name ?? "").localeCompare(rightModule?.qualified_name ?? "")
          || left.localeCompare(right);
      });
    const visible = moduleIds.slice(0, remainingModuleSlots);
    visibleModuleIdsByGroup.set(groupId, visible);
    remainingModuleSlots -= visible.length;
  }
  const visibleModuleIds = new Set([...visibleModuleIdsByGroup.values()].flat());

  const relationsByDisplayPair = new Map<string, {
    source: string;
    target: string;
    relationCount: number;
    edgeCount: number;
    relationKinds: Set<"imports" | "calls">;
    representative: ArchitectureRelation["representative"];
    highlighted: boolean;
  }>();
  const internalByGroup = new Map<string, { relationCount: number; edgeCount: number }>();
  const internalHttpByGroup = new Map<string, number>();
  let omittedRelationCount = 0;
  let omittedRelationEdgeCount = 0;

  for (const relation of overview.module_relations) {
    if (relation.index_id !== indexId || relation.source_module.index_id !== indexId || relation.target_module.index_id !== indexId) {
      continue;
    }
    if (relationKinds.size > 0 && !relation.relation_kinds.some((kind) => relationKinds.has(kind))) {
      continue;
    }
    const sourceGroupId = groupByModuleId.get(relation.source_module_id);
    const targetGroupId = groupByModuleId.get(relation.target_module_id);
    if (!sourceGroupId || !targetGroupId) {
      omittedRelationCount += 1;
      omittedRelationEdgeCount += relation.edge_count;
      continue;
    }
    if (focusedGroup && (
      sourceGroupId !== focusedGroup.group_id && targetGroupId !== focusedGroup.group_id
      || !retainedGroupIds.has(sourceGroupId)
      || !retainedGroupIds.has(targetGroupId)
    )) {
      focusHiddenRelationCount += 1;
      continue;
    }
    if (!visibleGroupIds.has(sourceGroupId) || !visibleGroupIds.has(targetGroupId)) {
      omittedRelationCount += 1;
      omittedRelationEdgeCount += relation.edge_count;
      continue;
    }
    const sourceVisible = visibleModuleIds.has(relation.source_module_id);
    const targetVisible = visibleModuleIds.has(relation.target_module_id);
    const source = sourceGroupId === targetGroupId && sourceVisible ? relation.source_module_id : sourceGroupId;
    const target = sourceGroupId === targetGroupId && targetVisible ? relation.target_module_id : targetGroupId;
    const routeHighlighted = httpModuleIds.size === 0
      || httpModuleIds.has(relation.source_module_id)
      || httpModuleIds.has(relation.target_module_id);

    if (sourceGroupId === targetGroupId && (!sourceVisible || !targetVisible)) {
      const internal = internalByGroup.get(sourceGroupId) ?? { relationCount: 0, edgeCount: 0 };
      internal.relationCount += 1;
      internal.edgeCount += relation.edge_count;
      internalByGroup.set(sourceGroupId, internal);
      continue;
    }
    if (source === target) {
      const internal = internalByGroup.get(sourceGroupId) ?? { relationCount: 0, edgeCount: 0 };
      internal.relationCount += 1;
      internal.edgeCount += relation.edge_count;
      internalByGroup.set(sourceGroupId, internal);
      continue;
    }

    const key = JSON.stringify([source, target]);
    const aggregate = relationsByDisplayPair.get(key) ?? {
      source,
      target,
      relationCount: 0,
      edgeCount: 0,
      relationKinds: new Set<"imports" | "calls">(),
      representative: null,
      highlighted: false,
    };
    aggregate.relationCount += 1;
    aggregate.edgeCount += relation.edge_count;
    for (const kind of relation.relation_kinds) {
      aggregate.relationKinds.add(kind);
    }
    aggregate.highlighted ||= routeHighlighted;
    if (!aggregate.representative) {
      const edge = relation.edges.find((item) => item.index_id === indexId);
      if (edge) {
        aggregate.representative = {
          path: edge.source_location.path,
          startLine: edge.source_location.start_line,
          endLine: edge.source_location.end_line,
        };
      }
    }
    relationsByDisplayPair.set(key, aggregate);
  }

  const httpRelationsByDisplayPair = new Map<string, {
    source: string;
    target: string;
    associations: OverviewHttpAssociation[];
    labels: Set<string>;
    highlighted: boolean;
  }>();
  let omittedHttpAssociationCount = 0;
  let summarizedHttpAssociationCount = 0;
  if (options.showHttpAssociations !== false) {
    for (const association of overview.http_associations) {
      if (association.index_id !== indexId || association.client_module.index_id !== indexId || association.server_module.index_id !== indexId) continue;
      const sourceGroupId = groupByModuleId.get(association.client_module.symbol_id);
      const targetGroupId = groupByModuleId.get(association.server_module.symbol_id);
      if (!sourceGroupId || !targetGroupId) {
        omittedHttpAssociationCount += 1;
        continue;
      }
      if (focusedGroup && (
        sourceGroupId !== focusedGroup.group_id && targetGroupId !== focusedGroup.group_id
        || !retainedGroupIds.has(sourceGroupId)
        || !retainedGroupIds.has(targetGroupId)
      )) {
        focusHiddenHttpAssociationCount += 1;
        continue;
      }
      if (!visibleGroupIds.has(sourceGroupId) || !visibleGroupIds.has(targetGroupId)) {
        omittedHttpAssociationCount += 1;
        continue;
      }
      const sourceExpandedButHidden = expandedGroups.has(sourceGroupId) && !visibleModuleIds.has(association.client_module.symbol_id);
      const targetExpandedButHidden = expandedGroups.has(targetGroupId) && !visibleModuleIds.has(association.server_module.symbol_id);
      if (sourceExpandedButHidden || targetExpandedButHidden) summarizedHttpAssociationCount += 1;
      const source = sourceGroupId;
      const target = targetGroupId;
      if (sourceGroupId === targetGroupId && source === target) {
        internalHttpByGroup.set(sourceGroupId, (internalHttpByGroup.get(sourceGroupId) ?? 0) + 1);
        continue;
      }
      const key = JSON.stringify([source, target]);
      const aggregate = httpRelationsByDisplayPair.get(key) ?? {
        source,
        target,
        associations: [],
        labels: new Set<string>(),
        highlighted: false,
      };
      aggregate.associations.push(association);
      aggregate.labels.add(`${association.method} ${association.path}`);
      aggregate.highlighted ||= httpModuleIds.size === 0 || httpModuleIds.has(association.client_module.symbol_id) || httpModuleIds.has(association.server_module.symbol_id);
      httpRelationsByDisplayPair.set(key, aggregate);
    }
  }

  const groupWidth = 350;
  const groupHeights = new Map(visibleDirectoryGroups.map((group) => {
    const visibleCount = visibleModuleIdsByGroup.get(group.group_id)?.length ?? 0;
    const hasEntries = (entryPointIdsByGroup.get(group.group_id)?.entryPointIds.size ?? 0) > 0;
    return [group.group_id, (visibleCount > 0 ? 82 + Math.ceil(visibleCount / 2) * 80 : 96) + (hasEntries ? 28 : 0)] as const;
  }));
  const displayPairs = [...relationsByDisplayPair.values(), ...httpRelationsByDisplayPair.values()];
  const layout = layoutByDependency(
    visibleDirectoryGroups,
    groupHeights,
    displayPairs.map(({ source, target }) => ({
      source: groupByModuleId.get(source) ?? source,
      target: groupByModuleId.get(target) ?? target,
    })),
  );
  const positions = focusedGroup
    ? layoutFocusedBoundary(
      visibleDirectoryGroups,
      groupHeights,
      displayPairs.map(({ source, target }) => ({
        source: groupByModuleId.get(source) ?? source,
        target: groupByModuleId.get(target) ?? target,
      })),
      focusedGroup.group_id,
      groupWidth,
    )
    : layout.positions;

  const selectedGroup = groupById.get(options.selectedGroupId ?? "") ?? null;
  const focusGroupIds = new Set<string>();
  if (focusedGroup) {
    for (const groupId of retainedGroupIds) focusGroupIds.add(groupId);
  }
  if (selectedGroup && visibleGroupIds.has(selectedGroup.group_id)) {
    focusGroupIds.add(selectedGroup.group_id);
  }
  if (selectedHttpGroup) {
    for (const moduleId of httpModuleIds) {
      const groupId = groupByModuleId.get(moduleId);
      if (groupId && visibleGroupIds.has(groupId)) focusGroupIds.add(groupId);
    }
  }
  const focusNodeIds = new Set<string>();
  for (const groupId of focusGroupIds) {
    focusNodeIds.add(groupId);
    for (const moduleId of visibleModuleIdsByGroup.get(groupId) ?? []) {
      focusNodeIds.add(moduleId);
    }
  }

  let visibleModuleCount = 0;
  const directories = visibleDirectoryGroups.map((group): ArchitectureDirectory => {
    const moduleIds = moduleIdsByGroup.get(group.group_id) ?? [];
    const loadedModules = moduleIds.filter((moduleId) => moduleById.has(moduleId));
    const visibleModules = visibleModuleIdsByGroup.get(group.group_id) ?? [];
    const groupEntries = entryPointIdsByGroup.get(group.group_id) ?? { entryPointIds: new Set<string>(), unmappedEntryPointIds: new Set<string>() };
    const visibleModuleViews = visibleModules.map((moduleId, moduleIndex) => {
      const module = moduleById.get(moduleId)!;
      visibleModuleCount += 1;
      const routeHighlighted = httpModuleIds.size === 0 || httpModuleIds.has(moduleId);
      if (focusGroupIds.has(group.group_id)) focusNodeIds.add(moduleId);
      return {
        module,
        position: {
          x: 12 + (moduleIndex % 2) * 166,
          y: 67 + (groupEntries.entryPointIds.size > 0 ? 28 : 0) + Math.floor(moduleIndex / 2) * 80,
        },
        highlighted: routeHighlighted,
        entryPointCounts: summarizeArchitectureEntries([...(entryPointIdsByModule.get(module.module_id) ?? [])], entriesById),
      };
    });
    const internal = internalByGroup.get(group.group_id) ?? { relationCount: 0, edgeCount: 0 };
    return {
      group,
      expanded: expandedGroups.has(group.group_id),
      position: positions.get(group.group_id) ?? { x: 24, y: 24 },
      width: groupWidth,
      height: groupHeights.get(group.group_id) ?? 96,
      visibleModules: visibleModuleViews,
      loadedModuleCount: loadedModules.length,
      classificationCounts: loadedModules.reduce((counts, moduleId) => {
        const module = moduleById.get(moduleId)!;
        counts.application_entry += module.classification_counts.application_entry;
        counts.public_api += module.classification_counts.public_api;
        counts.extension_point += module.classification_counts.extension_point;
        counts.unknown += module.classification_counts.unknown;
        return counts;
      }, { application_entry: 0, public_api: 0, extension_point: 0, unknown: 0 }),
      entryPointCounts: summarizeArchitectureEntries([...groupEntries.entryPointIds], entriesById, groupEntries.unmappedEntryPointIds),
      omittedIndexModuleCount: moduleIds.length - loadedModules.length,
      omittedByCanvasLimit: expandedGroups.has(group.group_id) ? loadedModules.length - visibleModules.length : 0,
      internalRelationCount: internal.relationCount,
      internalEdgeCount: internal.edgeCount,
      internalHttpAssociationCount: internalHttpByGroup.get(group.group_id) ?? 0,
      highlighted: httpModuleIds.size === 0 || moduleIds.some((moduleId) => httpModuleIds.has(moduleId)),
      dependencyCycleGroupSize: layout.cycleSizes.get(group.group_id) ?? null,
    };
  });

  const relations: ArchitectureRelation[] = [...relationsByDisplayPair.entries()]
    .sort(([, left], [, right]) => left.source.localeCompare(right.source) || left.target.localeCompare(right.target))
    .map(([id, relation]) => {
      const kinds = [...relation.relationKinds].sort();
      const edgeKinds = relationKinds.size === 0 || kinds.some((kind) => relationKinds.has(kind));
      const highlighted = httpModuleIds.size === 0 || relation.highlighted;
      if (!edgeKinds) return null;
      return {
        id: `architecture:${id}`,
        source: relation.source,
        target: relation.target,
        relationCount: relation.relationCount,
        edgeCount: relation.edgeCount,
        relationKinds: kinds,
        representative: relation.representative,
        highlighted,
      };
    })
    .filter((relation): relation is ArchitectureRelation => relation !== null);

  const httpRelations: ArchitectureHttpRelation[] = [...httpRelationsByDisplayPair.entries()]
    .sort(([, left], [, right]) => left.source.localeCompare(right.source) || left.target.localeCompare(right.target))
    .map(([id, aggregate]) => ({
      id: `architecture-http:${id}`,
      source: aggregate.source,
      target: aggregate.target,
      associationCount: aggregate.associations.length,
      labels: [...aggregate.labels].sort().slice(0, 3),
      representative: aggregate.associations[0] ?? null,
      highlighted: httpModuleIds.size === 0 || aggregate.highlighted,
    }));

  const unmatchedHttpModuleCount = selectedHttpGroup
    ? selectedHttpGroup.module_ids.filter((moduleId) => !groupByModuleId.has(moduleId)).length
    : 0;
  return {
    directories,
    relations,
    httpRelations,
    visibleModuleCount,
    omittedRelationCount,
    omittedRelationEdgeCount,
    omittedHttpAssociationCount,
    summarizedHttpAssociationCount,
    unmatchedHttpModuleCount,
    focusNodeIds: [...focusNodeIds],
    focusHiddenGroupCount,
    focusHiddenRelationCount,
    focusHiddenHttpAssociationCount,
    supportingDirectoryCount: supportingGroups.length,
    dependencyCycleCount: layout.cycleCount,
  };
}
