[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "comfyui-common.ps1")
. (Join-Path $PSScriptRoot "session-common.ps1")
$WaitForSession = Test-ProjectSessionComponent -Names @("ComfyUI")
if ($env:QWEN3_TTS_SUPERVISOR_CLEANUP -ne "1" -and $WaitForSession) {
    $Supervisor = Get-ProjectSessionSupervisor
    if (-not $Supervisor) { throw "ComfyUI is managed by project-session, but its supervisor identity is unavailable; no unverified PID was stopped." }
    Request-ProjectSessionTeardown -Supervisor $Supervisor -Reason "ComfyUI stop requested"
    Wait-ProjectSessionTeardown -Seconds 25
    if (Test-Path -LiteralPath $script:ProjectSessionStatePath) { throw "Project shutdown is incomplete; session state was preserved." }
    Write-Host "Managed ComfyUI session stopped."
    return
}
if (-not (Test-Path -LiteralPath $script:ComfyUIStatePath)) { Write-Host "ComfyUI is not running (no project PID file or managed session)."; return }
try { $State = Get-Content -Raw -LiteralPath $script:ComfyUIStatePath | ConvertFrom-Json } catch {
    throw "ComfyUI PID state is malformed and no validated managed session is available; no unverified PID was stopped."
}
$Process = Get-Process -Id ([int]$State.pid) -ErrorAction SilentlyContinue
if (-not $Process) {
    Remove-Item -LiteralPath $script:ComfyUIStatePath
    Write-Host "ComfyUI has exited; stale project PID file removed."
    return
}
$Owned = Test-ComfyUIOwnedProcess -State $State
if (-not $Owned) { throw "PID $($State.pid) belongs to another process; stop cancelled and PID file preserved." }
Stop-Process -Id $Owned.Id
if (-not $Owned.WaitForExit(15000)) { throw "ComfyUI process did not exit within 15 seconds." }
Remove-Item -LiteralPath $script:ComfyUIStatePath
Write-Host "Project ComfyUI stopped (PID $($State.pid))."
