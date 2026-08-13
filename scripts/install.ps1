[CmdletBinding()]
param([string]$Python = "", [switch]$SkipRuntimeCheck)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogPath = Join-Path $LogDir ("install-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))
Start-Transcript -Path $LogPath | Out-Null
try {
    if (-not $Python) {
        $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($PyLauncher) {
            $Line = (& py -0p 2>$null) | Where-Object { $_ -match "3\.12" } | Select-Object -First 1
            if ($Line -and $Line -match "([A-Za-z]:\\.*python\.exe)$") { $Python = $Matches[1] }
        }
    }
    if (-not $Python) {
        $Command = Get-Command python -ErrorAction SilentlyContinue
        if ($Command -and $Command.Source -notmatch "WindowsApps") { $Python = $Command.Source }
    }
    if (-not $Python -or -not (Test-Path -LiteralPath $Python)) { throw "Python 3.12 was not found." }
    $VersionOutput = & $Python --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Unable to run the selected Python interpreter (exit $LASTEXITCODE)." }
    if ($VersionOutput -notmatch "Python 3\.12\.") { throw "Python 3.12 is required." }
    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        & $Python -m venv (Join-Path $ProjectRoot ".venv")
        if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed (exit $LASTEXITCODE)." }
    }
    & $VenvPython -m pip install "uv==0.12.2"
    if ($LASTEXITCODE -ne 0) { throw "uv installation failed (exit $LASTEXITCODE)." }
    $env:UV_CACHE_DIR = Join-Path $ProjectRoot ".cache\uv"
    & $VenvPython -m uv pip install --python $VenvPython -e "$ProjectRoot[test]"
    if ($LASTEXITCODE -ne 0) { throw "Project dependency installation failed (exit $LASTEXITCODE)." }
    & $VenvPython -m uv pip check
    if ($LASTEXITCODE -ne 0) { throw "Installed dependency validation failed (exit $LASTEXITCODE)." }
    & $VenvPython -c "import fastapi,httpx,pydantic,yaml; import qwen3_tts_st; print('Lightweight facade imports OK')"
    if ($LASTEXITCODE -ne 0) { throw "Lightweight facade import check failed (exit $LASTEXITCODE)." }
    & (Join-Path $PSScriptRoot "ensure-qwentts-models.ps1") -Config (Join-Path $ProjectRoot "config\config.local.yaml") -AllModels
    if (-not $SkipRuntimeCheck) { & (Join-Path $PSScriptRoot "verify-qwentts-runtime.ps1") -AllModels | Format-List }
    Write-Host "Installation completed. Log: $LogPath"
} finally {
    Stop-Transcript | Out-Null
}
