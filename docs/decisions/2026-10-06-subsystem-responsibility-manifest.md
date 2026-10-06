# 决策：固定快照中的职责分组

Status: implemented

## 问题

项目结构概览目前按源码目录分组。目录只能说明路径位置，不能稳定表达 API、索引、存储、Run、MCP 等子系统职责；模型生成的功能描述也不能作为确定的 ownership。架构图需要保留静态依赖方向和同快照源码入口，同时让职责分组能用于不同项目。

## 决定

支持可选的根文件 `codetrail-subsystems.json`，格式固定为 `{ "schema_version": 1, "subsystems": [{ "id", "label", "owners" }] }`。每项用稳定 ID、显示名称和 root-relative owner 路径声明一个职责。Owner 只接受精确文件路径或以 `/` 结尾的目录前缀；路径区分大小写，必须是规范 POSIX 相对路径。严格拒绝重复 JSON key/ID/owner、额外字段、绝对路径、别名路径、越界段和任何词法重叠。

解析有界：文件最多 64 KiB、最多 80 项、最多 400 个 owner 路径、每个路径最多 500 字符；不增加依赖，不解析 AST。源码捕获器将可读的原始 manifest 作为普通 snapshot file 同源码一起保存，并通过现有 snapshot hash/index identity 固定内容。语义无效时仍保留原文和有界 `manifest_invalid` snapshot issue；超限、乱码、symlink 或不可读则保留 issue，但不假装文件缺失。所有 issue 均非阻断，不令源码索引失败。

概览只从请求的不可变 index 读取 manifest 和 provenance evidence，不访问 live worktree。没有 manifest 或旧 index 未捕获 manifest 时使用现有 source-directory 分组。语义无效时整份忽略并回退目录分组，同时返回 invalid 状态、issue 和可读取的 manifest evidence。有效配置按模块路径确定性归属；没有命中模块的声明项保留为空职责组并使状态为 partial，未被任何 owner 覆盖的模块继续出现在 source-directory fallback 组中。职责 metadata 与文档导航 owner 分属不同用途，不合并、不改变 docs checker。

职责组和 source-directory 组均可作为架构容器；模块间箭头只由现有同快照 `module_relations` 投影，方向保持 source → target。HTTP method/path association 继续独立展示，不混入 imports/calls。每个概览只返回一个 manifest provenance Evidence，不按职责组复制全文。职责展开目前到模块；此决策不表示已经支持 module → symbol 列表。

沿用 overview `max_groups`（上限 200，工作台默认 80）和现有可见/总量计数。组数上限是预算，不保证所有组均返回；概览必须显式呈现截断、partial、无效配置和未归属模块。当前架构画布仅把职责和目录组绘成容器，HTTP 形状关联继续独立展示。HTTP 与 MCP 共用同一应用服务结果和预算。

源码捕获规则及 index identity 升级，配置 hash 随规则版本变化；旧不可变 index 继续读取并使用目录 fallback。关系语义与 facts 版本不变，不新增运行服务状态或存储表。HTTP/MCP schema 增加可选默认字段，便于旧 index 与既有 immutable rows 兼容。

## 替代方案

- 继续只按源码目录分组：不增加配置或维护成本，但无法表达职责边界。
- 从 feature-generation/model 输出推断职责：语义结果可能变化且带假设，不能当作确定 ownership。
- 将 docs owner 表作为 runtime ownership：现有路径表服务于文档导航与 checker，语义不同且仓库专用。
- 把依赖另存为职责边：会复制静态关系并引入一致性问题；直接由同快照模块关系投影即可。
- 将职责或模块符号写入可编辑服务配置：状态会脱离源码快照，无法保证 freshness 与来源证据一致。

## 验收与证据

- fixture 验证无配置、有效配置、无效 JSON/重复 key/越界路径/重叠 owner/预算超限、unmatched owner 及未归属模块；错误均有界且不阻断代码索引。
- 修改、添加、删除、变成 symlink、超限或乱码的 manifest 均不能被当成未配置；freshness 对捕获不完整输入报告 unknown，对可读内容变化报告 changed。旧 index 不读取 live manifest。
- 同一固定 index 的 HTTP 与 MCP 概览包含相同职责、状态、覆盖和 provenance；source evidence 的 hash/index 与职责配置一致。
- PC 工作台显示职责容器、未覆盖目录 fallback、同快照 imports/calls 依赖方向，能够展开职责到模块并打开代表源码；HTTP shape association 保持独立。
- 契约生成/检查、相关后端测试、Web 测试/typecheck/build、文档检查通过；真实 CodeTrail 项目可见 API、Index、Storage、Run、MCP、Web 职责。

本阶段已验收：`uv run --directory apps/api pytest -q` 224 passed，后端 compileall 通过；Web tests 34 passed、typecheck/build 通过；contracts、docs 与 local docs 检查通过。真实 CodeTrail 项目由 HTTP 从既有 v8 immutable index 刷新得到 `index-import-c929f2f2b7a3e5b435ddaee3859158fe-relations-v9-rebuild-08759c7862754f11`（snapshot `08759c7862754f11427ca4913cd53cd55cb8e5e085029ae589c58d1c15876c48`）。概览覆盖 94/94 个模块、28/28 个组；其中 6 个职责组归属 26 个模块，68 个未归属模块留在 16 个目录 fallback 组，状态为 partial。manifest provenance hash 为 `35b43c2a37b6eebc56ea1d18a99383e47e2a0eff76e985833a2829da307110cc`。冷启动 stdio MCP 的 overview 与同项目、同 index 的 HTTP 结果逐字段相等。

PC 实际展开 API、Storage、Run、MCP 四个职责容器并查看到各自模块与静态依赖；从 Storage 的 service → storage 关系打开代表源码，显示同一 v9 index 的 `apps/api/app/service.py:L143–L156`。画布有 22 个职责/目录容器，即 6 个职责加 16 个 fallback 目录；overview 的 28 个组还包括 6 个单独展示的 HTTP 形状组。未实施模块到符号列表，符号钻取仍属于后续阶段。

## 后果

仓库作者需要维护一份小型、版本化 JSON 文件；owner 路径重叠会整份被拒绝。不同语言项目可以用同一格式，但只有当前索引器产生的模块会被归属；未索引语言不会被推断成模块。结构视图仍受入口、模块、关系和组预算影响，职责组并未补齐符号级浏览。
