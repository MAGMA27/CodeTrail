# 决策：固定快照内的 TypeScript path alias 解析

Status: implemented

## 问题

TypeScript AST 已记录 named import 和调用事实，但静态关系解析只支持相对模块 specifier。包含仓库内部 `tsconfig.json` path alias 的导入会被标为外部或别名，因而不能从调用者继续到定义和源码。

## 决定

将大小、数量和扫描时间受限的标准文件名 `tsconfig.json` 作为非源码元数据捕获进不可变快照，并接受 JSONC 注释与尾逗号。按导入文件最近的配置解析 `compilerOptions.paths` exact key 与单 `*` key；目标只在同一快照中解析为唯一仓库相对路径后才生成既有 imports/calls 边。配置内容参与快照 hash 与 freshness。`extends` 不展开；含 `extends` 的最近配置整体不参与 alias 解析。无效配置、缺失目标及候选歧义保留 unresolved，不猜测目标。mapping target 数组若有多个现存候选则整体 unresolved，不采用 TypeScript 编译器的顺序 fallback。

## 替代方案

- 直接从仓库读取 config：会让保存快照关系读取依赖实时文件系统，破坏 index_id 与证据的一致性。
- 实现完整 TypeScript config 与模块解析：涉及 extends、项目 references、package exports/lookup 及 bundler 行为，超出确定性关系规则的合理边界。
- 只解析 tsconfig 中的相对路径而不捕获配置：无法保证历史 index 可复现，也无法让 config-only 修改参与 freshness。

## 验收与证据

实施后以合成仓库和 CodeTrail 的 HTTP/MCP 查询验证 alias 导入、前向/反向符号 calls、同快照源码、config-only freshness/identity、嵌套配置以及 invalid/missing/ambiguous 目标。实际命令和结果记录于 `/tmp/ct-navigation/alias-final.md`。

## 后果

该规则覆盖 JSONC 注释/尾逗号、exact 与单通配符映射，不模拟任意 TypeScript compiler、runtime bundler 或包管理器语义。仅捕获 `tsconfig.json`；自定义配置文件名、`extends`、project references、多通配符、package exports/lookup 和运行时 alias 保留 unresolved。mapping target 数组按保守策略聚合所有已捕获候选；多个候选即歧义，即使 TypeScript 编译器会按数组顺序选中第一个。
