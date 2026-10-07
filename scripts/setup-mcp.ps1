[CmdletBinding()]
param([switch]$Replace)
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
& uv tool install --force (Join-Path $repoRoot "apps/api")
if ($LASTEXITCODE -ne 0) { throw "CodeTrail 安装失败。" }
$compilerPath = Join-Path $repoRoot "apps/web/node_modules/typescript/lib/typescript.js"
& codetrail init --data-dir (Join-Path $repoRoot ".codetrail-runtime") --allow-root $repoRoot --typescript-compiler $compilerPath
if ($LASTEXITCODE -ne 0) { throw "CodeTrail 配置初始化失败。" }
if ($Replace) { & codetrail install --replace } else { & codetrail install }
if ($LASTEXITCODE -ne 0) { throw "MCP 注册失败。" }
