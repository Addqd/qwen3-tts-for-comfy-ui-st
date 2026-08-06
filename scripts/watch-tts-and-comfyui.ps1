[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][int]$LauncherPid,
    [Parameter(Mandatory = $true)][int64]$LauncherStartTicks,
    [Parameter(Mandatory = $true)][string]$LauncherExecutable,
    [Parameter(Mandatory = $true)][int]$BackendPid,
    [Parameter(Mandatory = $true)][int64]$BackendStartTicks,
    [Parameter(Mandatory = $true)][string]$BackendExecutable,
    [Parameter(Mandatory = $true)][int]$ComfyPid,
    [Parameter(Mandatory = $true)][int64]$ComfyStartTicks,
    [Parameter(Mandatory = $true)][string]$ComfyExecutable,
    [Parameter(Mandatory = $true)][string]$ProjectRoot
)

$ErrorActionPreference = "Stop"
$Runtime = Join-Path $ProjectRoot "runtime"
$LogPath = Join-Path $ProjectRoot "logs\combined-session-watch.log"
$WatchStatePath = Join-Path $Runtime "combined-watch.json"
$BackendStatePath = Join-Path $Runtime "server.json"
$ComfyStatePath = Join-Path $Runtime "comfyui.json"

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
        if (
            $Process.StartTime.ToUniversalTime().Ticks -eq $StartTicks -and
            $ActualPath.Equals($ExpectedPath, [System.StringComparison]::OrdinalIgnoreCase)
        ) { return $Process }
    } catch { }
    return $null
}

function Stop-ExactProcess {
    param([string]$Name, [int]$ProcessId, [int64]$StartTicks, [string]$Executable)
    $Process = Get-ExactProcess -ProcessId $ProcessId -StartTicks $StartTicks -Executable $Executable
    if (-not $Process) { return $true }
    try {
        Stop-Process -Id $Process.Id
        if (-not $Process.WaitForExit(15000)) { throw "$Name did not exit within 15 seconds." }
        Write-WatchLog "$Name stopped (PID $ProcessId)."
        return $true
    } catch {
        Write-WatchLog "Unable to stop $Name (PID $ProcessId): $($_.Exception.Message)"
        return $false
    }
}

function Remove-MatchingState {
    param([string]$Path, [int]$ProcessId, [int64]$StartTicks, [string]$StartProperty)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    try {
        $State = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
        if ([int]$State.pid -ne $ProcessId) { return }
        $StateTicks = if ($StartProperty -eq "start_ticks") {
            [int64]$State.start_ticks
        } else {
            [DateTime]::Parse([string]$State.start_time).ToUniversalTime().Ticks
        }
        if ([Math]::Abs($StateTicks - $StartTicks) -le [TimeSpan]::FromSeconds(2).Ticks) {
            Remove-Item -LiteralPath $Path
        }
    } catch { }
}

try {
    New-Item -ItemType Directory -Force -Path $Runtime,(Split-Path -Parent $LogPath) | Out-Null
    @{
        pid = $PID
        start_ticks = (Get-Process -Id $PID).StartTime.ToUniversalTime().Ticks
        launcher_pid = $LauncherPid
        backend_pid = $BackendPid
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
            $Reason = "backend closed"
            break
        }
        if (-not (Get-ExactProcess -ProcessId $ComfyPid -StartTicks $ComfyStartTicks -Executable $ComfyExecutable)) {
            $Reason = "ComfyUI closed"
            break
        }
        Start-Sleep -Milliseconds 500
    }

    Write-WatchLog "Stopping the project session because $Reason."
    $BackendStopped = Stop-ExactProcess -Name "backend" -ProcessId $BackendPid -StartTicks $BackendStartTicks -Executable $BackendExecutable
    $ComfyStopped = Stop-ExactProcess -Name "ComfyUI" -ProcessId $ComfyPid -StartTicks $ComfyStartTicks -Executable $ComfyExecutable
    if ($BackendStopped) { Remove-MatchingState -Path $BackendStatePath -ProcessId $BackendPid -StartTicks $BackendStartTicks -StartProperty "start_time" }
    if ($ComfyStopped) { Remove-MatchingState -Path $ComfyStatePath -ProcessId $ComfyPid -StartTicks $ComfyStartTicks -StartProperty "start_ticks" }
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
