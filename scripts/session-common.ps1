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
    param(
        [Parameter(Mandatory = $true)][string]$ComponentName,
        [int]$ProcessId = $PID
    )
    if (-not (Test-Path -LiteralPath $script:ProjectSessionStatePath)) { return }
    $Python = Join-Path $script:ProjectRoot ".venv\Scripts\python.exe"
    $SupervisorScript = Join-Path $PSScriptRoot "project-session.py"
    $Runtime = Split-Path -Parent $script:ProjectSessionStatePath
    $RequestPath = Join-Path $Runtime ("project-session-release-component-{0}-{1}.json" -f $PID,[Guid]::NewGuid().ToString("N"))
    @{ components = @(@{ name = $ComponentName; pid = $ProcessId }) } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $RequestPath -Encoding UTF8
    try {
        & $Python $SupervisorScript release-component --project-root $script:ProjectRoot --request $RequestPath --state $script:ProjectSessionStatePath
        if ($LASTEXITCODE -notin @(0,3)) { throw "Unable to release the project-session startup component (exit $LASTEXITCODE)." }
    } finally {
        Remove-Item -LiteralPath $RequestPath -Force -ErrorAction SilentlyContinue
    }
}

function Start-ManagedProjectProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [hashtable]$Environment = @{},
        [switch]$Hidden,
        [string]$RedirectStandardOutput = "",
        [string]$RedirectStandardError = ""
    )
    $Python = Join-Path $script:ProjectRoot ".venv\Scripts\python.exe"
    $HelperScript = Join-Path $PSScriptRoot "project-process.py"
    $Runtime = Join-Path $script:ProjectRoot "runtime"
    $Token = "{0}-{1}" -f $PID,[Guid]::NewGuid().ToString("N")
    $RequestPath = Join-Path $Runtime "project-process-$Token.json"
    $GoPath = Join-Path $Runtime "project-process-$Token.go"
    $ResultPath = Join-Path $Runtime "project-process-$Token.result.json"
    $ReleasePath = Join-Path $Runtime "project-process-$Token.release"
    $DonePath = Join-Path $Runtime "project-process-$Token.done"
    $BootstrapName = "$Name bootstrap $Token"
    $Bootstrap = $null
    $Process = $null
    [ordered]@{
        file_path = [IO.Path]::GetFullPath($FilePath)
        arguments = @($ArgumentList)
        working_directory = [IO.Path]::GetFullPath($WorkingDirectory)
        environment = $Environment
        hidden = [bool]$Hidden
        stdout = $RedirectStandardOutput
        stderr = $RedirectStandardError
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $RequestPath -Encoding UTF8
    try {
        $Bootstrap = Start-Process -FilePath $Python -ArgumentList @($HelperScript,"--request",$RequestPath,"--go",$GoPath,"--result",$ResultPath,"--release",$ReleasePath,"--done",$DonePath,"--state",$script:ProjectSessionStatePath) -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden -PassThru
        $null = Start-OrJoin-ProjectSession -Components @(@{ name = $BootstrapName; pid = $Bootstrap.Id })
        New-Item -ItemType File -Path $GoPath -Force | Out-Null
        $Deadline = (Get-Date).AddSeconds(30)
        while (-not (Test-Path -LiteralPath $ResultPath) -and (Get-Date) -lt $Deadline) {
            if ($Bootstrap.HasExited) { throw "$Name bootstrap exited before creating the process." }
            Start-Sleep -Milliseconds 50
        }
        if (-not (Test-Path -LiteralPath $ResultPath)) { throw "$Name bootstrap timed out." }
        $Result = Get-Content -Raw -LiteralPath $ResultPath -Encoding UTF8 | ConvertFrom-Json
        $Process = Get-Process -Id ([int]$Result.pid) -ErrorAction Stop
        $null = Start-OrJoin-ProjectSession -Components @(@{ name = $Name; pid = $Process.Id })
        New-Item -ItemType File -Path $ReleasePath -Force | Out-Null
        $Deadline = (Get-Date).AddSeconds(10)
        do {
            if ($Bootstrap.HasExited) { throw "$Name bootstrap exited before resuming the process." }
            $Result = Get-Content -Raw -LiteralPath $ResultPath -Encoding UTF8 | ConvertFrom-Json
            if (-not $Result.resumed) { Start-Sleep -Milliseconds 50 }
        } while (-not $Result.resumed -and (Get-Date) -lt $Deadline)
        if (-not $Result.resumed) { throw "$Name bootstrap did not confirm process resume." }
        Release-ProjectSessionComponent -ComponentName $BootstrapName -ProcessId $Bootstrap.Id
        New-Item -ItemType File -Path $DonePath -Force | Out-Null
        try { Wait-Process -Id $Bootstrap.Id -Timeout 10 -ErrorAction SilentlyContinue } catch { }
        return $Process
    } catch {
        if ($Process -and -not $Process.HasExited) { Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue }
        if ($Bootstrap -and -not $Bootstrap.HasExited) { Stop-Process -Id $Bootstrap.Id -ErrorAction SilentlyContinue }
        throw
    } finally {
        Remove-Item -LiteralPath $RequestPath,$GoPath,$ResultPath,$ReleasePath,$DonePath -Force -ErrorAction SilentlyContinue
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
