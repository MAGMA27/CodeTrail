# CodeTrail

理解项目结构，追踪代码关系，用源码证据调查 Issue。

CodeTrail 提供项目工作区和可展开的架构画布；Codex 可以通过 MCP 参与调查，结果实时显示在网页中。项目共享代码索引，不同 Session 各自保存问题、执行记录和结论。

## 快速开始

需要 Node.js、pnpm、uv 和 Python 3.10–3.13。克隆仓库后，在仓库根目录执行以下命令（Windows PowerShell）：

```powershell
pnpm install --frozen-lockfile
uv tool install ./apps/api
codetrail init
codetrail api
```

另开一个终端，在仓库根目录启动网页：

```powershell
pnpm web:dev
```

打开 [localhost:3000](http://localhost:3000)。初始化会生成本地配置，已有配置和项目数据保持不变。若找不到 `codetrail` 命令，执行 `uv tool update-shell` 后重新打开终端。

## 使用项目工作区

### 导入项目

点击左侧“+ 新建”，填写名称，选择本地目录或公开 GitHub 仓库。GitHub 导入默认开启；等待索引完成后打开项目。

首次初始化默认允许导入当前目录。要导入其他本地目录，按[本地路径配置](apps/api/mcp.md)添加允许的目录。

### 浏览架构

打开项目知识页，从职责或目录总览开始：

- 展开容器查看模块，折叠回整体视图。
- 选中模块查看函数、类及局部关系。
- 点击入口、公共 API 或扩展点的证据，查看源码与行号。

浏览层级为 **项目 → 职责/目录 → 模块 → 符号 → 源码**。使用聚焦和过滤查看相关部分，避免一次展开整份代码库。

### 用 Codex 调查 Issue

先连接一次 MCP（需要 Codex CLI）：

```powershell
codetrail install
```

重连 MCP 或重启 Codex 客户端后：

1. 在工作台选择项目，新建 Session，输入并保存 Issue。
2. 在 Codex 中要求调查该项目和 Session，例如：

   > 使用 CodeTrail MCP 调查“我的项目”中刚保存 Issue 的 Session。核对项目和固定索引，创建调查 Run，给出问题位置、源码证据和影响范围；证据不足时明确说明。

3. 保持 Session 页面打开，查看实时工具调用、调查报告和源码证据。
4. 从候选位置打开影响范围，继续浏览相关代码。

网页保存消息不会自动启动 Codex；需要在 Codex 中发出调查请求。使用这条流程无需额外配置模型供应商密钥。其他分析模式见[运行说明](apps/api/execution.md)。

### 更新与继续调查

源码变化后，在索引管理中点击“检查更新”，再显式更新索引。旧 Session 和报告继续保留原代码快照；分析新版本时创建新的 Session。

项目、消息和报告会持久化，切换或刷新后可继续查看。停止服务时，在 API 和网页终端按 `Ctrl+C`。

## 当前能力

支持 Python、TypeScript/TSX 的定义、导入和保守静态调用分析，以及架构聚合、源码钻取和有证据的 Issue 调查。

动态调用与无法解析的关系会保留为未知。静态可达不等于已确认影响，报告也不代替运行时复现。CodeGraph/Archify 是设计参考，当前并非完整复现。

## 当前架构与演进规划

当前 Web 工作台通过 HTTP 使用 FastAPI API，MCP server 作为独立 stdio 入口复用应用服务。GitHub 导入/重试/清理重试、GitHub 刷新与索引重建以后台 job 提交；默认轻量模式使用 SQLite 和 API 进程内线程执行，不提供进程崩溃恢复保证。完整模式设置 `CODETRAIL_REDIS_URL` 并启动 `codetrail worker`，或使用仓库根目录 `docker compose up --build` 启动 Redis、API、worker 和 Web；任务事实仍保存在共享 SQLite 数据卷。Compose 当前不包含 PostgreSQL，PR 3 再引入 PostgreSQL/Alembic。Redis 分布式协调与缓存仍属 PR 2；[后端基础设施演进提案](docs/decisions/2026-10-28-backend-infrastructure-evolution.md)保持 proposed，实施细节与验证状态见 API/Web 就近文档。

## 配置与开发

- [CLI、共享配置与 MCP](apps/api/mcp.md)：允许导入路径、编译器、GitHub token、模型配置和故障排查。
- [工作台说明](apps/web/workbench.md) · [API 说明](apps/api/README.md)
- [开发者联合操作流程](docs/mvp-operation.md) · [MVP 验收清单](docs/mvp-acceptance.md)
- [样本与离线演示](fixtures/README.md) · [评测](evals/README.md)
- [开发规范](AGENTS.md) · [设计决策](docs/decisions/README.md) · [子系统导航](docs/subsystems/README.md)

修改源码后需要更新已安装的 API/MCP：`uv tool install --force ./apps/api`。开发环境初始化与检查命令见 [API](apps/api/README.md) 和 [Web](apps/web/README.md)。
