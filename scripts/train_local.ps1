param(
    [switch]$All,
    [string]$UserId
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$pythonExe = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$projectRoot;$env:PYTHONPATH" } else { $projectRoot }

if ($All) {
    & $pythonExe "scripts/train_local.py" --all
    exit $LASTEXITCODE
}

if ($UserId) {
    & $pythonExe "scripts/train_local.py" --user-id $UserId
    exit $LASTEXITCODE
}

throw "Use -All or -UserId <discord_user_id>"
