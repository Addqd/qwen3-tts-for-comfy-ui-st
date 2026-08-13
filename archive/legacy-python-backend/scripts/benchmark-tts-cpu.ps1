[CmdletBinding()]
param([string]$Text = "")
$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $Root "start.ps1") -Config "config/config.cpu.yaml"
try { & (Join-Path $PSScriptRoot "test-russian.ps1") -Text $Text -Output "artifacts/audio-tests/benchmark-cpu.wav" } finally { & (Join-Path $Root "stop.ps1") }
