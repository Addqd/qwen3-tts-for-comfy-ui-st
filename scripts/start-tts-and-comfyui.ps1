[CmdletBinding()]
param(
    [string]$Config = "config/config.local.yaml",
    [switch]$VisibleComfyUIConsole,
    [switch]$WaitForComfyUIExit
)

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

if ($WaitForComfyUIExit) {
    if (-not $VisibleComfyUIConsole) { throw "WaitForComfyUIExit requires VisibleComfyUIConsole." }
    if (-not (Test-Path -LiteralPath $script:ComfyUIStatePath)) { throw "ComfyUI project PID file is unavailable." }
    $ComfyState = Get-Content -Raw -LiteralPath $script:ComfyUIStatePath | ConvertFrom-Json
    $ComfyProcess = Test-ComfyUIOwnedProcess -State $ComfyState
    if (-not $ComfyProcess) { throw "The recorded ComfyUI process is not running or its PID was reused." }
    Write-Host "Close the separate ComfyUI Python console to stop this session."
    Write-Host "This launcher will then stop only the backend it started itself."
    try {
        Wait-Process -Id $ComfyProcess.Id
    } finally {
        & (Join-Path $PSScriptRoot "stop-comfyui.ps1")
        if ($BackendStarted) { & (Join-Path $script:ProjectRoot "stop.ps1") }
    }
    Write-Host "ComfyUI closed. Services started by this launcher are stopped."
}
