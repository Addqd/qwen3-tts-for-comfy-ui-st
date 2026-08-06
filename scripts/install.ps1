[CmdletBinding()]
param(
    [string]$Python = "",
    [ValidateSet("CPU", "CUDA126")][string]$TorchVariant = "CPU",
    [switch]$SkipModelCheck
)

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
            $Candidates = & py -0p 2>$null
            $Line = $Candidates | Where-Object { $_ -match "3\.12" } | Select-Object -First 1
            if ($Line -and $Line -match "([A-Za-z]:\\.*python\.exe)$") { $Python = $Matches[1] }
        }
    }
    if (-not $Python) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($PythonCommand -and $PythonCommand.Source -notmatch "WindowsApps") { $Python = $PythonCommand.Source }
    }
    if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
        throw "Python 3.12 was not found. Retry: .\scripts\install.ps1 -Python 'C:\path\to\python.exe'"
    }
    $Version = & $Python --version 2>&1
    if ($Version -notmatch "Python 3\.12\.") { throw "Python 3.12 is required; found: $Version" }
    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $VenvPython)) { & $Python -m venv (Join-Path $ProjectRoot ".venv") }
    & $VenvPython -m pip install "uv==0.12.2"
    $env:UV_CACHE_DIR = Join-Path $ProjectRoot ".cache\uv"
    & $VenvPython -m uv pip install --python $VenvPython --no-build-isolation -e "$ProjectRoot[test]"
    if ($TorchVariant -eq "CUDA126") {
        & $VenvPython -m uv pip install --python $VenvPython --reinstall "torch==2.11.0+cu126" "torchaudio==2.11.0+cu126" --index-url "https://download.pytorch.org/whl/cu126"
    }
    & $VenvPython -m uv pip check
    & $VenvPython -c "import fastapi, torch, qwen_tts, soundfile; print('Imports OK'); print('torch', torch.__version__, 'CUDA', torch.cuda.is_available())"
    if (-not $SkipModelCheck) {
        & $VenvPython -c "from qwen3_tts_st.config import load_config; c=load_config(); assert c.get('model.id') == 'Qwen/Qwen3-TTS-12Hz-0.6B-Base'; print('Model config OK:', c.get('model.id'))"
    }
    & $VenvPython -m uv pip freeze | Set-Content -LiteralPath (Join-Path $ProjectRoot "requirements.lock.txt") -Encoding UTF8
    Write-Host "Installation completed. Log: $LogPath"
} finally {
    Stop-Transcript | Out-Null
}
