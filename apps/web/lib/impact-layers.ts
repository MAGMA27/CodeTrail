import {
  ApiRequestError,
  RUN_IMPACT_REQUEST_TIMEOUT_MS,
  RequestTimeoutError,
  type ImpactLayerKind,
  type RunImpactResponse,
} from "./api.ts";

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
  const counts = `${input.impact.reachable_node_count} 个可达节点 · ${input.impact.paths.length} 条路径`;
  return {
    mode: "result",
    headline: emptyStatic ? "当前方向没有静态 calls 可达节点" : truncated ? `${counts} · 已截断` : counts,
    showRetry: false,
    showGraph: true,
    showUnmapped: false,
    showTruncation: truncated,
    truncationMessage: truncated
      ? `本次影响分层只包含已返回的结果，因${describeBudgetReasons(input.impact.truncation_reason)}而截断。未返回的符号不代表没有影响，静态可达也不是已确认影响。`
      : null,
    emptyStatic,
  };
}

const BUDGET_REASON_LABELS: ReadonlyArray<[RegExp, string]> = [
  [/^max_seconds exceeded/, "达到时间预算"],
  [/^max_paths reached/, "达到路径数上限"],
  [/^max_nodes reached/, "达到节点数上限"],
  [/^max_edges reached/, "达到关系边上限"],
  [/^max_unresolved reached/, "达到未知边界（unresolved）条数上限"],
  [/^max_output_bytes reached/, "达到输出大小上限"],
];

function splitReasons(reason: string | null | undefined): string[] {
  return (reason ?? "").split(/;\s*/).map((item) => item.trim()).filter(Boolean);
}

/** Chinese label for one server budget reason, keeping the raw reason as a secondary detail. */
export function impactBudgetReasonLabel(reason: string): string {
  const match = BUDGET_REASON_LABELS.find(([pattern]) => pattern.test(reason));
  const context = /while (collecting|resolving) contextual calls/.test(reason) ? "（上下文调用阶段）" : "";
  return match ? `${match[1]}${context}（${reason}）` : `其他限制（${reason}）`;
}

function describeBudgetReasons(reason: string | null | undefined): string {
  const reasons = splitReasons(reason);
  return reasons.length > 0 ? reasons.map(impactBudgetReasonLabel).join("、") : "未提供原因";
}

/** Coverage note for a truncated pinned index, naming the budget that was actually hit. */
export function impactIndexTruncationMessage(reason: string | null | undefined): string {
  const reasons = splitReasons(reason);
  const timeLimited = reasons.some((item) => item.startsWith("max_seconds"));
  const budgetLimited = reasons.some((item) => /^max_(edges|unresolved|output_bytes|nodes|paths)/.test(item));
  const cause = timeLimited && budgetLimited
    ? "同时达到关系分析的时间预算和关系预算"
    : timeLimited
      ? "关系分析达到时间预算"
      : budgetLimited
        ? "关系分析达到关系预算"
        : "覆盖不完整";
  return `影响查询使用的固定索引${cause}：${describeBudgetReasons(reason)}；空邻接不能证明没有关系。`;
}

export function impactUnresolvedTruncationMessage(reason: string | null | undefined): string {
  return `影响查询使用的 unresolved 详情已截断：${describeBudgetReasons(reason)}；未返回的 unresolved 不能被当作不存在。`;
}

export type ImpactErrorView = { message: string; detail: string | null };

/** The response answered a different project/session/run/index than the one requested. */
export class ImpactScopeMismatchError extends Error {
  constructor(detail: string) {
    super(detail);
    this.name = "ImpactScopeMismatchError";
  }
}

export type ImpactScope = { projectId: string; sessionId: string; runId: string; indexId?: string };

export function impactScopeMismatch(response: RunImpactResponse, expected: ImpactScope): string | null {
  const mismatches = [
    response.project_id !== expected.projectId ? "project_id" : null,
    response.session_id !== expected.sessionId ? "session_id" : null,
    response.run_id !== expected.runId ? "run_id" : null,
    expected.indexId !== undefined && response.index_id !== expected.indexId ? "index_id" : null,
  ].filter((item): item is string => item !== null);
  return mismatches.length > 0 ? `不一致字段：${mismatches.join(", ")}` : null;
}

function isTimeoutError(error: unknown): boolean {
  return error instanceof RequestTimeoutError || (error instanceof Error && error.name === "TimeoutError");
}

export function impactErrorView(error: unknown, timeoutMs = RUN_IMPACT_REQUEST_TIMEOUT_MS): ImpactErrorView {
  if (isTimeoutError(error)) {
    return {
      message: `影响查询超时（${Math.round(timeoutMs / 1000)} 秒内没有返回）。服务端可能繁忙或暂时不可达，可以重试。`,
      detail: "timeout",
    };
  }
  if (error instanceof ImpactScopeMismatchError) {
    return {
      message: "返回的影响结果不属于当前选择的 Run，已拒绝显示。请重试。",
      detail: error.message,
    };
  }
  if (error instanceof ApiRequestError) {
    const detail = `${error.code} · HTTP ${error.status} · ${error.message}`;
    if (error.code === "not_found" || error.status === 404) {
      return { message: "找不到这个 Run、报告、候选或发现，可能已被移除或不属于当前会话。", detail };
    }
    if (error.code === "invalid_input" || error.status === 400 || error.status === 422) {
      return { message: "影响查询参数无效，请重新从 Run 报告选择候选或发现。", detail };
    }
    if (error.status >= 500) {
      return {
        message: /storage failed/.test(error.message)
          ? "读取 Run 固定快照的本地存储失败，请稍后重试。"
          : `服务端暂时不可用（HTTP ${error.status}），请稍后重试。`,
        detail,
      };
    }
    return { message: `服务端拒绝了影响查询（HTTP ${error.status}）。`, detail };
  }
  if (error instanceof TypeError) {
    return { message: "无法连接 API，请确认服务已启动、网络可用后重试。", detail: error.message };
  }
  if (error instanceof Error && error.message) {
    return { message: error.message, detail: null };
  }
  return { message: "影响范围请求失败，请稍后重试。", detail: null };
}
