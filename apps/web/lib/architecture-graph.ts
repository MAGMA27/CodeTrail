import type {
  OverviewGroup,
  OverviewHttpAssociation,
  OverviewModule,
  OverviewModuleRelation,
  ProjectOverview,
} from "./api.ts";

export const ARCHITECTURE_MODULE_LIMIT = 40;

export type ArchitecturePosition = { x: number; y: number };

export type ArchitectureDirectory = {
  group: OverviewGroup;
  expanded: boolean;
  position: ArchitecturePosition;
  width: number;
  height: number;
  visibleModules: Array<{ module: OverviewModule; position: ArchitecturePosition; highlighted: boolean }>;
  loadedModuleCount: number;
  omittedIndexModuleCount: number;
  omittedByCanvasLimit: number;
  internalRelationCount: number;
  internalEdgeCount: number;
  internalHttpAssociationCount: number;
  highlighted: boolean;
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
};

export function buildArchitectureGraph(
  overview: ProjectOverview,
  options: {
    expandedGroupIds?: string[];
    selectedHttpGroupId?: string | null;
    selectedGroupId?: string | null;
    relationKinds?: Array<"imports" | "calls">;
    showHttpAssociations?: boolean;
    maxVisibleModules?: number;
  } = {},
): ArchitectureGraphModel {
  const indexId = overview.index.index_id;
  const directoryGroups = overview.groups
    .filter((group) => group.group_kind === "source_directory" && group.index_id === indexId)
    .sort((left, right) => left.group_key.localeCompare(right.group_key) || left.group_id.localeCompare(right.group_id));
  const groupById = new Map(directoryGroups.map((group) => [group.group_id, group]));
  const moduleById = new Map(
    overview.modules
      .filter((module) => module.index_id === indexId && module.symbol.index_id === indexId)
      .map((module) => [module.module_id, module]),
  );
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

  const expandedGroupIds = [...new Set(options.expandedGroupIds ?? [])].filter((groupId) => groupById.has(groupId));
  const expandedGroups = new Set(expandedGroupIds);
  const maxVisibleModules = Math.max(0, Math.min(options.maxVisibleModules ?? ARCHITECTURE_MODULE_LIMIT, ARCHITECTURE_MODULE_LIMIT));
  const visibleModuleIdsByGroup = new Map<string, string[]>();
  let remainingModuleSlots = maxVisibleModules;
  for (const groupId of expandedGroupIds) {
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

  const selectedHttpGroup = overview.groups.find(
    (group) => group.group_kind === "http_path" && group.group_id === options.selectedHttpGroupId && group.index_id === indexId,
  ) ?? null;
  const httpModuleIds = new Set(selectedHttpGroup?.module_ids ?? []);
  const relationKinds = new Set(options.relationKinds ?? ["imports", "calls"]);
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
    const sourceVisible = visibleModuleIds.has(relation.source_module_id);
    const targetVisible = visibleModuleIds.has(relation.target_module_id);
    const source = sourceVisible ? relation.source_module_id : sourceGroupId;
    const target = targetVisible ? relation.target_module_id : targetGroupId;
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
      const sourceVisible = visibleModuleIds.has(association.client_module.symbol_id);
      const targetVisible = visibleModuleIds.has(association.server_module.symbol_id);
      const sourceExpandedButHidden = expandedGroups.has(sourceGroupId) && !sourceVisible;
      const targetExpandedButHidden = expandedGroups.has(targetGroupId) && !targetVisible;
      if (sourceExpandedButHidden || targetExpandedButHidden) summarizedHttpAssociationCount += 1;
      const source = sourceVisible ? association.client_module.symbol_id : sourceGroupId;
      const target = targetVisible ? association.server_module.symbol_id : targetGroupId;
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

  const groupColumnCount = 4;
  const groupWidth = 350;
  const groupColumnGap = 370;
  const groupRowGap = 20;
  const groupHeights = directoryGroups.map((group) => {
    const visibleCount = visibleModuleIdsByGroup.get(group.group_id)?.length ?? 0;
    return visibleCount > 0 ? 82 + Math.ceil(visibleCount / 2) * 80 : 96;
  });
  const rowHeights: number[] = [];
  for (let index = 0; index < directoryGroups.length; index += 1) {
    const row = Math.floor(index / groupColumnCount);
    rowHeights[row] = Math.max(rowHeights[row] ?? 0, groupHeights[index]);
  }
  const rowOffsets: number[] = [];
  rowHeights.forEach((_, row) => {
    rowOffsets[row] = (rowOffsets[row - 1] ?? 24) + (row === 0 ? 0 : groupRowGap + rowHeights[row - 1]);
    if (row === 0) rowOffsets[row] = 24;
  });

  const selectedGroup = groupById.get(options.selectedGroupId ?? "") ?? null;
  const focusGroupIds = new Set<string>();
  if (selectedGroup) {
    focusGroupIds.add(selectedGroup.group_id);
  }
  if (selectedHttpGroup) {
    for (const moduleId of httpModuleIds) {
      const groupId = groupByModuleId.get(moduleId);
      if (groupId) focusGroupIds.add(groupId);
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
  const directories = directoryGroups.map((group, index): ArchitectureDirectory => {
    const moduleIds = moduleIdsByGroup.get(group.group_id) ?? [];
    const loadedModules = moduleIds.filter((moduleId) => moduleById.has(moduleId));
    const visibleModules = visibleModuleIdsByGroup.get(group.group_id) ?? [];
    const column = index % groupColumnCount;
    const row = Math.floor(index / groupColumnCount);
    const visibleModuleViews = visibleModules.map((moduleId, moduleIndex) => {
      const module = moduleById.get(moduleId)!;
      visibleModuleCount += 1;
      const routeHighlighted = httpModuleIds.size === 0 || httpModuleIds.has(moduleId);
      if (focusGroupIds.has(group.group_id)) focusNodeIds.add(moduleId);
      return {
        module,
        position: {
          x: 12 + (moduleIndex % 2) * 166,
          y: 67 + Math.floor(moduleIndex / 2) * 80,
        },
        highlighted: routeHighlighted,
      };
    });
    const internal = internalByGroup.get(group.group_id) ?? { relationCount: 0, edgeCount: 0 };
    return {
      group,
      expanded: expandedGroups.has(group.group_id),
      position: { x: 24 + column * groupColumnGap, y: rowOffsets[row] ?? 24 },
      width: groupWidth,
      height: groupHeights[index],
      visibleModules: visibleModuleViews,
      loadedModuleCount: loadedModules.length,
      omittedIndexModuleCount: moduleIds.length - loadedModules.length,
      omittedByCanvasLimit: expandedGroups.has(group.group_id) ? loadedModules.length - visibleModules.length : 0,
      internalRelationCount: internal.relationCount,
      internalEdgeCount: internal.edgeCount,
      internalHttpAssociationCount: internalHttpByGroup.get(group.group_id) ?? 0,
      highlighted: httpModuleIds.size === 0 || moduleIds.some((moduleId) => httpModuleIds.has(moduleId)),
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
  };
}
