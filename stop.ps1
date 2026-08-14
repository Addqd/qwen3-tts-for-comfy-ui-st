$ErrorActionPreference = "Stop"
$StatePath = Join-Path $PSScriptRoot "runtime\server.json"
$QwenStatePath = Join-Path $PSScriptRoot "runtime\qwentts.json"
if (-not (Test-Path -LiteralPath $StatePath)) { Write-Host "Backend is not running (no PID file)."; exit 0 }
$State = Get-Content -Raw -LiteralPath $StatePath -Encoding UTF8 | ConvertFrom-Json

function Stop-ProjectProcess {
    param($Record, [string]$Name)
    if (-not $Record) { return $true }
    $Process = Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue
    if (-not $Process) { return $true }
    try {
        $ExpectedStart = [DateTime]::Parse([string]$Record.start_time).ToUniversalTime()
        $ExpectedPath = [IO.Path]::GetFullPath([string]$Record.executable)
        $ActualPath = [IO.Path]::GetFullPath([string]$Process.Path)
    } catch {
        Write-Warning "$Name identity is unreadable; stop cancelled for PID $($Record.pid): $($_.Exception.Message)"
        return $false
    }
    if ([Math]::Abs(($Process.StartTime.ToUniversalTime() - $ExpectedStart).TotalSeconds) -gt 2 -or
        -not $ActualPath.Equals($ExpectedPath, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Warning "$Name PID identity mismatch; stop cancelled for PID $($Record.pid)"
        return $false
    }
    try {
        Stop-Process -Id $Process.Id -ErrorAction Stop
        if (-not $Process.WaitForExit(15000)) {
            Write-Warning "$Name did not exit within 15 seconds (PID $($Record.pid))."
            return $false
        }
    } catch {
        if (-not (Get-Process -Id $Process.Id -ErrorAction SilentlyContinue)) {
            Write-Host "$Name exited during the stop attempt (PID $($Record.pid))."
            return $true
        }
        Write-Warning "$Name could not be stopped (PID $($Record.pid)): $($_.Exception.Message)"
        return $false
    }
    Write-Host "$Name stopped (PID $($Record.pid))."
    return $true
}

$Results = @(
    Stop-ProjectProcess $State.facade "Compatibility facade"
    Stop-ProjectProcess $State.engine "qwentts engine"
    Stop-ProjectProcess $State.runner "qwentts runner"
)
if ($Results -notcontains $false) {
    Remove-Item -LiteralPath $StatePath,$QwenStatePath -Force -ErrorAction SilentlyContinue
    if ($env:QWEN3_TTS_SUPERVISOR_CLEANUP -ne "1") {
        $SessionState = Join-Path $PSScriptRoot "runtime\project-session.json"
        $Deadline = (Get-Date).AddSeconds(20)
        while ((Test-Path -LiteralPath $SessionState) -and (Get-Date) -lt $Deadline) { Start-Sleep -Milliseconds 200 }
        if (Test-Path -LiteralPath $SessionState) { throw "Project session supervisor did not complete teardown within 20 seconds." }
    }
    exit 0
}
throw "Project shutdown is incomplete. Runtime state was preserved for a safe retry."
