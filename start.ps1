[CmdletBinding()]
param([string]$Config = "config/config.local.yaml", [int]$WaitSeconds = 90, [switch]$NoSessionSupervisor)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$script:ProjectRoot = $ProjectRoot
. (Join-Path $ProjectRoot "scripts\session-common.ps1")
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ConfigPath = [IO.Path]::GetFullPath($(if ([IO.Path]::IsPathRooted($Config)) { $Config } else { Join-Path $ProjectRoot $Config }))
$Runtime = Join-Path $ProjectRoot "runtime"
$StatePath = Join-Path $Runtime "server.json"
$QwenStatePath = Join-Path $Runtime "qwentts.json"
if (-not (Test-Path -LiteralPath $Python)) { throw ".venv was not found. Run .\scripts\install.ps1" }
if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "Configuration was not found: $ConfigPath" }
New-Item -ItemType Directory -Force -Path $Runtime,(Join-Path $ProjectRoot "logs") | Out-Null

if (Test-Path -LiteralPath $StatePath) {
    $Existing = Get-Content -Raw -LiteralPath $StatePath -Encoding UTF8 | ConvertFrom-Json
    if (Get-Process -Id ([int]$Existing.facade.pid) -ErrorAction SilentlyContinue) { throw "Backend is already running, PID $($Existing.facade.pid)" }
}
& (Join-Path $ProjectRoot "scripts\ensure-qwentts-models.ps1")
& (Join-Path $ProjectRoot "scripts\verify-qwentts-runtime.ps1") | Out-Null
$ConfigJson = & $Python -c "from qwen3_tts_st.config import load_config; import json,sys; c=load_config(sys.argv[1]); talker,codec=c.qwentts_models(); print(json.dumps({'public':int(c.get('server.port',8020)),'engine':int(c.get('qwentts.port',8030)),'talker':talker.name,'codec':codec.name}))" $ConfigPath | ConvertFrom-Json
foreach ($Port in @([int]$ConfigJson.public,[int]$ConfigJson.engine)) {
    $Listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($Listener) { throw "Port $Port is already used by PID $($Listener.OwningProcess -join ',')" }
}

$RunnerScript = Join-Path $ProjectRoot "scripts\qwentts-runner.py"
$SessionSupervisor = $null
$StartupOwnerRegistered = $false
$Runner = $null
$RunnerProcess = $null
$Engine = $null
$Facade = $null
$EngineReady = $false
$Health = $null
try {
    if (-not $NoSessionSupervisor) {
        $SessionSupervisor = Start-OrJoin-ProjectSession -OwnerName "backend startup" -MonitorOwner -Components @()
        $StartupOwnerRegistered = $true
    } else {
        $SessionSupervisor = Get-ProjectSessionSupervisor
        if (-not $SessionSupervisor) { throw "NoSessionSupervisor requires an existing validated managed project session." }
    }
    $Runner = Start-ManagedProjectProcess -Name "qwentts runner launcher" -FilePath $Python `
        -ArgumentList @($RunnerScript,"--config",$ConfigPath) -WorkingDirectory $ProjectRoot `
        -Environment @{ QWEN3_TTS_SESSION_INTERNAL = "1" } -Hidden
    $EngineDeadline = (Get-Date).AddSeconds($WaitSeconds)
    do {
        Start-Sleep -Milliseconds 500
        if ($Runner.HasExited) { throw "qwentts runner exited before readiness" }
        try { $null = Invoke-RestMethod -Uri "http://127.0.0.1:$($ConfigJson.engine)/health" -TimeoutSec 2; $EngineReady = $true } catch { }
    } while (-not $EngineReady -and (Get-Date) -lt $EngineDeadline)
    if (-not $EngineReady) { throw "qwentts did not answer /health within $WaitSeconds seconds" }
    $QwenState = Get-Content -Raw -LiteralPath $QwenStatePath -Encoding UTF8 | ConvertFrom-Json
    $Engine = Get-Process -Id ([int]$QwenState.pid) -ErrorAction Stop
    $RunnerProcess = Get-Process -Id ([int]$QwenState.runner_pid) -ErrorAction Stop
    if ([int]$QwenState.runner_parent_pid -ne $Runner.Id -or -not $QwenState.session_id) {
        throw "qwentts runtime state does not belong to the current runner session"
    }
    $SessionLog = Join-Path $ProjectRoot "logs\qwentts.err.log"
    $SessionMarker = "[qwen3-tts-st] session=$($QwenState.session_id)"
    $CurrentLog = if (Test-Path -LiteralPath $SessionLog) { Get-Content -Raw -LiteralPath $SessionLog -Encoding UTF8 } else { "" }
    if (-not $CurrentLog.Contains($SessionMarker) -or -not $CurrentLog.Contains("Talker backend: CUDA0")) {
        throw "qwentts started without current-session confirmation of CUDA0 backend"
    }
    $QwenState | Add-Member -NotePropertyName verified_backend -NotePropertyValue "CUDA0" -Force
    $QwenState | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $QwenStatePath -Encoding UTF8

    $SessionSupervisor = Start-OrJoin-ProjectSession -Components @(
        @{ name = "qwentts runner"; pid = $RunnerProcess.Id },
        @{ name = "qwentts.cpp"; pid = $Engine.Id }
    )
    $Facade = Start-ManagedProjectProcess -Name "facade" -FilePath $Python `
        -ArgumentList @("-m","qwen3_tts_st.cli","--config",$ConfigPath) -WorkingDirectory $ProjectRoot -Hidden `
        -Environment @{ QWEN3_TTS_SESSION_INTERNAL = "1" } `
        -RedirectStandardOutput (Join-Path $ProjectRoot "logs\facade.out.log") `
        -RedirectStandardError (Join-Path $ProjectRoot "logs\facade.err.log")
    $PublicUrl = "http://127.0.0.1:$($ConfigJson.public)"
    $FacadeDeadline = (Get-Date).AddSeconds($WaitSeconds)
    do {
        Start-Sleep -Milliseconds 500
        if ($Facade.HasExited) { throw "Compatibility facade exited before readiness" }
        try { $Health = Invoke-RestMethod -Uri "$PublicUrl/health" -TimeoutSec 3 } catch { }
    } while (-not $Health -and (Get-Date) -lt $FacadeDeadline)
    if (-not $Health) { throw "Compatibility facade did not answer /health within $WaitSeconds seconds" }

    $State = [ordered]@{
        facade = @{ pid=$Facade.Id; start_time=$Facade.StartTime.ToUniversalTime().ToString("o"); executable=$Python }
        runner = @{ pid=$RunnerProcess.Id; start_time=$RunnerProcess.StartTime.ToUniversalTime().ToString("o"); executable=$RunnerProcess.Path }
        runner_launcher = @{ pid=$Runner.Id; start_time=$Runner.StartTime.ToUniversalTime().ToString("o"); executable=$Python }
        engine = @{ pid=$Engine.Id; start_time=$Engine.StartTime.ToUniversalTime().ToString("o"); executable=[string]$QwenState.executable }
        config = [IO.Path]::GetFullPath($ConfigPath)
    }
    $State | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StatePath -Encoding UTF8
    if ($StartupOwnerRegistered) {
        Release-ProjectSessionOwner -OwnerName "backend startup"
        $StartupOwnerRegistered = $false
    }
    Write-Host "Project session supervisor: PID $($SessionSupervisor.Id)"
    Write-Host "Qwen3-TTS facade: $PublicUrl"
    Write-Host "Engine: qwentts.cpp / CUDA0 / $($Health.model_variant) / $($Health.model_file) (PID $($Engine.Id))"
    Write-Host "Default voice: $($Health.default_voice)"
} catch {
    if ($SessionSupervisor) {
        Request-ProjectSessionTeardown -Supervisor $SessionSupervisor -Reason "backend startup failed"
    } else {
        if ($Facade -and -not $Facade.HasExited) { Stop-Process -Id $Facade.Id -ErrorAction SilentlyContinue }
        if ($Engine -and -not $Engine.HasExited) { Stop-Process -Id $Engine.Id -ErrorAction SilentlyContinue }
        if ($RunnerProcess -and -not $RunnerProcess.HasExited) { Stop-Process -Id $RunnerProcess.Id -ErrorAction SilentlyContinue }
        if ($Runner -and -not $Runner.HasExited) { Stop-Process -Id $Runner.Id -ErrorAction SilentlyContinue }
        Remove-Item -LiteralPath $StatePath,$QwenStatePath -Force -ErrorAction SilentlyContinue
    }
    throw
}
