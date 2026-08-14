$script:ProjectSessionStatePath = Join-Path $script:ProjectRoot "runtime\project-session.json"

function Test-ProjectSessionComponent {
    param([Parameter(Mandatory = $true)][string[]]$Names)
    if (-not (Test-Path -LiteralPath $script:ProjectSessionStatePath)) { return $false }
    try {
        $State = Get-Content -Raw -LiteralPath $script:ProjectSessionStatePath -Encoding UTF8 | ConvertFrom-Json
        return (@($State.components | Where-Object { $Names -contains [string]$_.name }).Count -gt 0)
    } catch {
        return $false
    }
}

function Wait-ProjectSessionTeardown {
    param([int]$Seconds = 20)
    $Deadline = (Get-Date).AddSeconds($Seconds)
    while ((Test-Path -LiteralPath $script:ProjectSessionStatePath) -and (Get-Date) -lt $Deadline) {
        Start-Sleep -Milliseconds 200
    }
    if (Test-Path -LiteralPath $script:ProjectSessionStatePath) {
        Write-Warning "Project session supervisor did not complete teardown within $Seconds seconds. The component stop itself succeeded."
    }
}

function Request-ProjectSessionTeardown {
    param([Parameter(Mandatory = $true)]$Supervisor, [string]$Reason)
    if (-not (Test-Path -LiteralPath $script:ProjectSessionStatePath)) {
        $PreviousCleanup = $env:QWEN3_TTS_SUPERVISOR_CLEANUP
        $env:QWEN3_TTS_SUPERVISOR_CLEANUP = "1"
        try {
            try { & (Join-Path $script:ProjectRoot "scripts\stop-comfyui.ps1") } catch { Write-Warning $_.Exception.Message }
            try { & (Join-Path $script:ProjectRoot "stop.ps1") } catch { Write-Warning $_.Exception.Message }
        } finally {
            if ($null -eq $PreviousCleanup) { Remove-Item Env:QWEN3_TTS_SUPERVISOR_CLEANUP -ErrorAction SilentlyContinue }
            else { $env:QWEN3_TTS_SUPERVISOR_CLEANUP = $PreviousCleanup }
        }
        try { Wait-Process -Id $Supervisor.Id -Timeout 20 -ErrorAction SilentlyContinue } catch { }
        if (Get-Process -Id $Supervisor.Id -ErrorAction SilentlyContinue) {
            Write-Warning "Project session state was not published; graceful component cleanup was attempted and the supervisor was left to fail safely."
        }
        return
    }
    $Python = Join-Path $script:ProjectRoot ".venv\Scripts\python.exe"
    $SupervisorScript = Join-Path $PSScriptRoot "project-session.py"
    $RequestPath = Join-Path (Split-Path -Parent $script:ProjectSessionStatePath) ("project-session-stop-{0}-{1}.json" -f $PID,[Guid]::NewGuid().ToString("N"))
    @{ supervisor_pid = [int]$Supervisor.Id; reason = $Reason } | ConvertTo-Json | Set-Content -LiteralPath $RequestPath -Encoding UTF8
    try {
        & $Python $SupervisorScript request-stop --project-root $script:ProjectRoot --request $RequestPath --state $script:ProjectSessionStatePath
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Unable to request controlled project-session teardown (exit $LASTEXITCODE)."
            return
        }
        try { Wait-Process -Id $Supervisor.Id -Timeout 20 -ErrorAction SilentlyContinue } catch { }
    } finally {
        Remove-Item -LiteralPath $RequestPath -Force -ErrorAction SilentlyContinue
    }
}

function Release-ProjectSessionOwner {
    param([string]$OwnerName = "launcher")
    if (-not (Test-Path -LiteralPath $script:ProjectSessionStatePath)) { return }
    $Python = Join-Path $script:ProjectRoot ".venv\Scripts\python.exe"
    $SupervisorScript = Join-Path $PSScriptRoot "project-session.py"
    $Runtime = Split-Path -Parent $script:ProjectSessionStatePath
    $RequestPath = Join-Path $Runtime ("project-session-release-{0}-{1}.json" -f $PID,[Guid]::NewGuid().ToString("N"))
    @{ owners = @(@{ name = $OwnerName; pid = $PID }) } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $RequestPath -Encoding UTF8
    try {
        & $Python $SupervisorScript release-owner --project-root $script:ProjectRoot --request $RequestPath --state $script:ProjectSessionStatePath
        if ($LASTEXITCODE -notin @(0,3)) { throw "Unable to release the project-session startup owner (exit $LASTEXITCODE)." }
    } finally {
        Remove-Item -LiteralPath $RequestPath -Force -ErrorAction SilentlyContinue
    }
}

function Release-ProjectSessionComponent {
    param([Parameter(Mandatory = $true)][string]$ComponentName)
    if (-not (Test-Path -LiteralPath $script:ProjectSessionStatePath)) { return }
    $Python = Join-Path $script:ProjectRoot ".venv\Scripts\python.exe"
    $SupervisorScript = Join-Path $PSScriptRoot "project-session.py"
    $Runtime = Split-Path -Parent $script:ProjectSessionStatePath
    $RequestPath = Join-Path $Runtime ("project-session-release-component-{0}-{1}.json" -f $PID,[Guid]::NewGuid().ToString("N"))
    @{ components = @(@{ name = $ComponentName; pid = $PID }) } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $RequestPath -Encoding UTF8
    try {
        & $Python $SupervisorScript release-component --project-root $script:ProjectRoot --request $RequestPath --state $script:ProjectSessionStatePath
        if ($LASTEXITCODE -notin @(0,3)) { throw "Unable to release the project-session startup component (exit $LASTEXITCODE)." }
    } finally {
        Remove-Item -LiteralPath $RequestPath -Force -ErrorAction SilentlyContinue
    }
}

function Start-OrJoin-ProjectSession {
    param(
        [Parameter(Mandatory = $true)][array]$Components,
        [string]$OwnerName = "launcher",
        [switch]$MonitorOwner
    )

    $Python = Join-Path $script:ProjectRoot ".venv\Scripts\python.exe"
    $SupervisorScript = Join-Path $PSScriptRoot "project-session.py"
    if (-not (Test-Path -LiteralPath $Python)) { throw ".venv was not found: $Python" }
    $Runtime = Join-Path $script:ProjectRoot "runtime"
    $Logs = Join-Path $script:ProjectRoot "logs"
    New-Item -ItemType Directory -Force -Path $Runtime,$Logs | Out-Null
    $RequestPath = Join-Path $Runtime ("project-session-request-{0}-{1}.json" -f $PID,[Guid]::NewGuid().ToString("N"))
    $Request = [ordered]@{
        owners = if ($MonitorOwner) { @(@{ name = $OwnerName; pid = $PID }) } else { @() }
        components = @($Components | ForEach-Object { @{ name = [string]$_.name; pid = [int]$_.pid } })
    }
    $Request | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $RequestPath -Encoding UTF8

    try {
        & $Python $SupervisorScript ensure --project-root $script:ProjectRoot --request $RequestPath --state $script:ProjectSessionStatePath
        $EnsureExit = $LASTEXITCODE
        if ($EnsureExit -ne 0) {
            if ($EnsureExit -eq 7) { throw "A managed project session must be created before starting the first project component." }
            throw "The project session cannot accept the requested lifecycle mutation (exit $EnsureExit)."
        }
        $Session = Get-Content -Raw -LiteralPath $script:ProjectSessionStatePath -Encoding UTF8 | ConvertFrom-Json
        return Get-Process -Id ([int]$Session.supervisor.pid) -ErrorAction Stop
    } finally {
        Remove-Item -LiteralPath $RequestPath -Force -ErrorAction SilentlyContinue
    }
}
