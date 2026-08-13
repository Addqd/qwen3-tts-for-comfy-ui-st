[CmdletBinding()]
param([string]$Text = "")
$Root = Split-Path -Parent $PSScriptRoot
Write-Host "This validated mode spawns an FP32 CUDA worker per request and releases VRAM afterward."
& (Join-Path $Root "start.ps1") -Config "config/config.cuda-on-demand.yaml"
try { & (Join-Path $PSScriptRoot "test-russian.ps1") -Text $Text -Output "artifacts/audio-tests/benchmark-on-demand.wav" } finally { & (Join-Path $Root "stop.ps1") }
