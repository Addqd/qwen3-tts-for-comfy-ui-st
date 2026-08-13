[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][int]$LauncherPid,
    [Parameter(Mandatory = $true)][int64]$LauncherStartTicks,
    [Parameter(Mandatory = $true)][string]$LauncherExecutable,
    [Parameter(Mandatory = $true)][int]$BackendPid,
    [Parameter(Mandatory = $true)][int64]$BackendStartTicks,
    [Parameter(Mandatory = $true)][string]$BackendExecutable,
    [Parameter(Mandatory = $true)][int]$EnginePid,
    [Parameter(Mandatory = $true)][int64]$EngineStartTicks,
    [Parameter(Mandatory = $true)][string]$EngineExecutable,
    [Parameter(Mandatory = $true)][int]$RunnerPid,
    [Parameter(Mandatory = $true)][int64]$RunnerStartTicks,
    [Parameter(Mandatory = $true)][string]$RunnerExecutable,
    [Parameter(Mandatory = $true)][int]$ComfyPid,
    [Parameter(Mandatory = $true)][int64]$ComfyStartTicks,
    [Parameter(Mandatory = $true)][string]$ComfyExecutable,
    [Parameter(Mandatory = $true)][string]$ProjectRoot
)

$ErrorActionPreference = "Stop"
$Runtime = Join-Path $ProjectRoot "runtime"
$LogPath = Join-Path $ProjectRoot "logs\combined-session-watch.log"
$WatchStatePath = Join-Path $Runtime "combined-watch.json"

function Write-WatchLog {
    param([string]$Message)
    try {
        $Timestamp = (Get-Date).ToUniversalTime().ToString("o")
        Add-Content -LiteralPath $LogPath -Value "$Timestamp $Message" -Encoding UTF8
    } catch { }
}

function Get-ExactProcess {
    param([int]$ProcessId, [int64]$StartTicks, [string]$Executable)
    $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $Process) { return $null }
    try {
        $ExpectedPath = [System.IO.Path]::GetFullPath($Executable)
        $ActualPath = [System.IO.Path]::GetFullPath([string]$Process.Path)
        $StartMatches = [Math]::Abs($Process.StartTime.ToUniversalTime().Ticks - $StartTicks) -le [TimeSpan]::FromSeconds(2).Ticks
        if ($StartMatches -and $ActualPath.Equals($ExpectedPath, [System.StringComparison]::OrdinalIgnoreCase)) { return $Process }
    } catch { }
    return $null
}

try {
    New-Item -ItemType Directory -Force -Path $Runtime,(Split-Path -Parent $LogPath) | Out-Null
    @{
        pid = $PID
        start_ticks = (Get-Process -Id $PID).StartTime.ToUniversalTime().Ticks
        launcher_pid = $LauncherPid
        backend_pid = $BackendPid
        engine_pid = $EnginePid
        runner_pid = $RunnerPid
        comfyui_pid = $ComfyPid
    } | ConvertTo-Json | Set-Content -LiteralPath $WatchStatePath -Encoding UTF8
    Write-WatchLog "Session watcher started (PID $PID)."

    $Reason = "unknown"
    while ($true) {
        if (-not (Get-ExactProcess -ProcessId $LauncherPid -StartTicks $LauncherStartTicks -Executable $LauncherExecutable)) {
            $Reason = "launcher closed"
            break
        }
        if (-not (Get-ExactProcess -ProcessId $BackendPid -StartTicks $BackendStartTicks -Executable $BackendExecutable)) {
            $Reason = "compatibility facade closed"
            break
        }
        if (-not (Get-ExactProcess -ProcessId $EnginePid -StartTicks $EngineStartTicks -Executable $EngineExecutable)) {
            $Reason = "qwentts.cpp engine closed"
            break
        }
        if (-not (Get-ExactProcess -ProcessId $RunnerPid -StartTicks $RunnerStartTicks -Executable $RunnerExecutable)) {
            $Reason = "qwentts.cpp runner closed"
            break
        }
        if (-not (Get-ExactProcess -ProcessId $ComfyPid -StartTicks $ComfyStartTicks -Executable $ComfyExecutable)) {
            $Reason = "ComfyUI closed"
            break
        }
        Start-Sleep -Milliseconds 500
    }

    Write-WatchLog "Stopping the project session because $Reason."
    try {
        & (Join-Path $ProjectRoot "scripts\stop-comfyui.ps1")
    } catch {
        Write-WatchLog "ComfyUI cleanup failed: $($_.Exception.Message)"
    }
    try {
        & (Join-Path $ProjectRoot "stop.ps1")
    } catch {
        Write-WatchLog "qwentts.cpp cleanup failed: $($_.Exception.Message)"
    }
} catch {
    Write-WatchLog "Watcher failure: $($_.Exception.Message)"
} finally {
    if (Test-Path -LiteralPath $WatchStatePath) {
        try {
            $WatchState = Get-Content -Raw -LiteralPath $WatchStatePath | ConvertFrom-Json
            if ([int]$WatchState.pid -eq $PID) { Remove-Item -LiteralPath $WatchStatePath }
        } catch { }
    }
}
