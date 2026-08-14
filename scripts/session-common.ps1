$script:ProjectSessionStatePath = Join-Path $script:ProjectRoot "runtime\project-session.json"

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
        Stop-Process -Id $Supervisor.Id -ErrorAction SilentlyContinue
        throw "Project session supervisor did not publish its state within 15 seconds."
    }
    $State = Get-Content -Raw -LiteralPath $script:ProjectSessionStatePath -Encoding UTF8 | ConvertFrom-Json
    if ([int]$State.supervisor.pid -ne $Supervisor.Id) {
        Stop-Process -Id $Supervisor.Id -ErrorAction SilentlyContinue
        throw "Project session supervisor state belongs to another process."
    }
    return $Supervisor
}
