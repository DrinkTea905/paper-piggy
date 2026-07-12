# Sync current source into an existing bundle's app/ (does NOT touch python/ or models/).
# Run this after editing code to refresh the test package folder in seconds.
#   Usage: powershell -ExecutionPolicy Bypass -File sync_app.ps1 -Bundle "D:\OneDrive\AI\知识库应用\LocalKB"
# -Bundle points at the bundle root (the folder that contains app\ python\ models\ 启动.bat).
# ASCII-only source on purpose: Windows PowerShell 5.1 mis-parses UTF-8 .ps1 without a BOM.
param([Parameter(Mandatory = $true)][string]$Bundle)

$ErrorActionPreference = 'Stop'
$src = 'D:\OneDrive\AI\知识库应用\LocalKB源码'
$app = Join-Path $Bundle 'app'
if (-not (Test-Path $app)) { Write-Error "app not found: $app  (build/extract the bundle first)"; exit 1 }

# Keep in sync with build_bundle.py DEV_ONLY / KEEP_MD
$devOnly = @('build_bundle.py','pack_models.py','setup_reranker_onnx.py','setup_onnx.py','import_fulltext.py','fix_schema.py','gen_mcp_doc.py')
$keepMd  = @('README.md','MCP接入说明.md')

# 1) copy wanted top-level .py (exclude DEV_ONLY)
$wantPy = Get-ChildItem "$src\*.py" -File | Where-Object { $devOnly -notcontains $_.Name }
foreach ($f in $wantPy) { Copy-Item $f.FullName (Join-Path $app $f.Name) -Force }

# 2) delete top-level .py in app/ that were removed from source (e.g. bibutil.py)
$wantNames = $wantPy.Name
Get-ChildItem "$app\*.py" -File | Where-Object { $wantNames -notcontains $_.Name } |
  ForEach-Object { Remove-Item $_.FullName -Force; Write-Host "  - removed app/$($_.Name)" }

# 3) kept .md files
foreach ($m in $keepMd) { if (Test-Path "$src\$m") { Copy-Item "$src\$m" (Join-Path $app $m) -Force } }

# 4) journal-tier seed data
if (Test-Path "$src\journal_tiers.json") { Copy-Item "$src\journal_tiers.json" (Join-Path $app 'journal_tiers.json') -Force }

# 5) mirror web/ and docs/ and journal_grading/ (exclude __pycache__)
#    journal_grading = 期刊引用权重分级引擎（含 config/ 与 catalogs/ 目录数据；必须整包带上，否则 retriever 里 import journal_grading 失败、权重回退旧离散档）
foreach ($sub in @('web','docs','journal_grading')) {
  if (Test-Path "$src\$sub") {
    robocopy "$src\$sub" (Join-Path $app $sub) /MIR /XD __pycache__ /NFL /NDL /NJH /NJS | Out-Null
  }
}

# 6) drop stale __pycache__ in app/
Get-ChildItem $app -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "OK: app/ synced -> $app"
