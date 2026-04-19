param(
    [switch]$All,
    [string]$UserId,
    [ValidateSet("all", "markovify", "persona", "gpt")]
    [string]$Modes = "all"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$pythonExe = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$projectRoot;$env:PYTHONPATH" } else { $projectRoot }

if ($All) {
    & $pythonExe "scripts/train_local.py" --all --modes $Modes
    exit $LASTEXITCODE
}

if ($UserId) {
    & $pythonExe "scripts/train_local.py" --user-id $UserId --modes $Modes
    exit $LASTEXITCODE
}

throw "Use -All or -UserId <discord_user_id>"
