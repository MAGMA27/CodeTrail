# CodeTrail MVP 操作说明

本说明用于在本地启动 CodeTrail 工作台，并让当前 Codex 主机通过 stdio MCP 驱动一次可恢复的 external Run。

## 启动 API 与工作台

从仓库根目录启动 API；API 和 MCP 必须使用同一个服务数据目录，已有 managed source 才能被项目列表发现。

```powershell
$env:CODETRAIL_DATA_DIR = 'D:/Dev/CodeTrail-runtime'
$env:CODETRAIL_REPOSITORIES = '{}'
$env:CODETRAIL_WORKBENCH_URL = 'http://localhost:3000'
$env:CODETRAIL_MCP_EXTERNAL_RUN_ENABLED = '1'
uv run --directory apps/api uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开 PowerShell 启动工作台：

```powershell
pnpm --dir apps/web dev
```

## 从 HTTP 入口理解项目

选择一个 ready index 后，工作台会在项目概览的“入口路径”中列出可静态确认的 FastAPI 路由。`HTTP` 行显示方法、声明路径和处理函数；点击入口打开装饰器到函数结束的同快照源码证据，点击“关系图”则以处理函数为根进入既有静态关系图。MCP 的 `get_overview` 返回同一 `project_id/index_id` 下的入口和 evidence 引用，可用 `/api/health` 或项目概览路由核对行号与源码 hash。

这里的路径是装饰器声明值，不是解析过 router 或部署前缀后的最终 URL。当前规则只扫描模块/函数体的直接语句，不展开控制流块或类体；也不识别 `APIRouter`、`include_router`、`mount`、动态路径和运行时注册。未识别路由不代表项目没有 HTTP 入口。

## 加载 Codex MCP

把 `apps/api/README.md` 中的 Windows stdio 配置写入当前 Codex 主机的配置文件，确保 `CODETRAIL_DATA_DIR` 与 API 完全一致；启用 `CODETRAIL_MCP_EXTERNAL_RUN_ENABLED=1` 后重启或重载 Codex 主机。external Run 不需要真实模型供应商密钥。

## 进行调查

打开 `http://localhost:3000`，选择目标项目和一个已有消息的 Session；当前自用验收使用项目 `CodeTrail 自用验收`、Session `已知费用结算问题自用验收` 和其固定 ready index。首次使用时先导入来源、等待索引 ready、创建 Session 并保存一条问题消息。

在 Codex 中只描述现象，不预先提供候选文件、函数或行号，例如：“批量评测时供应商返回明确 token 用量，但费用高于请求前预留时最终累计费用可能仍停留在预留值，请检查是否存在导致低估的逻辑，给出源码证据、触发条件与可能影响；证据不足时明确说明。”

Codex 应从 MCP 项目发现开始，使用同一 Run 的 `start_external_run`、`run_search_code`、`run_read_source`、`run_get_neighbors` 和 `submit_external_run`；工作台不创建 external Run，只从当前 Session history 自动发现并展示。

## 查看结果

在工作台的 Run history 打开完成的 `CODEX / 外部 Agent` Run，确认公开事件、工具结果、候选结论、源码 evidence 和限制均属于同一个 project/session/index；从证据按钮打开源码，从影响范围按钮查看固定快照上的有界静态 calls。刷新或切换 Session 后回到原 Session，页面应恢复同一 Run，不创建重复 Run。

## 边界

external Run 只执行白名单代码搜索、源码读取和静态邻接查询，不执行来源仓库代码、shell 或测试；报告不展示隐藏思维链。真实供应商调用、DeepSeek 语义质量和实际账单仍需单独授权与评测，不因本地 external 闭环通过而宣称已验证。
