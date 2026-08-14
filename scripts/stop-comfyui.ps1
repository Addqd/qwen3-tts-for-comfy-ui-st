[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "comfyui-common.ps1")
. (Join-Path $PSScriptRoot "session-common.ps1")
if (-not (Test-Path -LiteralPath $script:ComfyUIStatePath)) { Write-Host "ComfyUI is not running (no project PID file)."; return }
$State = Get-Content -Raw -LiteralPath $script:ComfyUIStatePath | ConvertFrom-Json
$WaitForSession = Test-ProjectSessionComponent -Names @("ComfyUI")
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
if ($env:QWEN3_TTS_SUPERVISOR_CLEANUP -ne "1" -and $WaitForSession) { Wait-ProjectSessionTeardown }
Write-Host "Project ComfyUI stopped (PID $($State.pid))."
