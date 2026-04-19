param(
    [Parameter(Mandatory = $true)]
    [string]$Token,
    [string]$BridgeHost = "0.0.0.0",
    [int]$BridgePort = 8787
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $projectRoot ".model_bridge.pid"

if (Test-Path $pidFile) {
    $existingPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($existingPid) {
        $proc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "[bridge] already running with PID $existingPid"
            exit 0
        }
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

$pythonExe = if (Test-Path (Join-Path $projectRoot ".venv\Scripts\python.exe")) {
    (Join-Path $projectRoot ".venv\Scripts\python.exe")
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python not found. Install dependencies first or create .venv."
    }
    $pythonCommand.Source
}

$pythonPath = if ($env:PYTHONPATH) { "$projectRoot;$env:PYTHONPATH" } else { $projectRoot }

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $pythonExe
$psi.Arguments = "scripts/model_bridge_server.py"
$psi.WorkingDirectory = $projectRoot
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.Environment["REMOTE_MODEL_API_TOKEN"] = $Token
$psi.Environment["REMOTE_MODEL_API_HOST"] = $BridgeHost
$psi.Environment["REMOTE_MODEL_API_PORT"] = "$BridgePort"
$psi.Environment["PYTHONPATH"] = $pythonPath

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
$null = $proc.Start()

Set-Content -Path $pidFile -Value $proc.Id
Write-Host "[bridge] started, PID $($proc.Id), host=$BridgeHost, port=$BridgePort"
