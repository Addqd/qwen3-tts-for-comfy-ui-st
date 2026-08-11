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
$BackendProcess = $null
$ComfyProcess = $null
$WatchProcess = $null

function Get-BackendOwnedProcess {
    param($State)
    $Process = Get-Process -Id ([int]$State.pid) -ErrorAction SilentlyContinue
    if (-not $Process) { return $null }
    try {
        $ExpectedPath = [System.IO.Path]::GetFullPath((Join-Path $script:ProjectRoot ".venv\Scripts\python.exe"))
        $ActualPath = [System.IO.Path]::GetFullPath([string]$Process.Path)
        $ExpectedStart = [DateTime]::Parse([string]$State.start_time).ToUniversalTime()
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

function Quote-ProcessArgument {
    param([string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Start-SessionWatcher {
    param($LauncherProcess, $BackendProcess, $ComfyProcess)
    $WatcherScript = Join-Path $PSScriptRoot "watch-tts-and-comfyui.ps1"
    $BackendExecutable = [System.IO.Path]::GetFullPath([string]$BackendProcess.Path)
    $ComfyExecutable = [System.IO.Path]::GetFullPath([string]$ComfyProcess.Path)
    $LauncherExecutable = [System.IO.Path]::GetFullPath([string]$LauncherProcess.Path)
    $Arguments = @(
        "-NoLogo", "-NoProfile", "-File", (Quote-ProcessArgument $WatcherScript),
        "-LauncherPid", [string]$LauncherProcess.Id,
        "-LauncherStartTicks", [string]$LauncherProcess.StartTime.ToUniversalTime().Ticks,
        "-LauncherExecutable", (Quote-ProcessArgument $LauncherExecutable),
        "-BackendPid", [string]$BackendProcess.Id,
        "-BackendStartTicks", [string]$BackendProcess.StartTime.ToUniversalTime().Ticks,
        "-BackendExecutable", (Quote-ProcessArgument $BackendExecutable),
        "-ComfyPid", [string]$ComfyProcess.Id,
        "-ComfyStartTicks", [string]$ComfyProcess.StartTime.ToUniversalTime().Ticks,
        "-ComfyExecutable", (Quote-ProcessArgument $ComfyExecutable),
        "-ProjectRoot", (Quote-ProcessArgument $script:ProjectRoot)
    )
    $StartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $StartInfo.FileName = $LauncherExecutable
    $StartInfo.Arguments = $Arguments -join " "
    $StartInfo.WorkingDirectory = $script:ProjectRoot
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $StartInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    return [System.Diagnostics.Process]::Start($StartInfo)
}

if ($WaitForComfyUIExit -and -not $VisibleComfyUIConsole) {
    throw "WaitForComfyUIExit requires VisibleComfyUIConsole. No services were started."
}

try {
    if (Test-LocalHttp -Uri "$ComfyUrl/system_stats") {
        Assert-QwenTTSCloneVoiceSchema -Url $ComfyUrl
        Write-Host "ComfyUI is already ready: $ComfyUrl"
        if ($WaitForComfyUIExit) {
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
        if ($WaitForComfyUIExit) { $BackendProcess = Get-RequiredBackendProcess }
    } else {
        Write-Host "TTS backend is already ready: $BackendUrl"
        if ($WaitForComfyUIExit) { $BackendProcess = Get-RequiredBackendProcess }
    }

    Write-Host "TTS backend: $BackendUrl (started by this command: $BackendStarted)"
    Write-Host "ComfyUI: $ComfyUrl (started by this command: $ComfyUIStarted)"

    if ($WaitForComfyUIExit) {
        $LauncherProcess = Get-Process -Id $PID
        $WatchProcess = Start-SessionWatcher -LauncherProcess $LauncherProcess -BackendProcess $BackendProcess -ComfyProcess $ComfyProcess
        Start-Sleep -Milliseconds 750
        if ($WatchProcess.HasExited) { throw "The session watcher exited before it became ready." }

        Write-Host "Session watcher: PID $($WatchProcess.Id)"
        Write-Host "Close this launcher window or the ComfyUI Python console to stop BOTH services."
        try {
            Wait-Process -Id $ComfyProcess.Id
        } finally {
            try { & (Join-Path $script:ProjectRoot "stop.ps1") } catch { Write-Warning "Backend cleanup: $($_.Exception.Message)" }
            try { & (Join-Path $PSScriptRoot "stop-comfyui.ps1") } catch { Write-Warning "ComfyUI cleanup: $($_.Exception.Message)" }
            if ($WatchProcess -and -not $WatchProcess.HasExited) {
                try { Wait-Process -Id $WatchProcess.Id -Timeout 10 -ErrorAction SilentlyContinue } catch { }
                $WatchProcess.Refresh()
                if (-not $WatchProcess.HasExited) { Stop-Process -Id $WatchProcess.Id }
            }
        }
        Write-Host "Project session stopped. Backend and ComfyUI are closed."
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
