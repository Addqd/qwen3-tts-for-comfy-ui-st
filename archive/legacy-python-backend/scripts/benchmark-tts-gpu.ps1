[CmdletBinding()]
param([string]$Text = "")
$Root = Split-Path -Parent $PSScriptRoot
Write-Host "Validated mode: CUDA float32 + SDPA. FP16 is not usable on this Turing system."
& (Join-Path $Root "start.ps1") -Config "config/config.cuda.yaml"
try { & (Join-Path $PSScriptRoot "test-russian.ps1") -Text $Text -Output "artifacts/audio-tests/benchmark-gpu.wav" } finally { & (Join-Path $Root "stop.ps1") }
