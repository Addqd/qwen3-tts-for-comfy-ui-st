[CmdletBinding()]
param(
    [string]$Config = "config/config.local.yaml",
    [switch]$NoManager,
    [switch]$Hidden,
    [int]$WaitSeconds = 0
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "comfyui-common.ps1")
$Settings = Get-ComfyUISettings -Config $Config
if (-not $Settings.enabled) { throw "ComfyUI is disabled in configuration." }
if ($Settings.host -ne "127.0.0.1") { throw "Only ComfyUI host 127.0.0.1 is allowed." }
if (-not $Settings.install_path) { throw "Set comfyui.install_path in the ignored config/config.local.yaml." }

$Root = (Resolve-Path -LiteralPath $Settings.install_path).Path
$Python = Join-Path $Root "python_embeded\python.exe"
$Main = Join-Path $Root "ComfyUI\main.py"
if (-not (Test-Path -LiteralPath $Python)) { throw "ComfyUI embedded Python was not found: $Python" }
if (-not (Test-Path -LiteralPath $Main)) { throw "ComfyUI main.py was not found: $Main" }
Sync-QwenTTSManagedWorkflow -Settings $Settings | Out-Null

if (Test-Path -LiteralPath $script:ComfyUIStatePath) {
    $OldState = Get-Content -Raw -LiteralPath $script:ComfyUIStatePath | ConvertFrom-Json
    $OldProcess = Test-ComfyUIOwnedProcess -State $OldState
    if ($OldProcess) {
        if (Test-LocalHttp -Uri "$($OldState.url)/system_stats") {
            Assert-QwenTTSCloneVoiceSchema -Url ([string]$OldState.url)
            Write-Host "ComfyUI is already running: $($OldState.url) (PID $($OldState.pid))"
            return
        }
        throw "The recorded ComfyUI process is running but its API is unavailable; stop it with scripts/stop-comfyui.ps1."
    }
}
if (Test-LocalPortInUse -Port ([int]$Settings.port)) { throw "Port $($Settings.port) is already in use; no process was stopped." }

& (Join-Path $script:ProjectRoot "integrations\comfyui\install.ps1") `
    -ComfyUIPath $Root -Mode Junction -Synchronize -Confirm:$false

$Runtime = Join-Path $script:ProjectRoot "runtime"
$LogDirectory = Split-Path -Parent ([string]$Settings.log_path)
New-Item -ItemType Directory -Force -Path $Runtime,$LogDirectory | Out-Null
$OutLog = [string]$Settings.log_path
$ErrLog = [System.IO.Path]::ChangeExtension($OutLog, ".err.log")
$Arguments = @(
    "-s", $Main, "--windows-standalone-build", "--listen", "127.0.0.1",
    "--port", [string]$Settings.port, "--disable-auto-launch"
)
$Manager = [bool]$Settings.manager_enabled -and -not $NoManager
if ($Manager) { $Arguments += "--enable-manager" }

$StartParameters = @{
    FilePath = $Python
    ArgumentList = $Arguments
    WorkingDirectory = (Join-Path $Root "ComfyUI")
    PassThru = $true
}
if ($Hidden) {
    $StartParameters.WindowStyle = "Hidden"
    $StartParameters.RedirectStandardOutput = $OutLog
    $StartParameters.RedirectStandardError = $ErrLog
} else {
    $StartParameters.WindowStyle = "Normal"
}
$Process = Start-Process @StartParameters
$State = [ordered]@{
    pid = $Process.Id
    start_ticks = $Process.StartTime.ToUniversalTime().Ticks
    executable = $Python
    install_path = $Root
    url = "http://127.0.0.1:$($Settings.port)"
    manager_enabled = $Manager
    visible_console = (-not $Hidden)
    stdout_log = $OutLog
    stderr_log = $ErrLog
    config = $Settings.config_path
}
$State | ConvertTo-Json | Set-Content -LiteralPath $script:ComfyUIStatePath -Encoding UTF8

$Timeout = if ($WaitSeconds -gt 0) { $WaitSeconds } else { [int]$Settings.startup_timeout_seconds }
$Deadline = (Get-Date).AddSeconds($Timeout)
$Ready = $false
do {
    Start-Sleep -Milliseconds 750
    if (-not (Test-ComfyUIOwnedProcess -State $State)) { throw "ComfyUI exited before its API became ready. Check $OutLog and $ErrLog." }
    $Ready = Test-LocalHttp -Uri "$($State.url)/system_stats"
} while (-not $Ready -and (Get-Date) -lt $Deadline)
if (-not $Ready) {
    $Owned = Test-ComfyUIOwnedProcess -State $State
    if ($Owned) { Stop-Process -Id $Owned.Id }
    throw "ComfyUI did not become ready within $Timeout seconds."
}
try {
    Assert-QwenTTSCloneVoiceSchema -Url ([string]$State.url)
} catch {
    $SchemaFailure = $_
    $Owned = Test-ComfyUIOwnedProcess -State $State
    if ($Owned) { Stop-Process -Id $Owned.Id }
    if (Test-Path -LiteralPath $script:ComfyUIStatePath) { Remove-Item -LiteralPath $script:ComfyUIStatePath }
    throw $SchemaFailure
}
Write-Host "ComfyUI: $($State.url)"
Write-Host "PID: $($State.pid); Manager: $Manager; console visible: $(-not $Hidden)"
Write-Host "Log: $OutLog"
