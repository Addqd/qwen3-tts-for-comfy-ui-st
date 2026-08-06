[CmdletBinding()]
param([string]$Config = "config/config.local.yaml")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "comfyui-common.ps1")
if (-not (Test-Path -LiteralPath $script:ComfyUIStatePath)) { Write-Host "ComfyUI: stopped (no project PID file)"; exit 1 }
$State = Get-Content -Raw -LiteralPath $script:ComfyUIStatePath | ConvertFrom-Json
$Process = Test-ComfyUIOwnedProcess -State $State
if (-not $Process) { Write-Host "ComfyUI: stopped or PID was reused; no process was changed"; exit 1 }
try {
    $Stats = Invoke-RestMethod -Uri "$($State.url)/system_stats" -TimeoutSec 5
    [ordered]@{
        status = "running"
        pid = $State.pid
        url = $State.url
        comfyui_version = $Stats.system.comfyui_version
        python_version = $Stats.system.python_version
        manager_enabled = $State.manager_enabled
    } | ConvertTo-Json -Depth 4
} catch {
    Write-Host "ComfyUI PID $($State.pid) is running but API is unavailable: $($_.Exception.Message)"
    exit 2
}
