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
    if ((& $Python --version 2>&1) -notmatch "Python 3\.12\.") { throw "Python 3.12 is required." }
    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $VenvPython)) { & $Python -m venv (Join-Path $ProjectRoot ".venv") }
    & $VenvPython -m pip install "uv==0.12.2"
    $env:UV_CACHE_DIR = Join-Path $ProjectRoot ".cache\uv"
    & $VenvPython -m uv pip install --python $VenvPython -e "$ProjectRoot[test]"
    & $VenvPython -m uv pip check
    & $VenvPython -c "import fastapi,httpx,pydantic,yaml; import qwen3_tts_st; print('Lightweight facade imports OK')"
    if (-not $SkipRuntimeCheck) { & (Join-Path $PSScriptRoot "verify-qwentts-runtime.ps1") | Format-List }
    Write-Host "Installation completed. Log: $LogPath"
} finally {
    Stop-Transcript | Out-Null
}
