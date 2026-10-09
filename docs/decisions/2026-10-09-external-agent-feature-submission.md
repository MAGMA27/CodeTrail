# 决策：由外部 Agent 识别并提交项目功能

Status: proposed

## 问题

MCP 的功能识别此前调用 CodeTrail 服务端模型；由 Codex 宿主调用 MCP 时，宿主已经拥有可用模型，继续调用服务端模型会重复承担模型费用，也要求额外配置服务端密钥。需要让宿主负责识别，同时保留固定快照 evidence、FeatureGeneration 持久化和工作台读取的一致边界。

## 决定

在现有 Codex external `understanding` Run 中增加 `submit_project_features`。宿主先通过已有概览、搜索、neighbors 和源码读取工具获得上下文，再提交 `FeatureModelOutput`。提交只随 `CODETRAIL_MCP_EXTERNAL_RUN_ENABLED=1` 注册，不增加新 secret 或模型开关；必须在 Run 结论终结前提交。

WorkspaceService 复用 `_validate_model_output` 与现有 FeatureGeneration 存储。提交的 evidence ID 必须来自该 Run 持久化的 `run_read_source` 结果；服务端从结果恢复 Evidence，并针对固定 index 重读源码以验证 ID、路径、行范围和内容 hash。entry、module、symbol 也须由相同 index 概览、实际源码读取路径或 Run 搜索/邻接结果支持。项目、Session、Run、index、analysis kind、活动状态、时间和源码行/工具预算均在服务端校验。提交不创建静态 `CodeEdge`。

外部 generation 记录 `source=external`、`source_scope=external_run`、`mode=real` 和可选宿主自报模型名；model 名只作显示标签。服务端模型与修复调用为 0，逐次模型字符用量为空、token 为 null，不能推断宿主 token 或费用。`FeatureGeneration`、历史摘要、版本及图读取仍是唯一保存与读取路径；HTTP 内置 real 生成功能保留原服务端模型路径。MCP 不再注册服务端 `generate_project_features`，`CODETRAIL_MCP_FEATURE_GENERATION_ENABLED` 不再有任何功能。

幂等键按 Run 隔离：同 key/同内容复用已保存 generation，同 key/不同内容冲突；保存事务再次验证同一 active external understanding Run 未超时或终结。

## 替代方案

继续保留一个 MCP 服务端生成工具会产生两条拥有不同模型付费方的生成路径和单独开关。只接受模型输出而接受客户端传入的 evidence 会让未实际读取的来源伪装成已核验来源。为识别功能新建 Agent 循环、队列或模型代理则重复 external Run 的生命周期，扩大了写入和模型调用面。

## 验收与证据

当前实现证据、定向测试、契约与 Schema 检查、工作台核对及独立审查结果由本阶段 memory-bank 进度记录维护；在主代理审查前保持 proposed。

## 后果

FeatureGeneration 新增来源和 `external_run` scope，旧历史通过默认 `source=server` 保持可读。模型用量字段只描述服务端适配器；宿主实际模型调用和费用由宿主承担。MCP 客户端需重新加载工具定义，默认 HTTP 内置生成功能和历史/图读取不变。
