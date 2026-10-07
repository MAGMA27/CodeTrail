# 决策：安装入口与共享运行配置分离

Status: implemented

## 问题

将源码目录和业务环境变量写入 Codex MCP 注册，会令配置变更与源码位置耦合。自动填写绝对路径只能减少输入，不能提供稳定运行入口。

## 决定

Python 包提供 codetrail console entry point，使用 uv tool install 安装。Codex 默认只注册 codetrail mcp。API/MCP 共用用户目录下 .config/codetrail/settings.json；显式 --config 或 CODETRAIL_CONFIG 选择其他文件，业务环境变量优先于文件。配置修改需要重启服务，不实现热加载。

init 只初始化缺失文件，保留已有配置；默认数据目录为用户目录下 .local/share/codetrail，默认导入根为空。源码便利脚本初始化到既有 .codetrail-runtime 并允许当前仓库，仍保留已有配置。旧 startup.json 不再读取且不删除，其他来源白名单需转入共享设置。既有索引与报告不迁移、不清理。

install 默认保留既有 codetrail 注册，--replace 显式更新；--print-config 只预览，status 检查注册，uninstall 只移除指定注册。沿用 Codex CLI，不另写 TOML 编辑器。TypeScript parser 随包安装，编译器通过 settings 的 typescript_compiler 指定，仍校验锁定版本。Web 与离线演示样本仍需源码工作区，本轮不打包前端或演示仓库。

## 替代方案

继续向 MCP 注入源码地址和业务环境变量会维护两份配置；仅加入更多脚本参数仍无法形成稳定入口。

## 后果

安装后的包独立于源码目录，但源码更新需重新安装；前端与 TypeScript 编译器不属于自包含发行包。只实现当前 Codex 适配，不增加多宿主安装框架。

## 验收与证据

CLI/TypeScript 定向测试 23 passed；实际 uv tool 安装后 API 健康接口返回 200，已停止测试 API。冷 stdio 客户端完成初始化、列出 28 项工具并成功读取项目。本机 codetrail 注册已变为稳定命令且无业务环境变量；当前宿主原进程未强制重载，需用户重连加载。配置与入口见 [API MCP 文档](../../apps/api/mcp.md)，测试见 [CLI 测试](../../apps/api/tests/test_cli.py)。
