# API 服务

## 职责与边界

`apps/api` 提供本地 FastAPI 服务、服务端仓库 allow-list、项目身份、索引构建及项目/索引查询。它不执行被分析仓库代码，不实现 GitHub 获取、项目功能概览或 Issue Agent。

## 入口与目录导航

- [main.py](app/main.py)：FastAPI 应用和 HTTP 路由。
- [service.py](app/service.py)：仓库注册、项目生命周期、索引服务和项目归属校验。
- [storage.py](app/storage.py)：每个配置仓库下 `.codetrail/index.sqlite3` 的事务存储与查询。
- [indexing.py](app/indexing.py) 与 [relations.py](app/relations.py)：Python 快照、符号和保守静态关系提取。
- [tests](tests/)：API、契约、索引、关系和存储测试。

## 接口与依赖

契约位于 [contracts.py](app/contracts.py)，Schema 由 [export_schema.py](app/export_schema.py) 生成。旧 `/api/indexes/*` 保留兼容；项目工作区使用 `/api/projects/*`，项目索引查询必须通过项目归属校验。项目来源当前仅复用服务端配置的 `repository_id`。

## 数据与生命周期

仓库 allow-list 由 `CODETRAIL_REPOSITORIES` JSON 对象提供。Project 持有稳定 ID、名称、来源 repository_id、创建时间和索引关联；索引源码及证据仍绑定不可变 `index_id`。可用索引和当前构建标记分开保存，失败清理构建标记且不替换原有可用索引；服务重启会清理同步构建遗留标记。

## 使用与配置

从仓库根目录准备服务端仓库映射后启动：

```powershell
$env:CODETRAIL_REPOSITORIES = '{"timeout-demo":"D:\\Dev\\CodeTrail\\fixtures\\repos\\timeout-demo"}'
Set-Location apps/api
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

API 健康检查为 `GET /api/health`。前端和测试通过项目 API 创建项目、建立索引，再用 project_id/index_id 查询。

## 错误与限制

仓库路径只由服务端配置，客户端不能提交宿主路径。未知项目、非所属索引和未配置 repository_id 返回稳定 `not_found`；输入边界返回 `invalid_input`。当前索引构建在受限 worker 中同步完成，仍只解析 Python，Run、SSE、Agent 和 GitHub 获取未实现。

## 验证

从 `apps/api` 执行 `uv run pytest -q` 和 `uv run python -m compileall -q app`。项目隔离、旧索引兼容关联、失败不覆盖最新索引及服务重启恢复由 `tests/test_api.py` 覆盖。

## 关联文档

- [API 子系统说明](../../docs/subsystems/api.md)
- [存储与查询子系统说明](../../docs/subsystems/storage.md)
- [数据契约](../../docs/subsystems/contracts.md)
- [工作区与项目概览决策](../../docs/decisions/2026-09-11-project-workspace.md)
