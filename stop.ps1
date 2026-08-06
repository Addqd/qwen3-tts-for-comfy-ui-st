$ErrorActionPreference = "Stop"
$StatePath = Join-Path $PSScriptRoot "runtime\server.json"
if (-not (Test-Path -LiteralPath $StatePath)) { Write-Host "Backend is not running (no PID file)."; exit 0 }
$State = Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json
$Process = Get-Process -Id $State.pid -ErrorAction SilentlyContinue
if (-not $Process) { Remove-Item -LiteralPath $StatePath; Write-Host "Process has exited; stale PID file removed."; exit 0 }
$Expected = [DateTime]::Parse($State.start_time).ToUniversalTime()
if ([Math]::Abs(($Process.StartTime.ToUniversalTime() - $Expected).TotalSeconds) -gt 2) { throw "PID was reused by another process; stop cancelled." }
Stop-Process -Id $Process.Id
if (-not $Process.WaitForExit(15000)) { throw "Project process did not exit within 15 seconds." }
Remove-Item -LiteralPath $StatePath
Write-Host "Project backend stopped (PID $($State.pid))."
