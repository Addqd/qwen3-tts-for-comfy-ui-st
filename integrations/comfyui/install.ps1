[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact="High")]
param(
    [Parameter(Mandatory=$true)][string]$ComfyUIPath,
    [ValidateSet("Junction", "Copy")][string]$Mode = "Junction",
    [switch]$ReplaceExisting,
    [switch]$Synchronize
)

$ErrorActionPreference = "Stop"
$IntegrationRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Source = (Resolve-Path -LiteralPath (Join-Path $IntegrationRoot "qwen_tts_api_nodes")).Path
$ResolvedComfy = (Resolve-Path -LiteralPath $ComfyUIPath).Path

if (Test-Path -LiteralPath (Join-Path $ResolvedComfy "ComfyUI\custom_nodes")) {
    $CustomNodes = Join-Path $ResolvedComfy "ComfyUI\custom_nodes"
} elseif (Test-Path -LiteralPath (Join-Path $ResolvedComfy "custom_nodes")) {
    $CustomNodes = Join-Path $ResolvedComfy "custom_nodes"
} else {
    throw "ComfyUI custom_nodes was not found under: $ResolvedComfy"
}

$Target = Join-Path $CustomNodes "qwen_tts_api_nodes"
$Marker = Join-Path $CustomNodes ".qwen_tts_api_nodes-install.json"
$ScriptCmdlet = $PSCmdlet

function Get-NormalizedPath {
    param([string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]"\/")
}

function Test-SamePath {
    param([string]$Left, [string]$Right)
    return (Get-NormalizedPath $Left).Equals(
        (Get-NormalizedPath $Right),
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Get-JunctionSource {
    param([string]$Path)
    $Item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (-not $Item -or [string]$Item.LinkType -ne "Junction") { return $null }
    $RawTarget = @($Item.Target)[0]
    if (-not $RawTarget) { return $null }
    if (-not [System.IO.Path]::IsPathRooted([string]$RawTarget)) {
        $RawTarget = Join-Path $Item.Parent.FullName ([string]$RawTarget)
    }
    return Get-NormalizedPath ([string]$RawTarget)
}

function Get-ComparableFileMap {
    param([string]$Root)
    $ResolvedRoot = Get-NormalizedPath $Root
    $Map = @{}
    foreach ($File in Get-ChildItem -LiteralPath $ResolvedRoot -File -Recurse) {
        if ($File.FullName -match "[\\/]__pycache__[\\/]" -or $File.Extension -in @(".pyc", ".pyo")) { continue }
        $Relative = $File.FullName.Substring($ResolvedRoot.Length).TrimStart([char[]]"\/").ToLowerInvariant()
        $Map[$Relative] = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash
    }
    return $Map
}

function Test-ManagedCopyCurrent {
    param([string]$SourcePath, [string]$TargetPath)
    $SourceFiles = Get-ComparableFileMap $SourcePath
    $TargetFiles = Get-ComparableFileMap $TargetPath
    if ($SourceFiles.Count -ne $TargetFiles.Count) { return $false }
    foreach ($Relative in $SourceFiles.Keys) {
        if (-not $TargetFiles.ContainsKey($Relative) -or $TargetFiles[$Relative] -ne $SourceFiles[$Relative]) {
            return $false
        }
    }
    return $true
}

function Write-InstallMarker {
    param([string]$InstallMode)
    $Info = [ordered]@{
        node = "qwen_tts_api_nodes"
        target = $Target
        source = $Source
        mode = $InstallMode
        installed_at = (Get-Date).ToString("o")
    }
    $Info | ConvertTo-Json | Set-Content -LiteralPath $Marker -Encoding UTF8
}

function Install-ManagedNode {
    param([string]$InstallMode, [string]$Status)
    if ($ScriptCmdlet.ShouldProcess($Target, "Install Qwen TTS API custom node ($InstallMode)")) {
        if (Get-Item -LiteralPath $Target -Force -ErrorAction SilentlyContinue) {
            $BackupRoot = Join-Path (Split-Path -Parent $CustomNodes) ".qwen_tts_api_nodes-backups"
            New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
            $Backup = Join-Path $BackupRoot ("qwen_tts_api_nodes-{0:yyyyMMdd-HHmmss-fff}" -f (Get-Date))
            Move-Item -LiteralPath $Target -Destination $Backup
            Write-Host "Existing node moved to: $Backup"
        }
        if ($InstallMode -eq "Junction") {
            New-Item -ItemType Junction -Path $Target -Target $Source | Out-Null
        } else {
            Copy-Item -LiteralPath $Source -Destination $Target -Recurse
        }
        Write-InstallMarker -InstallMode $InstallMode
        Write-Host "Qwen TTS ComfyUI nodes: $Status ($InstallMode)"
    }
}

Write-Host "Source : $Source"
Write-Host "Target : $Target"

$Managed = $false
$MarkerInfo = $null
if (Test-Path -LiteralPath $Marker) {
    try { $MarkerInfo = Get-Content -Raw -LiteralPath $Marker | ConvertFrom-Json } catch {
        throw "Qwen TTS install marker is unreadable; refusing to replace the installed custom node."
    }
    if (
        [string]$MarkerInfo.node -ne "qwen_tts_api_nodes" -or
        [string]$MarkerInfo.mode -notin @("Junction", "Copy") -or
        -not (Test-SamePath ([string]$MarkerInfo.target) $Target)
    ) {
        throw "Qwen TTS install marker failed its safety checks; refusing to replace the installed custom node."
    }
    $Managed = $true
}

$TargetItem = Get-Item -LiteralPath $Target -Force -ErrorAction SilentlyContinue
$JunctionSource = if ($TargetItem) { Get-JunctionSource $Target } else { $null }
$CurrentJunction = $JunctionSource -and (Test-SamePath $JunctionSource $Source)

if ($Synchronize) {
    if ($CurrentJunction) {
        if (-not $Managed -or [string]$MarkerInfo.mode -ne "Junction") {
            if ($ScriptCmdlet.ShouldProcess($Marker, "Record managed Qwen TTS Junction")) {
                Write-InstallMarker -InstallMode "Junction"
            }
        }
        Write-Host "Qwen TTS ComfyUI nodes: current (Junction)"
        return
    }

    if ($TargetItem -and -not $Managed) {
        throw "Unmanaged qwen_tts_api_nodes exists at $Target. It was not changed. Inspect or move it, then run scripts/install-comfyui-nodes.ps1 explicitly."
    }

    if ($Managed -and $TargetItem -and [string]$MarkerInfo.mode -eq "Copy" -and -not $JunctionSource) {
        if (Test-ManagedCopyCurrent -SourcePath $Source -TargetPath $Target) {
            Write-Host "Qwen TTS ComfyUI nodes: current (Copy)"
            return
        }
    }

    $InstallMode = if ($Managed) { [string]$MarkerInfo.mode } else { $Mode }
    $Status = if ($TargetItem) { "refreshed from repository source" } else { "installed from repository source" }
    Install-ManagedNode -InstallMode $InstallMode -Status $Status
    return
}

Write-Host "Mode   : $Mode"
if ($TargetItem -and -not $ReplaceExisting) {
    throw "Target already exists. Inspect it, then rerun with -ReplaceExisting if replacement is intended."
}
Install-ManagedNode -InstallMode $Mode -Status "installed"
if (-not $WhatIfPreference) {
    Write-Host "Restart ComfyUI, then add nodes from category 'Qwen TTS API'."
}
