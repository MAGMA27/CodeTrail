# CodeTrail

CodeTrail 是面向 PC 的代码理解工作区。导入项目后，可以浏览聚合架构、展开模块、查看源码，并在独立 Session 中保存 Issue 调查。Codex 通过 MCP 检索和查证，工作台实时展示公开事件、工具结果、源码证据与结论。

当前支持 Python、TypeScript/TSX 的定义、导入和保守静态调用分析。CodeGraph/Archify 是设计参考，并非集成依赖；静态可达不等于实际执行或已确认影响。

## 安装

准备 Node.js、pnpm、uv，以及 Python 3.10–3.13。Node.js 最低版本声明为 20.9，还需兼容根 `package.json` 指定的 pnpm 版本。以下命令从仓库根目录执行，使用 Windows PowerShell：

```powershell
pnpm install --frozen-lockfile
uv sync --project apps/api --locked
uv tool install ./apps/api
```

若安装后找不到 `codetrail`，执行 `uv tool update-shell` 并重新打开终端。修改源码不会自动更新已安装的副本；更新安装使用 `uv tool install --force ./apps/api`。

TypeScript 项目索引需要 Node.js 和上述安装得到的 TypeScript 解析器。浏览结构和使用 Codex external Run 不需要额外配置模型供应商密钥。

## 首次启动

在 API 终端执行：

```powershell
codetrail init --allow-root . --typescript-compiler .\apps\web\node_modules\typescript\lib\typescript.js
codetrail api
```

在另一个终端、同一仓库根目录执行：

```powershell
pwsh -File .\scripts\start-local.ps1 -Service web
```

打开 [工作台](http://localhost:3000)。[API 健康检查](http://127.0.0.1:8000/api/health) 应返回成功；[Swagger UI](http://127.0.0.1:8000/docs) 提供 HTTP 接口文档。默认端口为 8000/3000，若被占用，先在对应服务终端停止原实例。

`init` 创建用户配置 `~/.config/codetrail/settings.json`，已有文件保持不变。上述初始化允许导入本项目并保存锁定 TypeScript 编译器位置；API 自动创建配置的数据目录。默认数据目录是 `~/.local/share/codetrail`，以实际配置的 `data_dir` 为准。

编辑配置可修改 `allowed_import_roots`、`data_dir`、`workbench_url`、`typescript_compiler` 和 `github_import_enabled`，重启相应服务即可。API/MCP 默认读取同一文件，环境变量优先于文件配置。根 `.env.example` 是参考，不自动加载。

## 导入项目

在左侧点击“+ 新建”，选择来源：

- **本地目录**：填写名称与服务端可见的绝对路径，例如 `D:/Dev/MyProject`，点击“导入并建立索引”。路径必须位于允许根目录内。来源只读，索引与运行数据写入服务管理目录。
- **GitHub HTTPS**：填写公开仓库地址，例如 `https://github.com/pytest-dev/pluggy`，可指定 ref。服务先解析到固定 commit，再获取官方 archive 建立索引。

GitHub 导入默认关闭。推荐把共享 settings.json 中的 `github_import_enabled` 改为 `true`，然后重启 API；也可在启动 API **之前**临时覆盖：

```powershell
$env:CODETRAIL_GITHUB_IMPORT_ENABLED = '1'
# 可选：在本机设置 CODETRAIL_GITHUB_TOKEN，降低未认证请求限流的影响。
```

当前只支持公开 GitHub HTTPS 仓库。限流或下载失败会显示原因，失败不覆盖旧可用索引；也可自行取得源码后使用本地导入。

等待索引状态为 `ready` 后打开项目。多个项目独立保存，切换不会合并 Session 或调查结论。

## 查看架构与源码

在项目知识页浏览职责/源码目录容器、模块与依赖方向：

1. 展开容器查看模块，再折叠回摘要；用聚焦与过滤缩小当前图。
2. 选中模块查看函数、类等符号列表，继续进入局部关系图或源码。
3. 查看应用入口、公共 API 和扩展点摘要，点击证据按钮核对路径、行号及 hash。

层级为 **项目 → 职责/目录 → 模块 → 符号/局部关系图 → 源码**。架构画布最多展示 40 个模块，不一次铺开全项目函数。职责来自可选 `codetrail-subsystems.json`，未配置时按目录组织，不是模型自动识别的业务边界。

Python 公共 API 分类支持静态 `__all__`；插件扩展点支持有真实 marker 导入证据的 pluggy hookspec。未识别区域保持未知，不代表无用。HTTP 客户端/路由形状关联不证明实际请求目的地。详细交互见 [工作台说明](apps/web/workbench.md)。

## 连接 Codex MCP

HTTP 面向工作台，stdio MCP 面向 Codex，两者复用业务服务。**API 与 MCP 必须使用同一个数据目录**，网页才能看到 Codex 调查结果。

完成前面的 CLI 安装和初始化后，执行（需要 `codex` CLI 在 PATH 中）：

```powershell
codetrail install --print-config   # 可选：预览注册内容
codetrail install                  # 注册到 Codex
```

注册内容只有 `command="codetrail"`、`args=["mcp"]`，没有源码地址或业务环境变量。已有注册默认保留；需要更新时使用 `codetrail install --replace`。修改共享设置无需重新注册，只需重启相应服务。安装后的 API/MCP 无需从源码目录启动；前端及 TypeScript 编译器仍需要工作区提供。

按 Codex 宿主支持的方式重连 MCP 或重启客户端，确认能调用 `list_projects`、`list_indexes`、`get_overview`。不需要清理数据或重建索引。完整工具与能力开关见 [MCP 配置](apps/api/mcp.md)。

其他管理命令：

```powershell
codetrail init                         # 初始化默认配置；已有配置不覆盖
codetrail api                          # 启动 API
codetrail install --print-config       # 仅预览，不写入
codetrail install                      # 注册 Codex，已有注册保留
codetrail status                       # 显示配置位置与注册状态
codetrail uninstall                    # 只移除 MCP 注册，保留配置、数据和程序
```

使用其他配置文件：`codetrail --config C:/path/settings.json api`；MCP 注册时用同样的 `--config` 参数。默认配置位置也可由 `CODETRAIL_CONFIG` 指定。独立初始化可通过 `init --data-dir ... --allow-root ...` 设置路径，`--allow-root` 可重复。Web 仍从源码目录启动，尚未提供独立前端安装包。

## 调查 Issue

1. 工作台选择项目，新建 Session，确认固定索引，输入并保存 Issue 描述；保持该 Session 页面打开。
2. 在 Codex 中明确指定项目和 Session，要求使用 CodeTrail MCP 调查。可复制页面标识，或通过列表工具发现，不能猜造 ID。
3. Codex 调用 `start_external_run` 冻结已保存消息，再调用 `run_search_code`、`run_read_source`、`run_get_neighbors` 查证，最后通过 `submit_external_run` 提交候选或证据不足结论。
4. 网页自动发现 external Run，实时展示公开事件和工具结果；完成后查看定位、源码证据和有界静态影响。切换或刷新后可恢复保存的记录。

可给 Codex 这样的请求：

> 使用 CodeTrail MCP 调查项目“我的项目”中刚保存 Issue 的 Session。先发现并核对项目、Session 和固定 index，再创建 external Run。根据源码定位问题，提交包含证据和限制的结论；证据不足时明确说明，不执行仓库代码或测试。

网页保存消息不会自动驱动当前 Codex，需要在 Codex 显式提出请求。Run 默认及最大时间预算为 600 秒，同时限制工具次数与输出量。界面展示公开执行记录，不展示隐藏思维链。代码流程理解可通过 `start_external_run(analysis_kind="understanding")` 发起。

## 不使用 Codex 时

Session 的“Issue 分析”支持选择 fixture 场景、点击“启动离线分析”，体验候选、证据不足和工具失败。它是固定演示响应，不是真实 AI 调查；匹配源码样本见 [fixtures 说明](fixtures/README.md)。

也可选择 real 模式，由服务端模型调查。这需要在配置的 data_dir 中设置 `config.json` 并启用 `CODETRAIL_REAL_RUN_ENABLED=1`，格式见 [模型配置](apps/api/mcp.md)。密钥只存服务端，不提交 Git。Codex external 模式无需该供应商配置。

## 更新索引与保留历史

源码变化后，在索引管理中点击“检查更新”，再显式“更新索引”。GitHub 项目检查跟踪 ref 的远端 commit，确认变化后显式更新。

索引是不可变快照，不要求每次打开重建。更新产生或复用合适快照，失败保留旧版本；旧 Session、Run 和证据始终绑定旧索引。分析新代码时，在新索引上新建 Session。

配置的 `data_dir` 保存项目、索引、消息和报告，不是可随意删除的构建缓存。停止时只在 API/Web 终端按 `Ctrl+C`；再次启动后选择原项目和 Session 即可继续。

## 常见问题与范围

- 本地路径被拒绝：核对允许根目录、绝对路径和符号链接边界，修改共享 settings.json 后重启 API。
- Codex 看不到项目或网页看不到 Run：核对 API/MCP 使用同一 settings.json 和 data_dir，以及当前 project/Session；已有 CODETRAIL_DATA_DIR 环境变量会覆盖文件。
- 没有 external 工具：确认共享配置的 external_run_enabled 为 true，再重连 MCP；该项默认开启。显式 CODETRAIL_MCP_EXTERNAL_RUN_ENABLED 环境变量会覆盖文件。
- 图不完整：查看 `unresolved`、覆盖与截断提示；零邻居不证明无影响，动态调用不伪造确定边。
- 主闭环已通过已知真实 Issue 复测，不代表普适分析质量或完整复现 CodeGraph/Archify。真人画布 40 模块与分类证据 40,000 字符预算的触发验收尚未完成；TypeScript 导出分类不属于当前 MVP 必需范围。

## 开发与文档

```powershell
pnpm web:typecheck
pnpm web:build
pnpm --dir apps/web test
uv run --directory apps/api pytest -q
pnpm docs:check
```

- [开发者本地联合操作说明](docs/mvp-operation.md)：开发环境可使用 `scripts/start-local.ps1`、`scripts/setup-mcp.ps1` 包装同一 CLI。
- [API、索引、存储与 Run](apps/api/README.md) · [Web](apps/web/README.md) · [契约](contracts/README.md)
- [样本](fixtures/README.md) · [评测](evals/README.md)
- [开发约束](AGENTS.md) · [文档规范](docs/development.md) · [子系统导航](docs/subsystems/README.md)
- [共享决策](docs/decisions/README.md) · [UI 目标规范](docs/design/workspace-ui-spec.md)
