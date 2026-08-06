$StatePath = Join-Path $PSScriptRoot "runtime\server.json"
if (-not (Test-Path -LiteralPath $StatePath)) { Write-Host "Qwen3-TTS: stopped"; exit 1 }
$State = Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json
$Process = Get-Process -Id $State.pid -ErrorAction SilentlyContinue
if (-not $Process) { Write-Host "Qwen3-TTS: stopped (stale PID $($State.pid))"; exit 1 }
try {
    $Config = & (Join-Path $PSScriptRoot ".venv\Scripts\python.exe") -c "from qwen3_tts_st.config import load_config; import sys; c=load_config(sys.argv[1]); print(c.get('server.port'))" $State.config
    $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Config/health" -TimeoutSec 5
    $Health | ConvertTo-Json -Depth 5
} catch { Write-Host "PID $($State.pid) is running but /health is unavailable: $($_.Exception.Message)"; exit 2 }
