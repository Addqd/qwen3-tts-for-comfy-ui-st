[CmdletBinding()]
param([string]$Config = "config/config.local.yaml", [switch]$VisibleComfyUIConsole)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "comfyui-common.ps1")
$Settings = Get-ComfyUISettings -Config $Config
$BackendUrl = "http://127.0.0.1:$($Settings.backend_port)"
$BackendStarted = $false
$ComfyUIStarted = $false

if (-not (Test-LocalHttp -Uri "$BackendUrl/health")) {
    & (Join-Path $script:ProjectRoot "start.ps1") -Config $Config
    $BackendStarted = $true
} else {
    Write-Host "TTS backend is already ready: $BackendUrl"
}

$ComfyUrl = "http://127.0.0.1:$($Settings.port)"
if (-not (Test-LocalHttp -Uri "$ComfyUrl/system_stats")) {
    $StartArguments = @{ Config = $Config }
    if (-not $VisibleComfyUIConsole) { $StartArguments.Hidden = $true }
    & (Join-Path $PSScriptRoot "start-comfyui.ps1") @StartArguments
    $ComfyUIStarted = $true
} else {
    Write-Host "ComfyUI is already ready: $ComfyUrl"
}

Write-Host "TTS backend: $BackendUrl (started by this command: $BackendStarted)"
Write-Host "ComfyUI: $ComfyUrl (started by this command: $ComfyUIStarted)"
