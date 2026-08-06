[CmdletBinding()]
param([string]$Config = "config/config.local.yaml", [int]$WaitSeconds = 60)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ConfigPath = Join-Path $ProjectRoot $Config
$Runtime = Join-Path $ProjectRoot "runtime"
$Logs = Join-Path $ProjectRoot "logs"
if (-not (Test-Path -LiteralPath $Python)) { throw ".venv was not found. Run .\scripts\install.ps1" }
if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "Configuration was not found: $ConfigPath" }
New-Item -ItemType Directory -Force -Path $Runtime,$Logs | Out-Null
$Existing = Join-Path $Runtime "server.json"
if (Test-Path -LiteralPath $Existing) {
    $State = Get-Content -Raw -LiteralPath $Existing | ConvertFrom-Json
    if (Get-Process -Id $State.pid -ErrorAction SilentlyContinue) { throw "Backend is already running, PID $($State.pid)" }
}
$ConfigJson = & $Python -c "from qwen3_tts_st.config import load_config; import json,sys; c=load_config(sys.argv[1]); print(json.dumps({'host':c.get('server.host'),'port':c.get('server.port')}))" $ConfigPath | ConvertFrom-Json
if ($ConfigJson.host -ne "127.0.0.1") { throw "Only host 127.0.0.1 is allowed" }
$Listener = Get-NetTCPConnection -State Listen -LocalPort $ConfigJson.port -ErrorAction SilentlyContinue
if ($Listener) { throw "Port $($ConfigJson.port) is already used by PID $($Listener.OwningProcess -join ',')" }
$StartInfo = New-Object System.Diagnostics.ProcessStartInfo
$StartInfo.FileName = $Python
$EscapedConfig = $ConfigPath.Replace('"', '\"')
$StartInfo.Arguments = "-m qwen3_tts_st.cli --config `"$EscapedConfig`""
$StartInfo.WorkingDirectory = $ProjectRoot
$StartInfo.UseShellExecute = $false
$StartInfo.CreateNoWindow = $true
$StartInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$StartInfo.RedirectStandardOutput = $true
$StartInfo.RedirectStandardError = $true
$Process = [System.Diagnostics.Process]::Start($StartInfo)
@{ pid=$Process.Id; start_time=$Process.StartTime.ToUniversalTime().ToString("o"); config=$ConfigPath } | ConvertTo-Json | Set-Content -LiteralPath $Existing -Encoding UTF8
$Url = "http://127.0.0.1:$($ConfigJson.port)"
$Deadline = (Get-Date).AddSeconds($WaitSeconds)
do {
    Start-Sleep -Milliseconds 500
    if (-not (Get-Process -Id $Process.Id -ErrorAction SilentlyContinue)) { throw "Backend exited before /health became ready" }
    try { $Health = Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 3; break } catch { }
} while ((Get-Date) -lt $Deadline)
if (-not $Health) { throw "Backend did not answer /health within $WaitSeconds seconds" }
Write-Host "Qwen3-TTS: $Url"
Write-Host "Mode: $($Health.mode), device=$($Health.device), dtype=$($Health.dtype), attention=$($Health.attention)"
Write-Host "Selection reason: $($Health.mode_reason)"
$Voices = Invoke-RestMethod -Uri "$Url/v1/voices" -TimeoutSec 5
Write-Host "Voices: $((@($Voices.data | ForEach-Object { $_.voice_id }) -join ', '))"
