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
$ComfyUrl = "http://127.0.0.1:$($Settings.port)"
$BackendStarted = $false
$ComfyUIStarted = $false
$BackendStartAttempted = $false
$ComfyUIStartAttempted = $false
$ComfyProcess = $null

if ($WaitForComfyUIExit -and -not $VisibleComfyUIConsole) {
    throw "WaitForComfyUIExit requires VisibleComfyUIConsole. No services were started."
}

try {
    if (Test-LocalHttp -Uri "$ComfyUrl/system_stats") {
        Write-Host "ComfyUI is already ready: $ComfyUrl"
        if ($WaitForComfyUIExit) {
            if (-not (Test-Path -LiteralPath $script:ComfyUIStatePath)) {
                throw "ComfyUI is already running without a project PID file. Close the existing ComfyUI Python console, then run this launcher again. The backend was not started."
            }
            $ComfyState = Get-Content -Raw -LiteralPath $script:ComfyUIStatePath | ConvertFrom-Json
            $ComfyProcess = Test-ComfyUIOwnedProcess -State $ComfyState
            if (-not $ComfyProcess) {
                throw "ComfyUI is already running, but its project PID record is stale or belongs to another process. Close the existing ComfyUI Python console, then run this launcher again. The backend was not started."
            }
        }
    } else {
        $StartArguments = @{ Config = $Config }
        if (-not $VisibleComfyUIConsole) { $StartArguments.Hidden = $true }
        $ComfyUIStartAttempted = $true
        & (Join-Path $PSScriptRoot "start-comfyui.ps1") @StartArguments
        $ComfyUIStarted = $true
        if ($WaitForComfyUIExit) {
            $ComfyState = Get-Content -Raw -LiteralPath $script:ComfyUIStatePath | ConvertFrom-Json
            $ComfyProcess = Test-ComfyUIOwnedProcess -State $ComfyState
            if (-not $ComfyProcess) { throw "The newly started ComfyUI process could not be verified." }
        }
    }

    if (-not (Test-LocalHttp -Uri "$BackendUrl/health")) {
        $BackendStartAttempted = $true
        & (Join-Path $script:ProjectRoot "start.ps1") -Config $Config
        $BackendStarted = $true
    } else {
        Write-Host "TTS backend is already ready: $BackendUrl"
    }

    Write-Host "TTS backend: $BackendUrl (started by this command: $BackendStarted)"
    Write-Host "ComfyUI: $ComfyUrl (started by this command: $ComfyUIStarted)"

    if ($WaitForComfyUIExit) {
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
} catch {
    $Failure = $_
    if ($ComfyUIStartAttempted -and (Test-Path -LiteralPath $script:ComfyUIStatePath)) {
        try { & (Join-Path $PSScriptRoot "stop-comfyui.ps1") } catch { Write-Warning "Unable to clean up ComfyUI after startup failure: $($_.Exception.Message)" }
    }
    if ($BackendStartAttempted) {
        try { & (Join-Path $script:ProjectRoot "stop.ps1") } catch { Write-Warning "Unable to clean up the backend after startup failure: $($_.Exception.Message)" }
    }
    throw $Failure
}
