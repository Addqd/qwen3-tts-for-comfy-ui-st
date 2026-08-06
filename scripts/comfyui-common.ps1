$script:ProjectRoot = Split-Path -Parent $PSScriptRoot
$script:ComfyUIStatePath = Join-Path $script:ProjectRoot "runtime\comfyui.json"

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
    'install_path': str(c.get('comfyui.install_path', '')),
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
