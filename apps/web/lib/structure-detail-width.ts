export const STRUCTURE_DETAIL_WIDTH_STORAGE_KEY = "codetrail.structure-detail-width.v1";
export const DEFAULT_STRUCTURE_DETAIL_WIDTH = 360;
export const MIN_STRUCTURE_DETAIL_WIDTH = 280;
export const MAX_STRUCTURE_DETAIL_WIDTH = 560;
const MIN_ARCHITECTURE_CANVAS_WIDTH = 480;
const WORKSPACE_GAP = 14;

export type StructureDetailWidthRange = { min: number; max: number };

export function normalizeStructureDetailWidth(width: number): number {
  if (!Number.isFinite(width)) return DEFAULT_STRUCTURE_DETAIL_WIDTH;
  return Math.min(MAX_STRUCTURE_DETAIL_WIDTH, Math.max(MIN_STRUCTURE_DETAIL_WIDTH, Math.round(width)));
}

export function readStructureDetailWidth(value: string | null): number | null {
  if (value === null || value.trim() === "") return null;
  const width = Number(value);
  return Number.isFinite(width) ? normalizeStructureDetailWidth(width) : null;
}

export function resolveStructureDetailWidthRange(workspaceWidth: number): StructureDetailWidthRange {
  const max = Math.min(MAX_STRUCTURE_DETAIL_WIDTH, Math.max(0, Math.floor(workspaceWidth - MIN_ARCHITECTURE_CANVAS_WIDTH - WORKSPACE_GAP)));
  return { min: Math.min(MIN_STRUCTURE_DETAIL_WIDTH, max), max };
}

export function clampStructureDetailWidth(width: number, range: StructureDetailWidthRange): number {
  if (!Number.isFinite(width)) return range.min;
  return Math.min(range.max, Math.max(range.min, Math.round(width)));
}
