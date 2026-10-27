# 决策：公开 GitHub 导入固定 commit 并在服务管理目录解包

Status: implemented

## 问题

W2 只支持服务端允许根目录内的本地来源。工作台需要接收公开 GitHub 仓库地址，但不能把任意下载 URL、重定向目标、压缩包路径或临时目录当作长期来源身份，也不能让远程内容改变既有快照或绕过本地 MCP 的显式授权。

## 决定

GitHub 导入只接受 `https://github.com/{owner}/{repo}`，可选 branch/tag/commit。服务端先调用官方 GitHub API 将 ref 解析为固定 commit，再只请求该 commit 的官方 tar archive；API、`github.com` 和 `codeload.github.com` 之外的 HTTPS 主机、凭据、端口、子路径、查询参数和片段均拒绝。可选 `CODETRAIL_GITHUB_TOKEN`（未设置时回退 `GITHUB_TOKEN`）只在服务端使用，不进入 HTTP/MCP 或浏览器响应。限流（429，或带 `Retry-After`/`X-RateLimit-Remaining: 0` 的 403）在导入总时限内有界重试：遵守 `Retry-After` 或 reset 时间，否则指数退避；超过单次等待上限时直接返回说明重置时间与 token 配置的 `budget_exceeded`，普通 401/403 归为 `forbidden`。

远端身份使用规范化的 `github://owner/repo` 与 canonical URL 持久化，commit、请求 ref、快照 content hash 和清理状态写入管理 SQLite；每次 attempt 的临时目录位于服务管理目录的 `github-imports/{attempt_id}`，由 owner marker 保护，成功后删除。解包拒绝绝对路径、父目录、符号链接、硬链接和特殊文件，并限制 archive 大小、文件数量、解包总字节、重定向次数、单请求超时和操作总时间。来源目录不被写入，远端临时目录不作为项目长期来源。

相同仓库和相同 resolved commit 复用已有 attempt/index；分支移动到新 commit 时建立新快照但复用同一个 project。失败或重启中断不更新项目 latest index，固定 commit 可通过专用重试入口重新获取。清理失败仍保留已完成索引并持久化临时路径、错误和 `failed` 清理状态，可单独重试清理。HTTP GitHub 导入和状态/重试只接受 loopback；`CODETRAIL_MCP_GITHUB_IMPORT_ENABLED=1` 才向 MCP 注册 GitHub 获取工具，不能由本地 MCP 开关隐式开启网络下载。

GitHub archive 只进入既有 Python 快照、关系、证据和项目存储流程；不执行仓库脚本、hooks、依赖安装、submodule 或测试。手工对已清理的 GitHub 项目建立索引会被拒绝，必须再次导入以获得新的固定来源。

## 替代方案

直接接受 GitHub 下载 URL 会扩大 SSRF 和内容身份边界；只记录 branch/tag 会导致同一输入在分支移动后无法复现；将 archive 长期留在来源目录会破坏来源只读和服务管理隔离；允许 MCP 本地开关顺带下载远端内容会把本地写权限误当作网络权限。

## 验收与证据

离线测试覆盖 URL 规范化、默认分支与固定 commit、官方重定向、限流、超时、恶意路径/链接、解包预算、相同 commit 幂等、分支移动、清理失败重试和 HTTP 导入；完整 API、契约、MCP、前端类型/构建与浏览器验收记录在 progress.md。官方 API 语义以 [GitHub commits API](https://docs.github.com/en/rest/commits/commits)、[repository archive API](https://docs.github.com/en/rest/repos/contents?apiVersion=2022-11-28) 和 [REST API rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) 为依据。

## 后果

管理 SQLite 增加了远端来源和临时清理的迁移字段，API 服务需要访问 GitHub 的受限网络能力；快照、证据和项目查询仍复用原 W1/W2 语义。当前仍只索引 Python，私有仓库、GitHub App/OAuth、任意远程来源、异步队列、Run、Agent 和 Issue 分析不在本步范围。
