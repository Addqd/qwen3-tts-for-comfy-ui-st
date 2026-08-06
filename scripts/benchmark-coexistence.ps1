[CmdletBinding()]
param([string]$Text = "")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Before = & nvidia-smi --query-gpu=memory.free,memory.used,utilization.gpu --format=csv,noheader
Write-Host "Before: $Before"
& (Join-Path $Root "start.ps1") -Config "config/config.cpu.yaml"
try { & (Join-Path $PSScriptRoot "test-russian.ps1") -Text $Text -Output "artifacts/audio-tests/benchmark-coexistence.wav" } finally { & (Join-Path $Root "stop.ps1") }
$After = & nvidia-smi --query-gpu=memory.free,memory.used,utilization.gpu --format=csv,noheader
Write-Host "After : $After"
Write-Host "No external process was started, stopped, or reconfigured."
