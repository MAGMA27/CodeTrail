export const SIDEBAR_WIDTH_STORAGE_KEY = "codetrail.sidebar-width.v1";
export const DEFAULT_SIDEBAR_WIDTH = 264;
export const MIN_SIDEBAR_WIDTH = 220;
export const MAX_SIDEBAR_WIDTH = 480;

export type SidebarWidthRange = { min: number; max: number };

export function normalizeSidebarWidth(width: number): number {
  if (!Number.isFinite(width)) return DEFAULT_SIDEBAR_WIDTH;
  return Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, Math.round(width)));
}

export function readSidebarWidth(value: string | null): number | null {
  if (value === null || value.trim() === "") return null;
  const width = Number(value);
  return Number.isFinite(width) ? normalizeSidebarWidth(width) : null;
}

export function resolveSidebarWidthRange(workspaceWidth: number, reservedContentWidth: number): SidebarWidthRange {
  const max = Math.min(MAX_SIDEBAR_WIDTH, Math.max(0, Math.floor(workspaceWidth - reservedContentWidth)));
  return { min: Math.min(MIN_SIDEBAR_WIDTH, max), max };
}

export function clampSidebarWidth(width: number, range: SidebarWidthRange): number {
  if (!Number.isFinite(width)) return range.min;
  return Math.min(range.max, Math.max(range.min, Math.round(width)));
}
