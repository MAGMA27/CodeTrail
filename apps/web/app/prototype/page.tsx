"use client";

import {
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import styles from "./prototype.module.css";

type WorkspaceView = "knowledge" | "session";
type PanelKind = "evidence" | "graph" | null;
type DemoState = "empty" | "knowledge" | "empty-session" | "messages" | "evidence" | "graph" | "failure";
type SessionId = "timeout" | "imports";
type GraphFilter = "all" | "entry" | "feature" | "module";

type Message = {
  id: string;
  sequence: number;
  content: string;
  createdAt: string;
};

const sessionDetails: Record<SessionId, { title: string; updatedAt: string; description: string }> = {
  timeout: {
    title: "读取配置时的超时",
    updatedAt: "刚刚",
    description: "确认启动路径和环境变量读取的关系。",
  },
  imports: {
    title: "导入后的模块边界",
    updatedAt: "昨天",
    description: "梳理导入入口与配置模块的连接。",
  },
};

const initialMessages: Record<SessionId, Message[]> = {
  timeout: [
    {
      id: "message-1",
      sequence: 1,
      content: "启动时偶发超时，先确认 timeout 的读取路径。",
      createdAt: "10:42",
    },
    {
      id: "message-2",
      sequence: 2,
      content: "从入口函数开始看，不要先假设是网络请求的问题。",
      createdAt: "10:44",
    },
  ],
  imports: [],
};

const stateLabels: Record<DemoState, string> = {
  empty: "空工作区",
  knowledge: "项目知识页",
  "empty-session": "空会话",
  messages: "有消息会话",
  evidence: "证据面板",
  graph: "功能图面板",
  failure: "保存失败",
};

const graphItems = [
  { kind: "entry", label: "timeout_demo", detail: "__main__.py:18" },
  { kind: "feature", label: "读取运行配置", detail: "timeout_demo/config.py" },
  { kind: "module", label: "config.py", detail: "3 个符号" },
  { kind: "module", label: "__main__.py", detail: "2 个符号" },
] as const;

function stateClassName(...names: Array<string | false | null | undefined>): string {
  return names.filter(Boolean).join(" ");
}

export default function PrototypePage() {
  const [view, setView] = useState<WorkspaceView>("knowledge");
  const [selectedSession, setSelectedSession] = useState<SessionId>("timeout");
  const [demoState, setDemoState] = useState<DemoState>("knowledge");
  const [panel, setPanel] = useState<PanelKind>(null);
  const [panelWidth, setPanelWidth] = useState(360);
  const [graphFilter, setGraphFilter] = useState<GraphFilter>("all");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({
    timeout: "",
    imports: "",
  });
  const [messages, setMessages] = useState<Record<SessionId, Message[]>>(initialMessages);
  const [saving, setSaving] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saved" | "saving">("saved");
  const saveTimer = useRef<number | null>(null);

  const currentSession = sessionDetails[selectedSession];
  const currentDraft = drafts[selectedSession] ?? "";
  const currentMessages = messages[selectedSession] ?? [];
  const isEmptyWorkspace = demoState === "empty";
  const isFailureState = demoState === "failure";
  const workspaceStyle = { "--panel-width": `${panelWidth}px` } as CSSProperties;

  useEffect(() => {
    return () => {
      if (saveTimer.current !== null) {
        window.clearTimeout(saveTimer.current);
      }
    };
  }, []);

  const filteredGraphItems = useMemo(
    () => (graphFilter === "all" ? graphItems : graphItems.filter((item) => item.kind === graphFilter)),
    [graphFilter],
  );

  function changeDemoState(nextState: DemoState) {
    setDemoState(nextState);
    setMobileNavOpen(false);

    if (nextState === "empty") {
      setView("knowledge");
      setPanel(null);
      return;
    }

    if (nextState === "knowledge") {
      setView("knowledge");
      setPanel(null);
      return;
    }

    if (nextState === "evidence" || nextState === "graph") {
      setView("knowledge");
      setPanel(nextState === "evidence" ? "evidence" : "graph");
      return;
    }

    setView("session");
    setPanel(null);

    if (nextState === "empty-session") {
      setSelectedSession("imports");
      setSaveState("idle");
      return;
    }

    if (nextState === "failure") {
      setDrafts((current) => ({
        ...current,
        [selectedSession]: current[selectedSession] || "请重试保存这条调查说明。",
      }));
      setSaveState("idle");
    }
  }

  function selectSession(sessionId: SessionId) {
    setSelectedSession(sessionId);
    setView("session");
    setDemoState(sessionId === "timeout" && messages[sessionId].length > 0 ? "messages" : "empty-session");
    setPanel(null);
    setMobileNavOpen(false);
    setSaveState("saved");
  }

  function createSession() {
    setSelectedSession("imports");
    setView("session");
    setDemoState("empty-session");
    setPanel(null);
    setMobileNavOpen(false);
    setSaveState("idle");
  }

  function openKnowledge() {
    setView("knowledge");
    setDemoState("knowledge");
    setPanel(null);
    setMobileNavOpen(false);
  }

  function openPanel(kind: Exclude<PanelKind, null>) {
    setView("knowledge");
    setPanel(kind);
    setDemoState(kind === "evidence" ? "evidence" : "graph");
    setMobileNavOpen(false);
  }

  function closePanel() {
    setPanel(null);
    setDemoState(view === "knowledge" ? "knowledge" : "messages");
  }

  function updateDraft(value: string) {
    setDrafts((current) => ({ ...current, [selectedSession]: value }));
    if (saveState !== "idle") {
      setSaveState("idle");
    }
  }

  function saveMessage() {
    const content = currentDraft.trim();
    if (!content || saving) {
      return;
    }

    setSaving(true);
    setSaveState("saving");
    setDemoState("messages");
    setView("session");
    setPanel(null);

    saveTimer.current = window.setTimeout(() => {
      setMessages((current) => ({
        ...current,
        [selectedSession]: [
          ...current[selectedSession],
          {
            id: `message-${Date.now()}`,
            sequence: current[selectedSession].length + 1,
            content,
            createdAt: "刚刚",
          },
        ],
      }));
      setDrafts((current) => ({ ...current, [selectedSession]: "" }));
      setSaving(false);
      setSaveState("saved");
      setDemoState("messages");
      saveTimer.current = null;
    }, 420);
  }

  function handleComposerSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    saveMessage();
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      saveMessage();
    }
  }

  const saveLabel = saving ? "保存中" : saveState === "saved" ? "已保存" : "草稿";

  return (
    <div className={styles.prototype} style={workspaceStyle}>
      <header className={styles.topbar}>
        <button
          type="button"
          className={stateClassName(styles.mobileMenuButton, mobileNavOpen && styles.isPressed)}
          onClick={() => setMobileNavOpen((open) => !open)}
          aria-expanded={mobileNavOpen}
          aria-controls="prototype-sidebar"
        >
          项目
        </button>
        <div className={styles.brand} aria-label="CodeTrail 原型">
          <span className={styles.brandMark}>CT</span>
          <span className={styles.brandName}>CodeTrail</span>
        </div>
        <div className={styles.topbarContext}>
          <span className={styles.contextProject}>timeout-demo</span>
          <span className={styles.contextDivider}>/</span>
          <span>项目工作区</span>
        </div>
        <div className={styles.topbarActions}>
          <label className={styles.statePicker}>
            <span>预览状态</span>
            <select
              value={demoState}
              onChange={(event) => changeDemoState(event.target.value as DemoState)}
              aria-label="选择演示状态"
            >
              {Object.entries(stateLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <span className={styles.prototypeBadge}>设计原型</span>
        </div>
      </header>

      <div className={stateClassName(styles.workspace, panel && styles.withPanel)}>
        <aside
          id="prototype-sidebar"
          className={stateClassName(styles.sidebar, mobileNavOpen && styles.mobileNavOpen)}
          aria-label="项目和会话导航"
        >
          <div className={styles.sidebarHeader}>
            <span className={styles.sidebarLabel}>项目</span>
            <button type="button" className={styles.smallButton} onClick={() => changeDemoState("empty")}>
              新建
            </button>
          </div>

          {isEmptyWorkspace ? (
            <div className={styles.sidebarEmpty}>
              <span className={styles.emptyMark}>+</span>
              <p>还没有项目</p>
              <button type="button" className={styles.textButton} onClick={() => changeDemoState("knowledge")}>
                查看演示项目
              </button>
            </div>
          ) : (
            <>
              <button type="button" className={stateClassName(styles.projectItem, styles.selectedItem)} onClick={openKnowledge}>
                <span className={styles.projectGlyph}>TD</span>
                <span className={styles.projectCopy}>
                  <strong>timeout-demo</strong>
                  <small>本地仓库</small>
                </span>
                <span className={styles.readyMark} aria-label="索引就绪" />
              </button>

              <div className={styles.sessionNavigation}>
                <div className={styles.sidebarHeader}>
                  <span className={styles.sidebarLabel}>会话</span>
                  <button type="button" className={styles.addButton} onClick={createSession} aria-label="新建会话">
                    +
                  </button>
                </div>
                <button
                  type="button"
                  className={stateClassName(styles.sessionItem, selectedSession === "timeout" && view === "session" && styles.selectedSession)}
                  onClick={() => selectSession("timeout")}
                >
                  <span className={styles.sessionTitle}>{sessionDetails.timeout.title}</span>
                  <small>{sessionDetails.timeout.updatedAt}</small>
                </button>
                <button
                  type="button"
                  className={stateClassName(styles.sessionItem, selectedSession === "imports" && view === "session" && styles.selectedSession)}
                  onClick={() => selectSession("imports")}
                >
                  <span className={styles.sessionTitle}>{sessionDetails.imports.title}</span>
                  <small>{sessionDetails.imports.updatedAt}</small>
                </button>
              </div>
            </>
          )}

          <div className={styles.sidebarFooter}>
            <button type="button" className={stateClassName(styles.sidebarLink, view === "knowledge" && styles.activeLink)} onClick={openKnowledge}>
              <span>⌂</span>
              项目知识
            </button>
            <button type="button" className={styles.sidebarLink} onClick={() => changeDemoState("knowledge")}>
              <span>↗</span>
              索引与导入
            </button>
          </div>
        </aside>

        {mobileNavOpen ? <button type="button" className={styles.mobileScrim} onClick={() => setMobileNavOpen(false)} aria-label="关闭项目导航" /> : null}

        <main className={styles.mainColumn}>
          <div className={styles.mainScroll}>
            {isEmptyWorkspace ? <EmptyWorkspace onCreate={() => changeDemoState("knowledge")} /> : null}
            {!isEmptyWorkspace && view === "knowledge" ? <KnowledgePage onOpenPanel={openPanel} /> : null}
            {!isEmptyWorkspace && view === "session" ? (
              <SessionPage
                currentDraft={currentDraft}
                currentMessages={currentMessages}
                currentSession={currentSession}
                draftIsFailure={isFailureState}
                onBack={openKnowledge}
                onChangeDraft={updateDraft}
                onKeyDown={handleComposerKeyDown}
                onRetry={saveMessage}
                onSubmit={handleComposerSubmit}
                saveLabel={saveLabel}
                saving={saving}
              />
            ) : null}
          </div>
        </main>

        {panel ? (
          <InspectorPanel
            filteredGraphItems={filteredGraphItems}
            graphFilter={graphFilter}
            kind={panel}
            onClose={closePanel}
            onFilterChange={setGraphFilter}
            onOpenEvidence={() => openPanel("evidence")}
            onOpenGraph={() => openPanel("graph")}
            onWidthChange={setPanelWidth}
            panelWidth={panelWidth}
          />
        ) : null}
      </div>
    </div>
  );
}

function EmptyWorkspace({ onCreate }: { onCreate: () => void }) {
  return (
    <div className={styles.emptyWorkspace}>
      <div className={styles.emptyWorkspaceMark}>CT</div>
      <h1>从一个项目开始</h1>
      <p>项目知识、会话和固定版本会在这里集中。当前状态没有可浏览的项目。</p>
      <button type="button" className={styles.primaryButton} onClick={onCreate}>
        打开演示项目
      </button>
    </div>
  );
}

function KnowledgePage({ onOpenPanel }: { onOpenPanel: (kind: Exclude<PanelKind, null>) => void }) {
  return (
    <div className={styles.content}>
      <div className={styles.pageIntro}>
        <div>
          <p className={styles.eyebrow}>PROJECT KNOWLEDGE</p>
          <h1>timeout-demo</h1>
          <p className={styles.pageLead}>先看项目事实，再进入一个独立调查会话。</p>
        </div>
        <div className={styles.pageActions}>
          <button type="button" className={styles.secondaryButton}>
            导入源代码
          </button>
          <button type="button" className={styles.secondaryButton}>
            索引管理
          </button>
        </div>
      </div>

      <div className={styles.snapshotBar}>
        <span className={styles.snapshotStatus}>
          <span className={styles.readyMark} aria-hidden="true" />
          当前可用版本
        </span>
        <code>fixture-v1</code>
        <span className={styles.snapshotDivider} />
        <span>8 个模块</span>
        <span>2 个入口候选</span>
        <button type="button" className={styles.detailButton}>
          查看详情
        </button>
      </div>

      <nav className={styles.tabs} aria-label="项目内容" role="tablist">
        <button type="button" className={stateClassName(styles.tab, styles.activeTab)} role="tab" aria-selected="true">
          知识页
        </button>
        <button type="button" className={styles.tab} role="tab" onClick={() => onOpenPanel("graph")}>
          功能图
        </button>
        <button type="button" className={styles.tab} role="tab" onClick={() => onOpenPanel("graph")}>
          代码关系
        </button>
      </nav>

      <section className={styles.knowledgeSection}>
        <div className={styles.sectionHeading}>
          <div>
            <h2>从入口到功能</h2>
            <p>固定快照里的静态事实和语义线索分开呈现。</p>
          </div>
          <button type="button" className={styles.inlineButton} onClick={() => onOpenPanel("graph")}>
            打开功能图
            <span aria-hidden="true">↗</span>
          </button>
        </div>

        <div className={styles.relationshipMap}>
          <div className={styles.mapColumn}>
            <span className={styles.mapLabel}>入口</span>
            <button type="button" className={stateClassName(styles.mapNode, styles.mapNodeAccent)} onClick={() => onOpenPanel("evidence")}>
              <strong>timeout_demo</strong>
              <small>__main__.py:18</small>
            </button>
            <button type="button" className={styles.mapNode} onClick={() => onOpenPanel("evidence")}>
              <strong>main guard</strong>
              <small>__main__.py:25</small>
            </button>
          </div>
          <div className={styles.mapConnector} aria-hidden="true">
            <span />
            <small>调用</small>
            <span />
          </div>
          <div className={styles.mapColumn}>
            <span className={styles.mapLabel}>主要功能</span>
            <button type="button" className={stateClassName(styles.mapNode, styles.mapNodeFeature)} onClick={() => onOpenPanel("evidence")}>
              <strong>读取运行配置</strong>
              <small>config.py:3</small>
            </button>
            <button type="button" className={styles.mapNode} onClick={() => onOpenPanel("evidence")}>
              <strong>执行超时演示</strong>
              <small>timeout_demo.py:12</small>
            </button>
          </div>
        </div>
      </section>

      <div className={styles.knowledgeSplit}>
        <section className={styles.knowledgeSection}>
          <div className={styles.sectionHeading}>
            <div>
              <h2>项目功能</h2>
              <p>可继续钻取到模块、符号和源码证据。</p>
            </div>
          </div>
          <div className={styles.featureList}>
            <button type="button" className={styles.featureRow} onClick={() => onOpenPanel("evidence")}>
              <span className={styles.featureIndex}>01</span>
              <span>
                <strong>读取运行配置</strong>
                <small>从环境变量构造 endpoint 和 timeout。</small>
              </span>
              <span className={styles.rowArrow}>↗</span>
            </button>
            <button type="button" className={styles.featureRow} onClick={() => onOpenPanel("evidence")}>
              <span className={styles.featureIndex}>02</span>
              <span>
                <strong>执行超时演示</strong>
                <small>用固定入口演示配置缺失的错误路径。</small>
              </span>
              <span className={styles.rowArrow}>↗</span>
            </button>
          </div>
        </section>

        <section className={styles.knowledgeSection}>
          <div className={styles.sectionHeading}>
            <div>
              <h2>版本详情</h2>
              <p>所有证据都从这个不可变快照读取。</p>
            </div>
          </div>
          <dl className={styles.detailList}>
            <div>
              <dt>索引 ID</dt>
              <dd>idx_timeout_demo_01</dd>
            </div>
            <div>
              <dt>源码 hash</dt>
              <dd>sha256: 7e3a...d92b</dd>
            </div>
            <div>
              <dt>生成模式</dt>
              <dd>fixture / entry_reachable</dd>
            </div>
          </dl>
        </section>
      </div>

      <div className={styles.notice}>
        <span className={styles.noticeMark}>i</span>
        <p>演示数据只用于验收布局。真实页面继续使用现有 HTTP 契约，Session 当前只保存消息，Run 尚未接入。</p>
      </div>
    </div>
  );
}

function SessionPage({
  currentDraft,
  currentMessages,
  currentSession,
  draftIsFailure,
  onBack,
  onChangeDraft,
  onKeyDown,
  onRetry,
  onSubmit,
  saveLabel,
  saving,
}: {
  currentDraft: string;
  currentMessages: Message[];
  currentSession: (typeof sessionDetails)[SessionId];
  draftIsFailure: boolean;
  onBack: () => void;
  onChangeDraft: (value: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onRetry: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  saveLabel: string;
  saving: boolean;
}) {
  return (
    <div className={styles.sessionPage}>
      <div className={styles.sessionContent}>
        <button type="button" className={styles.backButton} onClick={onBack}>
          ← 项目知识
        </button>
        <div className={styles.sessionHeading}>
          <div>
            <div className={styles.sessionTitleLine}>
              <h1>{currentSession.title}</h1>
              <span className={styles.fixedVersion}>固定版本</span>
            </div>
            <p>{currentSession.description}</p>
          </div>
          <button type="button" className={styles.secondaryButton} onClick={onBack}>
            查看项目知识
          </button>
        </div>

        <div className={styles.sessionMeta}>
          <span>timeout-demo</span>
          <span className={styles.snapshotDivider} />
          <code>fixture-v1</code>
          <span className={styles.snapshotDivider} />
          <span>Session session_timeout_01</span>
        </div>

        <div className={styles.sessionRule} />

        <div className={styles.messageList} aria-live="polite">
          <button type="button" className={styles.olderMessages}>
            加载更早消息
          </button>
          {currentMessages.length === 0 ? (
            <div className={styles.emptySession}>
              <span className={styles.emptySessionMark}>+</span>
              <h2>从一个具体问题开始</h2>
              <p>这里保存调查主题和追问。发送消息只写入当前 Session，不会启动 Run。</p>
            </div>
          ) : (
            currentMessages.map((message) => (
              <article key={message.id} className={styles.messageRow}>
                <div className={styles.messageAuthor}>
                  <span className={styles.authorMark}>你</span>
                  <span>消息 {message.sequence}</span>
                </div>
                <div className={styles.messageBody}>
                  <p>{message.content}</p>
                  <time>{message.createdAt}</time>
                </div>
              </article>
            ))
          )}
          {draftIsFailure ? (
            <div className={styles.errorBanner} role="alert">
              <div>
                <strong>消息保存失败</strong>
                <p>草稿仍保留在当前会话，可以使用相同内容重试。</p>
              </div>
              <button type="button" className={styles.errorButton} onClick={onRetry}>
                重试保存
              </button>
            </div>
          ) : null}
        </div>

        <form className={styles.composer} onSubmit={onSubmit}>
          <label className={styles.srOnly} htmlFor="prototype-message-input">
            发送调查消息
          </label>
          <textarea
            id="prototype-message-input"
            value={currentDraft}
            onChange={(event) => onChangeDraft(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="描述你想确认的代码问题..."
            rows={3}
          />
          <div className={styles.composerFooter}>
            <p>Enter 发送，Shift+Enter 换行。输入法组合输入完成前不会发送。</p>
            <div className={styles.composerActions}>
              <span className={stateClassName(styles.saveState, saving && styles.isSaving)}>{saveLabel}</span>
              <button type="submit" className={styles.primaryButton} disabled={!currentDraft.trim() || saving}>
                发送消息
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}

function InspectorPanel({
  filteredGraphItems,
  graphFilter,
  kind,
  onClose,
  onFilterChange,
  onOpenEvidence,
  onOpenGraph,
  onWidthChange,
  panelWidth,
}: {
  filteredGraphItems: ReadonlyArray<(typeof graphItems)[number]>;
  graphFilter: GraphFilter;
  kind: Exclude<PanelKind, null>;
  onClose: () => void;
  onFilterChange: (filter: GraphFilter) => void;
  onOpenEvidence: () => void;
  onOpenGraph: () => void;
  onWidthChange: (width: number) => void;
  panelWidth: number;
}) {
  return (
    <aside className={styles.inspector} aria-label={kind === "evidence" ? "源码证据面板" : "功能图面板"}>
      <div className={styles.inspectorHeader}>
        <div>
          <span className={styles.sidebarLabel}>{kind === "evidence" ? "证据" : "关系"}</span>
          <h2>{kind === "evidence" ? "源码证据" : "功能图"}</h2>
        </div>
        <button type="button" className={styles.closeButton} onClick={onClose} aria-label="关闭面板">
          ×
        </button>
      </div>

      <div className={styles.inspectorTabs} role="tablist" aria-label="右侧面板类型">
        <button type="button" className={stateClassName(styles.inspectorTab, kind === "evidence" && styles.activeInspectorTab)} onClick={onOpenEvidence} role="tab" aria-selected={kind === "evidence"}>
          源码证据
        </button>
        <button type="button" className={stateClassName(styles.inspectorTab, kind === "graph" && styles.activeInspectorTab)} onClick={onOpenGraph} role="tab" aria-selected={kind === "graph"}>
          功能图
        </button>
      </div>

      {kind === "evidence" ? (
        <div className={styles.inspectorScroll}>
          <div className={styles.contextBlock}>
            <span className={styles.inspectorLabel}>固定上下文</span>
            <code>project_id: project-timeout-demo</code>
            <code>index_id: idx_timeout_demo_01</code>
            <code>generation_id: gen_fixture_01</code>
          </div>
          <div className={styles.sourceHeader}>
            <div>
              <strong>config.py</strong>
              <span>读取运行配置</span>
            </div>
            <span>3-8</span>
          </div>
          <pre className={styles.sourceCode} aria-label="源码片段">
            <code>
              <span className={styles.codeMuted}>3 </span>
              <span className={styles.codeKeyword}>def</span>{" "}
              <span className={styles.codeFunction}>load_settings</span>
              <span>():</span>{"\n"}
              <span className={styles.codeMuted}>4 </span>
              <span>    endpoint = os.environ[</span>
              <span className={styles.codeString}>"TIMEOUT_DEMO_ENDPOINT"</span>
              <span>]</span>{"\n"}
              <span className={styles.codeMuted}>5 </span>
              <span>    timeout = os.environ[</span>
              <span className={styles.codeString}>"TIMEOUT_DEMO_TIMEOUT"</span>
              <span>]</span>{"\n"}
              <span className={styles.codeMuted}>6 </span>
              <span>    </span>
              <span className={styles.codeKeyword}>return</span>
              <span> endpoint, timeout</span>
            </code>
          </pre>
          <div className={styles.evidenceNote}>
            <span className={styles.noticeMark}>i</span>
            <p>证据来自固定 index_id 的不可变源码快照。打开面板不会改变会话内容。</p>
          </div>
          <button type="button" className={styles.secondaryButtonFull} onClick={onOpenGraph}>
            在功能图中查看关系
          </button>
        </div>
      ) : (
        <div className={styles.inspectorScroll}>
          <div className={styles.graphToolbar}>
            <span className={styles.inspectorLabel}>关系来源</span>
            <div className={styles.filterGroup} role="group" aria-label="功能图筛选">
              {(["all", "entry", "feature", "module"] as const).map((filter) => (
                <button
                  type="button"
                  key={filter}
                  className={stateClassName(styles.filterButton, graphFilter === filter && styles.activeFilter)}
                  onClick={() => onFilterChange(filter)}
                >
                  {filter === "all" ? "全部" : filter === "entry" ? "入口" : filter === "feature" ? "功能" : "模块"}
                </button>
              ))}
            </div>
          </div>
          <div className={styles.graphLegend}>
            <span><i className={styles.legendSwatchStatic} />静态关系</span>
            <span><i className={styles.legendSwatchSemantic} />语义线索</span>
          </div>
          <div className={styles.graphList}>
            {filteredGraphItems.map((item) => (
              <button type="button" className={styles.graphItem} key={`${item.kind}-${item.label}`} onClick={onOpenEvidence}>
                <span className={stateClassName(styles.graphKind, item.kind === "feature" && styles.graphKindFeature)}>{item.kind}</span>
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.detail}</small>
                </span>
                <span className={styles.rowArrow}>↗</span>
              </button>
            ))}
          </div>
          <div className={styles.graphContext}>
            <strong>当前快照</strong>
            <p>只显示属于 timeout-demo / fixture-v1 的受限关系。展开节点不会读取最新索引。</p>
          </div>
        </div>
      )}

      <div className={styles.inspectorFooter}>
        <label htmlFor="panel-width">
          <span>面板宽度</span>
          <output>{panelWidth}px</output>
        </label>
        <input
          id="panel-width"
          type="range"
          min="300"
          max="440"
          step="10"
          value={panelWidth}
          onChange={(event) => onWidthChange(Number(event.target.value))}
          aria-label="调整面板宽度"
        />
      </div>
    </aside>
  );
}
