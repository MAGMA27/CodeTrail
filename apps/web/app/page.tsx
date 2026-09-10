"use client";

import { useCallback, useEffect, useState } from "react";

type ConnectionState = "checking" | "connected" | "error";

type HealthResponse = {
  status: "ok";
  service: "codetrail-api";
  version: string;
};

const apiBaseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

export default function Home() {
  const [connectionState, setConnectionState] = useState<ConnectionState>("checking");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const checkHealth = useCallback(async () => {
    setConnectionState("checking");
    setHealth(null);
    setErrorMessage(null);

    try {
      const response = await fetch(`${apiBaseUrl}/api/health`, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`后端返回 HTTP ${response.status}`);
      }

      const payload: unknown = await response.json();
      if (!isHealthResponse(payload)) {
        throw new Error("后端返回了无法识别的健康状态");
      }

      setHealth(payload);
      setConnectionState("connected");
    } catch (error) {
      setConnectionState("error");
      setErrorMessage(error instanceof Error ? error.message : "无法连接到后端");
    }
  }, []);

  useEffect(() => {
    void checkHealth();
  }, [checkHealth]);

  const stateLabel = {
    checking: "检查中",
    connected: "后端已连接",
    error: "后端不可用",
  }[connectionState];

  return (
    <main className="shell">
      <section className="hero" aria-labelledby="page-title">
        <p className="eyebrow">ISSUE-DRIVEN CODE IMPACT ANALYSIS</p>
        <h1 id="page-title">CodeTrail</h1>
        <p className="lead">从 Issue 出发，看清代码关联与影响。</p>
      </section>

      <section className="status-card" data-state={connectionState} aria-live="polite">
        <div className="status-heading">
          <span className="status-dot" aria-hidden="true" />
          <div>
            <p className="card-label">服务状态</p>
            <h2>{stateLabel}</h2>
          </div>
        </div>

        {connectionState === "connected" && health ? (
          <p className="status-detail">
            {health.service} · v{health.version}
          </p>
        ) : null}

        {connectionState === "checking" ? (
          <p className="status-detail">正在检查 http://localhost:8000/api/health</p>
        ) : null}

        {connectionState === "error" ? (
          <p className="error-message">
            {errorMessage ?? "请确认 API 已启动，然后重新检查。"}
          </p>
        ) : null}

        <button type="button" onClick={() => void checkHealth()} disabled={connectionState === "checking"}>
          {connectionState === "checking" ? "检查中…" : "重新检查"}
        </button>
      </section>

      <section className="notice" aria-labelledby="notice-title">
        <p className="card-label">当前阶段</p>
        <h2 id="notice-title">分析功能尚未实现</h2>
        <p>当前版本只验证前后端启动与连接。代码索引、Issue 分析、图谱和报告将在后续阶段加入。</p>
      </section>
    </main>
  );
}

function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Record<string, unknown>;
  return (
    candidate.status === "ok" &&
    candidate.service === "codetrail-api" &&
    typeof candidate.version === "string"
  );
}
