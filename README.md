# CodeTrail

代码理解工作区：在项目中浏览入口、功能与静态关系，通过 Session/Run 保存 Issue 调查和同快照证据。当前能力与限制以源码附近的文档为准，静态可达不等于已确认业务影响。

## 使用入口

- [本地启动与 MCP 联合使用](docs/mvp-operation.md)
- [API 服务、索引、存储与 Run](apps/api/README.md)
- [前端工作台](apps/web/README.md)
- [数据契约](contracts/README.md)
- [开发样本](fixtures/README.md)
- [评测与结果](evals/README.md)

## 开发与文档

- [开发约束](AGENTS.md) 与 [文档归属规范](docs/development.md)
- [跨目录子系统导航](docs/subsystems/README.md)
- [共享决策](docs/decisions/README.md) 与 [UI 目标规范](docs/design/workspace-ui-spec.md)

memory-bank 是被 Git 忽略的本地计划、进度和历史资料，仍在使用；当前实现说明在上述就近文档，不依赖本地资料即可阅读共享工程文档。
