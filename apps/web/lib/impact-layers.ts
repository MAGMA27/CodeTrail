import type { ImpactLayerKind, RunImpactResponse } from "./api";

export const IMPACT_LAYER_LABELS: Record<ImpactLayerKind, string> = {
  report_anchor: "报告锚点",
  static_direct: "直接静态可达",
  static_indirect: "间接静态可达",
  unknown: "未知边界",
};

export type ImpactPanelStatus = "idle" | "loading" | "success" | "empty" | "error";

export type ImpactPanelView = {
  mode: "idle" | "loading" | "error" | "unmapped" | "result";
  headline: string;
  showRetry: boolean;
  showGraph: boolean;
  showUnmapped: boolean;
  showTruncation: boolean;
  truncationMessage: string | null;
  emptyStatic: boolean;
};

export function impactPanelStateAttribute(panel: ImpactPanelView): ImpactPanelView["mode"] | "truncated" {
  return panel.showTruncation ? "truncated" : panel.mode;
}

export function describeImpactPanel(input: {
  status: ImpactPanelStatus;
  impact: RunImpactResponse | null;
  canRetry: boolean;
}): ImpactPanelView {
  if (input.status === "loading") {
    return {
      mode: "loading",
      headline: "影响范围载入中",
      showRetry: false,
      showGraph: false,
      showUnmapped: false,
      showTruncation: false,
      truncationMessage: null,
      emptyStatic: false,
    };
  }
  if (input.status === "error") {
    return {
      mode: "error",
      headline: "影响范围请求失败",
      showRetry: input.canRetry,
      showGraph: false,
      showUnmapped: false,
      showTruncation: false,
      truncationMessage: null,
      emptyStatic: false,
    };
  }
  if (input.status === "idle" || input.impact === null) {
    return {
      mode: "idle",
      headline: "从 Run 报告选择一个候选或发现",
      showRetry: false,
      showGraph: false,
      showUnmapped: false,
      showTruncation: false,
      truncationMessage: null,
      emptyStatic: false,
    };
  }
  if (input.impact.mapping_status === "unmapped") {
    return {
      mode: "unmapped",
      headline: "没有可映射的唯一符号",
      showRetry: false,
      showGraph: false,
      showUnmapped: true,
      showTruncation: false,
      truncationMessage: null,
      emptyStatic: false,
    };
  }
  const truncated = input.impact.truncated;
  const emptyStatic = input.impact.reachable_node_count === 0 && !truncated && !input.impact.index_truncated;
  return {
    mode: "result",
    headline: truncated
      ? "影响分层已截断"
      : emptyStatic
        ? "当前方向没有静态 calls 可达节点"
        : `${input.impact.reachable_node_count} 个可达节点 · ${input.impact.paths.length} 条路径`,
    showRetry: false,
    showGraph: true,
    showUnmapped: false,
    showTruncation: truncated,
    truncationMessage: truncated
      ? `本次影响分层只包含已返回的结果，已截断：${input.impact.truncation_reason ?? "未提供原因"}。未返回的符号不代表没有影响，静态可达也不是已确认影响。`
      : null,
    emptyStatic,
  };
}
