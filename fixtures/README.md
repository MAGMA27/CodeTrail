# CodeTrail 演示 fixtures

本目录保存开发阶段使用的合成样本，不计入 [独立评测集](../evals/README.md)，也不代表产品已经支持执行任意仓库代码。独立评测的冻结输入、评分标注和关键词基线位于 `evals/`，两者不得混用。

## 目录

- `repos/timeout-demo/`：被分析的固定 Python 仓库，索引根目录指向这里。只依赖 Python 标准库。
- `scenarios/issues/`：只包含用户可见的 Issue 输入，不包含预期答案。
- `scenarios/success-timeout-missing/`：缺少 `timeout` 的成功定位场景。
- `scenarios/insufficient-external-service/`：样本没有外部服务证据的证据不足场景。
- `scenarios/tool-failure-read-source/`：复用成功定位 Issue、定义 `read_source` 确定性失败的场景。
- `repos/timeout-demo` 之外的 `expected.json`：预期位置、关系、unresolved 和失败表现。它们不应进入后续模型上下文。
- `responses/`：三个 `FixtureResponseSet` 固定响应包，包含工具结果、结构化模型输出、报告和事件序列。
- `../contracts/schemas/codetrail.json`：版本为 `0.1.0` 的聚合 JSON Schema。

每个场景目录的 `scenario.json` 具有稳定场景 ID，并引用独立的 Issue、演示控制和预期结果文件。工具失败场景的 `read_source` 注入由本地 Run 执行器实际驱动，仍只允许 fixture 模式，不代表真实供应商或任意仓库执行已经开放。

## 已知 Bug 与复现

缺少 `TIMEOUT_DEMO_TIMEOUT` 时，`config.load_settings` 声明了 `DEFAULT_TIMEOUT` 却没有写入默认值；`client.initialize_client` 随后读取缺少的 `timeout`，预期抛出 `KeyError: 'timeout'`。显式设置 timeout 时，程序走完跨文件调用链并输出确定性 JSON。

在 PowerShell 中从 `fixtures/repos/timeout-demo/` 执行：

```powershell
Remove-Item Env:TIMEOUT_DEMO_TIMEOUT -ErrorAction SilentlyContinue
$env:TIMEOUT_DEMO_ENDPOINT = 'https://example.test'
python -m timeout_demo
# 预期退出码 1，包含 KeyError: 'timeout'

$env:TIMEOUT_DEMO_TIMEOUT = '5'
python -m timeout_demo
# 预期退出码 0：{"endpoint": "https://example.test", "handler": "primary", "timeout": 5}
```

样本不访问网络、不需要 API Key 或第三方服务。`handlers.handle` 与 `other_handlers.handle` 是不同模块中的同名函数；`dispatch.dispatch` 通过模块映射和 `getattr` 动态选择它们，预期静态分析保留 unresolved，不伪造确定目标。

## 固定版本

`scenarios/source-manifest.json` 记录 `repos/timeout-demo/` 每个源码文件的 SHA-256。修改样本源码后，必须同步预期位置、关系清单和 manifest；场景答案仍然属于开发数据，不是修复后的代码。

## 契约与固定响应校验

契约模型位于 `apps/api/app/contracts.py`，拒绝未声明字段，并校验跨快照引用、证据来源、事件顺序、终态和工具失败边界。Schema 由固定命令生成，响应包由生成器构造后再次经过 Pydantic 校验：

```powershell
Set-Location apps/api
uv run python -m app.export_schema
uv run pytest -q
Set-Location ../..
uv run --project apps/api python fixtures/generate_responses.py
```

当前校验不启动 API、不调用真实模型，也不执行场景 JSON 中的任意 `command`。Run 的工具失败响应会保留失败前已返回的同快照 evidence，并把注入的失败结果写入事件/工具结果；证据不足响应正常结束但 outcome 为 `insufficient_evidence`。完整 Run 生命周期由 API fixture executor 另行验证。
