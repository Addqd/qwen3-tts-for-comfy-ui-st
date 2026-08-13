$ErrorActionPreference = "Stop"
$StatePath = Join-Path $PSScriptRoot "runtime\server.json"
$QwenStatePath = Join-Path $PSScriptRoot "runtime\qwentts.json"
if (-not (Test-Path -LiteralPath $StatePath)) { Write-Host "Backend is not running (no PID file)."; exit 0 }
$State = Get-Content -Raw -LiteralPath $StatePath -Encoding UTF8 | ConvertFrom-Json

function Stop-ProjectProcess {
    param($Record, [string]$Name)
    if (-not $Record) { return }
    $Process = Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue
    if (-not $Process) { return }
    $ExpectedStart = [DateTime]::Parse([string]$Record.start_time).ToUniversalTime()
    $ExpectedPath = [IO.Path]::GetFullPath([string]$Record.executable)
    $ActualPath = [IO.Path]::GetFullPath([string]$Process.Path)
    if ([Math]::Abs(($Process.StartTime.ToUniversalTime() - $ExpectedStart).TotalSeconds) -gt 2 -or
        -not $ActualPath.Equals($ExpectedPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Name PID identity mismatch; stop cancelled for PID $($Record.pid)"
    }
    Stop-Process -Id $Process.Id
    try { Wait-Process -Id $Process.Id -Timeout 15 -ErrorAction SilentlyContinue } catch { }
    Write-Host "$Name stopped (PID $($Record.pid))."
}

Stop-ProjectProcess $State.facade "Compatibility facade"
Stop-ProjectProcess $State.engine "qwentts engine"
Stop-ProjectProcess $State.runner "qwentts runner"
Remove-Item -LiteralPath $StatePath,$QwenStatePath -Force -ErrorAction SilentlyContinue
