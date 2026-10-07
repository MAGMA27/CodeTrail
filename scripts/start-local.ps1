[CmdletBinding()]
param([ValidateSet("api", "web")][string]$Service = "api")
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot
if ($Service -eq "api") {
    & uv run --directory (Join-Path $repoRoot "apps/api") codetrail init --data-dir (Join-Path $repoRoot ".codetrail-runtime") --allow-root $repoRoot --typescript-compiler (Join-Path $repoRoot "apps/web/node_modules/typescript/lib/typescript.js")
    if ($LASTEXITCODE -ne 0) { throw "CodeTrail 配置初始化失败。" }
    & uv run --directory (Join-Path $repoRoot "apps/api") codetrail api
} else {
    & pnpm --dir (Join-Path $repoRoot "apps/web") dev
}
exit $LASTEXITCODE
