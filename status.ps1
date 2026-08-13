$StatePath = Join-Path $PSScriptRoot "runtime\server.json"
if (-not (Test-Path -LiteralPath $StatePath)) { Write-Host "Qwen3-TTS: stopped"; exit 1 }
$State = Get-Content -Raw -LiteralPath $StatePath -Encoding UTF8 | ConvertFrom-Json
$Facade = Get-Process -Id ([int]$State.facade.pid) -ErrorAction SilentlyContinue
$Engine = Get-Process -Id ([int]$State.engine.pid) -ErrorAction SilentlyContinue
if (-not $Facade -or -not $Engine) { Write-Host "Qwen3-TTS: incomplete/stale process state"; exit 1 }
try {
    $Port = & (Join-Path $PSScriptRoot ".venv\Scripts\python.exe") -c "from qwen3_tts_st.config import load_config; import sys; print(load_config(sys.argv[1]).get('server.port',8020))" $State.config
    Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5 | ConvertTo-Json -Depth 6
} catch { Write-Host "Processes are running but /health is unavailable: $($_.Exception.Message)"; exit 2 }
