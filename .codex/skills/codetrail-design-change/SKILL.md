---
name: codetrail-design-change
description: 为 CodeTrail 的新增能力或接口、依赖、状态、持久化及行为变更设计可验收方案。纯机械编辑和只读问答不触发。
---

# 设计变更

先读根目录 AGENTS.md、docs/development.md、docs/subsystems/README.md，以及受影响子系统文档。memory-bank 存在时按其索引读取相关需求与当前进度；缺失时从代码和用户需求恢复上下文，不创建虚假进度。

根据需求和实际源码确定影响范围。公共契约变化继续追踪消费者，内部实现变化只扩展到确实受影响的接口。保留已有用户修改。

沿受影响源码目录向上查找 README.md 和所有适用的 AGENTS.md，读取最近的职责说明及父级约束。按 docs/development.md 的“就近文档”规则确定本次事实归属；在设计中列出要更新或创建的局部文档。具有独立职责的目录缺少 README 时，将按 docs/templates/local-readme.md 创建文档列为实施交付物。多个平铺模块共享目录时，使用父目录 README 导航与模块 docstring，不为每个文件建立目录。只读设计任务仅列出缺口，已授权开发任务继续创建。

在实现前明确职责、输入输出、数据所有者、失败行为、生命周期和验收证据。对 CodeTrail 检查 index_id 绑定、静态事实与模型假设分离、证据来源、只读路径约束和预算；仅对本次相关条目展开。遵守当前单 Agent 和纵向 MVP 范围，按独立变化需要引入抽象。

重要取舍使用 docs/templates/decision.md 写入 docs/decisions/YYYY-MM-DD-topic.md，初始 proposed。一般局部变更可在任务说明中给出设计，不为每个编辑新增记录。已有决定改变时更新或替代对应记录，保留原因。

交付可执行的变更范围、接口约定和验收条件。未验证的能力标明未验证；用户只要求设计时到此结束。已授权实现则继续实施，之后按 AGENTS.md 调用同步与审查。
