$script:ProjectRoot = Split-Path -Parent $PSScriptRoot
$script:ComfyUIStatePath = Join-Path $script:ProjectRoot "runtime\comfyui.json"
$script:QwenTTSRequiredSchemas = [ordered]@{
    QwenTTSServer = @("endpoint", "timeout", "response_format")
    QwenTTSRuntimeSettings = @("server", "apply_and_save", "language", "russian_normalization", "seed", "max_new_tokens", "temperature", "top_k", "top_p", "repetition_penalty")
    QwenTTSCloneVoice = @("server", "reference_audio", "ref_text", "profile_name", "character_name", "language", "overwrite")
    QwenTTSSynthesize = @("server", "text", "voice", "speed", "response_format", "russian_normalization")
}
$script:QwenTTSRemovedInputs = @("active_model", "generation_preset", "multilingual_mode", "chunking_mode", "style", "clone_mode")

function Get-ComfyUISettings {
    param([string]$Config = "config/config.local.yaml")
    $Python = Join-Path $script:ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $Python)) { throw ".venv was not found: $Python" }
    $ConfigPath = if ([System.IO.Path]::IsPathRooted($Config)) { $Config } else { Join-Path $script:ProjectRoot $Config }
    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "Configuration was not found: $ConfigPath" }
    $Code = @"
from qwen3_tts_st.config import load_config
import json, sys
c = load_config(sys.argv[1])
print(json.dumps({
    'enabled': bool(c.get('comfyui.enabled', True)),
    'install_path': str(c.path('comfyui.install_path', 'ComfyUI_windows_portable')),
    'host': str(c.get('comfyui.host', '127.0.0.1')),
    'port': int(c.get('comfyui.port', 8188)),
    'manager_enabled': bool(c.get('comfyui.manager_enabled', True)),
    'startup_timeout_seconds': int(c.get('comfyui.startup_timeout_seconds', 180)),
    'log_path': str(c.path('comfyui.log_path', 'logs/comfyui.log')),
    'backend_port': int(c.get('server.port', 8020)),
}))
"@
    $Raw = & $Python -c $Code $ConfigPath
    if ($LASTEXITCODE -ne 0) { throw "Unable to read ComfyUI configuration." }
    $Settings = $Raw | ConvertFrom-Json
    $Settings | Add-Member -NotePropertyName config_path -NotePropertyValue ([System.IO.Path]::GetFullPath($ConfigPath))
    return $Settings
}

function Test-ComfyUIOwnedProcess {
    param($State)
    $Process = Get-Process -Id ([int]$State.pid) -ErrorAction SilentlyContinue
    if (-not $Process) { return $null }
    try {
        $ExpectedPath = [System.IO.Path]::GetFullPath([string]$State.executable)
        $ActualPath = [System.IO.Path]::GetFullPath([string]$Process.Path)
        $StartMatches = ([int64]$State.start_ticks -eq $Process.StartTime.ToUniversalTime().Ticks)
        if ($StartMatches -and $ActualPath.Equals($ExpectedPath, [System.StringComparison]::OrdinalIgnoreCase)) { return $Process }
    } catch { }
    return $null
}

function Test-LocalHttp {
    param([string]$Uri, [int]$TimeoutSeconds = 3)
    try { Invoke-RestMethod -Uri $Uri -TimeoutSec $TimeoutSeconds | Out-Null; return $true } catch { return $false }
}

function Test-LocalPortInUse {
    param([int]$Port)
    return [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners().Port -contains $Port
}

function Sync-QwenTTSManagedWorkflow {
    param($Settings)

    $Source = (Resolve-Path -LiteralPath (Join-Path $script:ProjectRoot "integrations\comfyui\example_workflows\voice_profile_from_wav_ru.json")).Path
    $ComfyRoot = (Resolve-Path -LiteralPath ([string]$Settings.install_path)).Path
    $WorkflowRoot = Join-Path $ComfyRoot "ComfyUI\user\default\workflows"
    $Target = Join-Path $WorkflowRoot "voice_profile_from_wav_ru.json"
    $Marker = Join-Path $WorkflowRoot ".qwen_tts_voice_profile_workflow.json"

    if (-not (Test-Path -LiteralPath $Marker)) {
        if (Test-Path -LiteralPath $Target) {
            Write-Warning "An unmarked user workflow exists at $Target and was not overwritten. Open the canonical repository JSON directly."
        } else {
            Write-Host "Canonical Qwen TTS workflow is opened directly from: $Source"
        }
        return $false
    }

    try { $Info = Get-Content -Raw -LiteralPath $Marker -Encoding UTF8 | ConvertFrom-Json } catch {
        throw "Managed workflow marker is unreadable; no workflow was changed."
    }
    $ExpectedTarget = [IO.Path]::GetFullPath($Target)
    if ([string]::IsNullOrWhiteSpace([string]$Info.target)) {
        throw "Managed workflow marker failed its safety checks; no workflow was changed."
    }
    $MarkedTarget = [IO.Path]::GetFullPath([string]$Info.target)
    if ([string]$Info.workflow -ne "voice_profile_from_wav_ru.json" -or
        -not $MarkedTarget.Equals($ExpectedTarget, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Managed workflow marker failed its safety checks; no workflow was changed."
    }
    New-Item -ItemType Directory -Force -Path $WorkflowRoot | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Target -Force
    Write-Host "Managed canonical Qwen TTS workflow synchronized: $Target"
    return $true
}

function Assert-QwenTTSCloneVoiceSchema {
    param([string]$Url = "", $Objects = $null)

    if ($null -eq $Objects) {
        try {
            $Objects = Invoke-RestMethod -Uri "$Url/object_info" -TimeoutSec 15
        } catch {
            throw "Unable to validate the running QwenTTSCloneVoice schema at $Url. ComfyUI must not be used until object_info is available."
        }
    }
    foreach ($Entry in $script:QwenTTSRequiredSchemas.GetEnumerator()) {
        $NodeProperty = $Objects.PSObject.Properties[$Entry.Key]
        if (-not $NodeProperty) { throw "Running ComfyUI does not expose $($Entry.Key). Restart through the project launcher." }
        $Required = $NodeProperty.Value.input.required
        $Optional = $NodeProperty.Value.input.optional
        $Available = @()
        if ($Required) { $Available += @($Required.PSObject.Properties.Name) }
        if ($Optional) { $Available += @($Optional.PSObject.Properties.Name) }
        $Missing = @($Entry.Value | Where-Object { $Available -notcontains $_ })
        if ($Missing.Count) { throw "Running ComfyUI has a stale $($Entry.Key) schema; missing: $($Missing -join ', ')." }
        $Removed = @($script:QwenTTSRemovedInputs | Where-Object { $Available -contains $_ })
        if ($Removed.Count) { throw "Running ComfyUI has obsolete $($Entry.Key) inputs: $($Removed -join ', ')." }
    }
    Write-Host "Qwen TTS ComfyUI runtime schema: current"
}

function Get-LocalhostTcpListenerProcess {
    param([int]$Port)

    $ListenerPids = @()
    try {
        $ListenerPids = @(
            Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
                Where-Object { $_.LocalAddress -eq "127.0.0.1" } |
                ForEach-Object { [int]$_.OwningProcess }
        )
    } catch {
        $Pattern = "^\s*TCP\s+127\.0\.0\.1:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
        $Netstat = Join-Path $env:SystemRoot "System32\netstat.exe"
        if (Test-Path -LiteralPath $Netstat) {
            $ListenerPids = @(
                & $Netstat -ano -p tcp |
                    ForEach-Object { if ($_ -match $Pattern) { [int]$Matches[1] } }
            )
        }
    }

    $ListenerPids = @($ListenerPids | Sort-Object -Unique)
    if ($ListenerPids.Count -ne 1) { return $null }
    return Get-Process -Id $ListenerPids[0] -ErrorAction SilentlyContinue
}

function Get-ComfyUIListenerOwnedProcess {
    param($Settings)

    $Process = Get-LocalhostTcpListenerProcess -Port ([int]$Settings.port)
    if (-not $Process) { return $null }
    try {
        $Root = (Resolve-Path -LiteralPath ([string]$Settings.install_path)).Path
        $ExpectedPath = [System.IO.Path]::GetFullPath((Join-Path $Root "python_embeded\python.exe"))
        $ActualPath = [System.IO.Path]::GetFullPath([string]$Process.Path)
        if ($ActualPath.Equals($ExpectedPath, [System.StringComparison]::OrdinalIgnoreCase)) { return $Process }
    } catch { }
    return $null
}
