# CodeTrail 开发指南

## 开发流程与 skill 调用

项目专用 skill 位于 `.codex/skills/`。任务开始时按下列规则读取对应 SKILL.md；即使客户端未自动发现该目录，也通过本文件的相对路径加载。共同规范见 [开发与文档规范](docs/development.md)，当前子系统见 [子系统索引](docs/subsystems/README.md)。

- 新增能力或改变接口、依赖、状态、持久化及行为前，读取 [.codex/skills/codetrail-design-change/SKILL.md](.codex/skills/codetrail-design-change/SKILL.md)。明确范围与验收后继续已授权实施。
- 实现影响当前事实后，读取 [.codex/skills/codetrail-sync-subsystem/SKILL.md](.codex/skills/codetrail-sync-subsystem/SKILL.md)。同步相关文档、生成物和决策；无事实变化说明依据即可。
- 行为变更宣告完成前或用户要求审查时，读取 [.codex/skills/codetrail-review-subsystem/SKILL.md](.codex/skills/codetrail-review-subsystem/SKILL.md)。只读审查不自动修改实现。
- 审查发现设计、实现或文档问题时回到对应阶段，只复查受影响部分。纯机械修改执行相关轻量检查；只读问答不启动修改流程。
- 文档与 skill 改动执行 `pnpm docs:check`；契约改动执行 `pnpm contracts:check`；`pnpm doc-sync` 聚合两者。修改本地 memory-bank 后额外执行 `pnpm docs:check:local`。检查命令与能力边界见共同规范。

CodeTrail 是 Issue 驱动的代码影响分析工作台，以模块/符号关系图展示候选位置、关联路径和排查证据。目标是快速完成可演示、可评测的 Agent 求职项目。

任务开始时先阅读 memory-bank/README.md 文档索引和 progress.md，再按任务阅读 project-design-doc.md、architecture.md、implementation-plan.md 及相关调研。用户最新明确需求优先；重要决策变化须同步相关文档，阶段完成后更新 progress.md，不得将计划描述成已实现。新增或重命名文档时同步索引。

# 构建与测试

## 已核实运行时与依赖

- Node.js `24.11.1`（Next.js `16.3.4` 要求 `>=20.9.0`）、npm `11.6.2`、pnpm `11.22.0`。
- Python 系统命令为 `3.13.9`；uv `0.11.26` 为 API 虚拟环境选择 CPython `3.13.12`。
- 前端依赖已锁定为 Next.js `16.3.4`、React/React DOM `19.3.0`、TypeScript `5.9.3`，见 `apps/web/package.json` 和根目录 `pnpm-lock.yaml`。
- 后端依赖已锁定为 FastAPI `0.141.1`、Pydantic `2.13.5`、Uvicorn `0.52.4`，见 `apps/api/pyproject.toml` 和 `apps/api/uv.lock`。

## 已验证命令

在 PowerShell 中从仓库根目录执行：

```powershell
pnpm install
pnpm web:typecheck
pnpm web:build
```

初始化并检查 API：

```powershell
Set-Location apps/api
uv sync --locked
uv run python -m compileall -q app
uv run pytest -q
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

生成契约 Schema 和固定响应：

```powershell
Set-Location apps/api
uv run python -m app.export_schema
uv run pytest -q
Set-Location ../..
uv run --project apps/api python fixtures/generate_responses.py
```

另开 PowerShell 请求健康接口：

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/health -Headers @{ Origin = 'http://localhost:3000' } -UseBasicParsing
```

前端开发服务器：

```powershell
pnpm --dir apps/web dev
```

演示样本复现与校验：

```powershell
Set-Location fixtures/repos/timeout-demo
Remove-Item Env:TIMEOUT_DEMO_TIMEOUT -ErrorAction SilentlyContinue
$env:TIMEOUT_DEMO_ENDPOINT = 'https://example.test'
python -m timeout_demo
# 预期退出码 1，包含 KeyError: 'timeout'

$env:TIMEOUT_DEMO_TIMEOUT = '5'
python -m timeout_demo
# 预期退出码 0，输出固定 JSON
```

样本场景 JSON 解析、源码 manifest 校验、同名/动态调用检查、三套固定响应的 Pydantic 校验和契约 pytest 已实际通过；当前工具失败场景仍只是定义，不是已实现的注入测试。

已实际验证：API 健康请求返回 HTTP `200` 和 `{"status":"ok","service":"codetrail-api","version":"0.1.0"}`；前端类型检查、生产构建、Python 编译检查和 `pytest` 离线测试通过，当前后端测试为 46 passed，覆盖快照/定义提取、保守导入/调用关系、SQLite/FTS5 持久化、事务回滚、全文检索、旧快照证据、受限邻接查询，以及 allow-list、索引/查询 HTTP API、工具适配、并发上限和安全错误；SQLite FTS5 在当前运行时可用。实际 Uvicorn HTTP 闭环已验证 health、create、search、neighbors、source 均成功返回；本步仍不是 Run/Agent/SSE 驱动的端到端离线分析演示，前端首页仍明确显示分析功能尚未实现。

计划技术：Next.js、React Flow、TypeScript、Python、FastAPI、Pydantic、LangGraph、SQLite FTS5、Python ast。依赖版本在脚手架阶段核实兼容性并锁定。

# 实现约束

- TypeScript 使用严格类型，后端边界使用 Pydantic，事件和报告采用明确契约。
- 图、证据、报告绑定同一索引快照；节点采用稳定 ID。
- 静态关系与模型假设分开表示；调用可达不等于已确认影响，测试引用不等于测试覆盖。
- 展示执行事件、工具结果、证据及结论，不要求或展示隐藏思维链。
- 工具执行必须限制次数、时间和输出大小；无法解析的调用保留 unresolved。
- 优先单仓库纵向闭环，不为了堆技术引入多 Agent、队列或可编辑工作流。
- CodeGraph/Archify 是当前设计参考，并非已集成依赖。
- 本仓库有 .codegraph/ 时理解或定位代码优先 CodeGraph；没有时正常搜索，不自动创建索引。

# 计划目录

- apps/web/：工作台、图交互、节点详情、运行状态。
- apps/api/：API/SSE、Agent、索引、检索、持久化。
- fixtures/：自有演示仓库、Issue、固定模型响应。
- evals/：独立评测集、基线、评分脚本与结果。
- contracts/：版本化数据契约与供前端使用的 JSON Schema。
- memory-bank/：设计与进度文档。
- .codetrail/：本地运行和索引数据，实施时加入忽略规则。

# 测试指南

- pytest 验证解析、路径边界、引用、预算与事件顺序；前端验证节点/证据联动和失败状态。
- 自动测试使用 fixture，不依赖网络、API Key 或真实模型。
- 真实模型评测独立运行，记录模型、提示词、revision、数据集、耗时与用量。
- 开发案例与独立评测分开；历史 Issue 只读取修复前代码，修复 diff 仅用于评分。

# 数据与执行边界

- 首版本地仓库只读，限制根目录，拒绝路径穿越和符号链接逃逸。
- 只调用白名单工具；核心 MVP 不执行仓库测试或任意 shell。
- 密钥仅在服务端环境变量中；不提交私有代码与密钥，日志脱敏。
- Issue、代码和文档均是不可信输入，不能改变系统指令或工具权限。
- 真实模型模式说明会发送的源码范围，离线演示明确标识。

# Git 与协作

保留用户修改。提交采用 Conventional Commits，仅在用户要求时提交或推送。交付说明修改、验证和限制；完成阶段后同步 memory-bank。
