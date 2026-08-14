[CmdletBinding()]
param(
    [string]$Config = "config/config.local.yaml",
    [switch]$VisibleComfyUIConsole,
    [switch]$WaitForComfyUIExit
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "comfyui-common.ps1")
. (Join-Path $PSScriptRoot "session-common.ps1")
$Settings = Get-ComfyUISettings -Config $Config
$BackendUrl = "http://127.0.0.1:$($Settings.backend_port)"
$ComfyUrl = "http://127.0.0.1:$($Settings.port)"
$BackendStarted = $false
$ComfyUIStarted = $false
$BackendStartAttempted = $false
$ComfyUIStartAttempted = $false
$BackendProcess = $null
$ComfyProcess = $null
$SupervisorProcess = $null

function Get-BackendOwnedProcess {
    param($State)
    $Record = if ($State.facade) { $State.facade } else { $State }
    return Get-BackendRecordProcess -Record $Record
}

function Get-BackendRecordProcess {
    param($Record)
    if (-not $Record) { return $null }
    $Process = Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue
    if (-not $Process) { return $null }
    try {
        $ExpectedPath = [System.IO.Path]::GetFullPath([string]$Record.executable)
        $ActualPath = [System.IO.Path]::GetFullPath([string]$Process.Path)
        $ExpectedStart = [DateTime]::Parse([string]$Record.start_time).ToUniversalTime()
        $StartMatches = [Math]::Abs(($Process.StartTime.ToUniversalTime() - $ExpectedStart).TotalSeconds) -le 2
        if ($StartMatches -and $ActualPath.Equals($ExpectedPath, [System.StringComparison]::OrdinalIgnoreCase)) { return $Process }
    } catch { }
    return $null
}

function Save-AdoptedComfyUIState {
    param($Process, $Settings)
    $Root = (Resolve-Path -LiteralPath ([string]$Settings.install_path)).Path
    $Python = Join-Path $Root "python_embeded\python.exe"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $script:ComfyUIStatePath) | Out-Null
    $State = [ordered]@{
        pid = $Process.Id
        start_ticks = $Process.StartTime.ToUniversalTime().Ticks
        executable = $Python
        install_path = $Root
        url = "http://127.0.0.1:$($Settings.port)"
        manager_enabled = [bool]$Settings.manager_enabled
        visible_console = $true
        adopted = $true
        stdout_log = [string]$Settings.log_path
        stderr_log = [System.IO.Path]::ChangeExtension([string]$Settings.log_path, ".err.log")
        config = $Settings.config_path
    }
    $State | ConvertTo-Json | Set-Content -LiteralPath $script:ComfyUIStatePath -Encoding UTF8
    return [PSCustomObject]$State
}

function Get-RequiredBackendProcess {
    $StatePath = Join-Path $script:ProjectRoot "runtime\server.json"
    if (-not (Test-Path -LiteralPath $StatePath)) {
        throw "The backend API is ready, but its project PID file is missing. No running process was stopped."
    }
    $State = Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json
    $Process = Get-BackendOwnedProcess -State $State
    if (-not $Process) { throw "The backend PID record is stale or belongs to another process. No running process was stopped." }
    return $Process
}

if ($WaitForComfyUIExit -and -not $VisibleComfyUIConsole) {
    throw "WaitForComfyUIExit requires VisibleComfyUIConsole. No services were started."
}

try {
    $SupervisorProcess = Start-OrJoin-ProjectSession -OwnerName "combined launcher" -MonitorOwner -Components @()
    $CombinedOwnerRegistered = $true
    if (Test-LocalHttp -Uri "$ComfyUrl/system_stats") {
        Assert-QwenTTSCloneVoiceSchema -Url $ComfyUrl
        Write-Host "ComfyUI is already ready: $ComfyUrl"
        if (Test-Path -LiteralPath $script:ComfyUIStatePath) {
            $ComfyState = Get-Content -Raw -LiteralPath $script:ComfyUIStatePath | ConvertFrom-Json
            $ComfyProcess = Test-ComfyUIOwnedProcess -State $ComfyState
        }
        if (-not $ComfyProcess) {
            $ComfyProcess = Get-ComfyUIListenerOwnedProcess -Settings $Settings
            if (-not $ComfyProcess) {
                throw "Port $($Settings.port) is served by a process that cannot be verified as this project's ComfyUI. The backend was not started and no process was stopped."
            }
            $ComfyState = Save-AdoptedComfyUIState -Process $ComfyProcess -Settings $Settings
            Write-Host "Recovered project ownership for the existing ComfyUI process (PID $($ComfyProcess.Id))."
        }
    } else {
        $StartArguments = @{ Config = $Config; NoSessionSupervisor = $true }
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
        & (Join-Path $script:ProjectRoot "start.ps1") -Config $Config -NoSessionSupervisor
        $BackendStarted = $true
        if ($WaitForComfyUIExit) { $BackendProcess = Get-RequiredBackendProcess }
    } else {
        Write-Host "TTS backend is already ready: $BackendUrl"
        if ($WaitForComfyUIExit) { $BackendProcess = Get-RequiredBackendProcess }
    }

    Write-Host "TTS backend: $BackendUrl (started by this command: $BackendStarted)"
    Write-Host "ComfyUI: $ComfyUrl (started by this command: $ComfyUIStarted)"

    if (-not $BackendProcess) { $BackendProcess = Get-RequiredBackendProcess }
    if (-not $ComfyProcess) {
        $ComfyState = Get-Content -Raw -LiteralPath $script:ComfyUIStatePath | ConvertFrom-Json
        $ComfyProcess = Test-ComfyUIOwnedProcess -State $ComfyState
    }
    if (-not $ComfyProcess) { throw "The project ComfyUI process could not be verified for session supervision." }
    $BackendStatePath = Join-Path $script:ProjectRoot "runtime\server.json"
    $BackendState = Get-Content -Raw -LiteralPath $BackendStatePath | ConvertFrom-Json
    $EngineProcess = Get-BackendRecordProcess -Record $BackendState.engine
    $RunnerProcess = Get-BackendRecordProcess -Record $BackendState.runner
    if (-not $EngineProcess -or -not $RunnerProcess) {
        throw "The qwentts.cpp engine or its runner could not be verified for session supervision."
    }
    $SupervisorProcess = Start-OrJoin-ProjectSession -Components @(
        @{ name = "facade"; pid = $BackendProcess.Id },
        @{ name = "qwentts runner"; pid = $RunnerProcess.Id },
        @{ name = "qwentts.cpp"; pid = $EngineProcess.Id },
        @{ name = "ComfyUI"; pid = $ComfyProcess.Id }
    )
    Write-Host "Project session supervisor: PID $($SupervisorProcess.Id)"

    if ($WaitForComfyUIExit) {
        Write-Host "Close this launcher window or the ComfyUI Python console to stop qwentts.cpp and ComfyUI."
        try {
            Wait-Process -Id $ComfyProcess.Id
        } finally {
            try { & (Join-Path $script:ProjectRoot "stop.ps1") } catch { Write-Warning "Backend cleanup: $($_.Exception.Message)" }
            try { & (Join-Path $PSScriptRoot "stop-comfyui.ps1") } catch { Write-Warning "ComfyUI cleanup: $($_.Exception.Message)" }
            if ($SupervisorProcess -and -not $SupervisorProcess.HasExited) {
                try { Wait-Process -Id $SupervisorProcess.Id -Timeout 15 -ErrorAction SilentlyContinue } catch { }
                $SupervisorProcess.Refresh()
                if (-not $SupervisorProcess.HasExited) { Stop-Process -Id $SupervisorProcess.Id }
            }
        }
        Write-Host "Project session stopped. Backend and ComfyUI are closed."
    } elseif ($CombinedOwnerRegistered) {
        Release-ProjectSessionOwner -OwnerName "combined launcher"
        $CombinedOwnerRegistered = $false
    }
} catch {
    $Failure = $_
    if ($SupervisorProcess) {
        Request-ProjectSessionTeardown -Supervisor $SupervisorProcess -Reason "combined startup failed"
    }
    throw $Failure
}
