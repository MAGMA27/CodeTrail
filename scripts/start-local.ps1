[CmdletBinding()]
param(
    [ValidateSet("api", "web")]
    [string]$Service = "api"
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeRoot = Join-Path $repoRoot ".codetrail-runtime"

Set-Location -LiteralPath $repoRoot

if ($Service -eq "api") {
    if (-not (Test-Path -LiteralPath $runtimeRoot -PathType Container)) {
        throw "共享服务数据目录不存在：$runtimeRoot。请先按 docs/mvp-operation.md 注册或导入来源；脚本不会创建、清空或迁移数据。"
    }

    # The API and the Codex MCP entry both use this managed data directory.
    $env:CODETRAIL_DATA_DIR = $runtimeRoot
    $env:CODETRAIL_REPOSITORIES = "{}"
    $env:CODETRAIL_WEB_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
    $env:CODETRAIL_WORKBENCH_URL = "http://localhost:3000"
    $env:CODETRAIL_MCP_EXTERNAL_RUN_ENABLED = "1"

    & uv run --directory (Join-Path $repoRoot "apps/api") uvicorn app.main:app --host 127.0.0.1 --port 8000
    exit $LASTEXITCODE
}

& pnpm --dir (Join-Path $repoRoot "apps/web") dev
exit $LASTEXITCODE
