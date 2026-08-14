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
    try {
        $State = Get-Content -Raw -LiteralPath $script:ProjectSessionStatePath -Encoding UTF8 | ConvertFrom-Json
        if ([int]$State.supervisor.pid -ne [int]$Supervisor.Id) {
            Write-Warning "Project session state belongs to another supervisor; the new supervisor was left to fail safely without terminating adopted processes."
            return
        }
        $State | Add-Member -NotePropertyName stop_requested -NotePropertyValue $true -Force
        $State | Add-Member -NotePropertyName stop_reason -NotePropertyValue $Reason -Force
        $Temporary = "$script:ProjectSessionStatePath.$PID.tmp"
        $State | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $Temporary -Encoding UTF8
        Move-Item -LiteralPath $Temporary -Destination $script:ProjectSessionStatePath -Force
        try { Wait-Process -Id $Supervisor.Id -Timeout 20 -ErrorAction SilentlyContinue } catch { }
    } catch {
        Write-Warning "Unable to request controlled project-session teardown: $($_.Exception.Message)"
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

    if (Test-Path -LiteralPath $script:ProjectSessionStatePath) {
        & $Python $SupervisorScript attach --project-root $script:ProjectRoot --request $RequestPath --state $script:ProjectSessionStatePath
        $AttachExit = $LASTEXITCODE
        if ($AttachExit -eq 0) {
            $State = Get-Content -Raw -LiteralPath $script:ProjectSessionStatePath -Encoding UTF8 | ConvertFrom-Json
            return Get-Process -Id ([int]$State.supervisor.pid) -ErrorAction Stop
        }
        Remove-Item -LiteralPath $RequestPath -Force -ErrorAction SilentlyContinue
        if ($AttachExit -ne 3) {
            throw "The existing project session cannot accept new components (exit $AttachExit). Stop it and retry."
        }
        Remove-Item -LiteralPath $script:ProjectSessionStatePath -Force -ErrorAction SilentlyContinue
        $Request | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $RequestPath -Encoding UTF8
    }

    $OutLog = Join-Path $Logs "project-session.out.log"
    $ErrLog = Join-Path $Logs "project-session.err.log"
    $Supervisor = Start-Process -FilePath $Python -ArgumentList @(
        $SupervisorScript, "supervise", "--project-root", $script:ProjectRoot,
        "--request", $RequestPath, "--state", $script:ProjectSessionStatePath
    ) -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -PassThru

    $Deadline = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 100
        if ($Supervisor.HasExited) {
            $Detail = if (Test-Path -LiteralPath $ErrLog) { (Get-Content -Raw -LiteralPath $ErrLog -Encoding UTF8).Trim() } else { "" }
            throw "Project session supervisor failed during startup (exit $($Supervisor.ExitCode)). $Detail"
        }
    } while (-not (Test-Path -LiteralPath $script:ProjectSessionStatePath) -and (Get-Date) -lt $Deadline)
    if (-not (Test-Path -LiteralPath $script:ProjectSessionStatePath)) {
        Request-ProjectSessionTeardown -Supervisor $Supervisor -Reason "session state publication timed out"
        throw "Project session supervisor did not publish its state within 15 seconds."
    }
    $State = Get-Content -Raw -LiteralPath $script:ProjectSessionStatePath -Encoding UTF8 | ConvertFrom-Json
    if ([int]$State.supervisor.pid -ne $Supervisor.Id) {
        Request-ProjectSessionTeardown -Supervisor $Supervisor -Reason "published state identity mismatch"
        throw "Project session supervisor state belongs to another process."
    }
    return $Supervisor
}
