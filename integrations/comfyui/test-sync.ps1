[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$InstallScript = Join-Path $PSScriptRoot "install.ps1"
$Source = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "qwen_tts_api_nodes")).Path
$TempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd([char[]]"\/")
$TestRoot = Join-Path $TempBase ("qwen-tts-comfy-sync-{0}" -f [Guid]::NewGuid().ToString("N"))
$FullTestRoot = [System.IO.Path]::GetFullPath($TestRoot)
if (
    -not $FullTestRoot.StartsWith($TempBase + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase) -or
    (Split-Path -Leaf $FullTestRoot) -notlike "qwen-tts-comfy-sync-*"
) {
    throw "Unsafe temporary test path: $FullTestRoot"
}

function New-FakeComfyUI {
    param([string]$Name)
    $Root = Join-Path $FullTestRoot $Name
    New-Item -ItemType Directory -Force -Path (Join-Path $Root "ComfyUI\custom_nodes") | Out-Null
    return $Root
}

function Get-NodeTarget {
    param([string]$Root)
    return Join-Path $Root "ComfyUI\custom_nodes\qwen_tts_api_nodes"
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

try {
    $JunctionRoot = New-FakeComfyUI "junction"
    & $InstallScript -ComfyUIPath $JunctionRoot -Mode Junction -Synchronize -Confirm:$false
    $JunctionTarget = Get-NodeTarget $JunctionRoot
    $Item = Get-Item -LiteralPath $JunctionTarget -Force
    Assert-True ([string]$Item.LinkType -eq "Junction") "Missing install did not create a Junction."
    Assert-True ([System.IO.Path]::GetFullPath([string]@($Item.Target)[0]) -eq $Source) "Junction source is incorrect."

    $JunctionMarker = Join-Path $JunctionRoot "ComfyUI\custom_nodes\.qwen_tts_api_nodes-install.json"
    Remove-Item -LiteralPath $JunctionMarker -Force
    & $InstallScript -ComfyUIPath $JunctionRoot -Mode Junction -Synchronize -Confirm:$false
    Assert-True (Test-Path -LiteralPath $JunctionMarker) "Correct unmanaged Junction was not safely adopted."
    $StaleSource = Join-Path $FullTestRoot "stale-source"
    New-Item -ItemType Directory -Force -Path $StaleSource | Out-Null
    [System.IO.Directory]::Delete($JunctionTarget)
    New-Item -ItemType Junction -Path $JunctionTarget -Target $StaleSource | Out-Null
    & $InstallScript -ComfyUIPath $JunctionRoot -Mode Junction -Synchronize -Confirm:$false
    $Item = Get-Item -LiteralPath $JunctionTarget -Force
    Assert-True ([System.IO.Path]::GetFullPath([string]@($Item.Target)[0]) -eq $Source) "Stale managed Junction was not refreshed."

    $CopyMigrationRoot = New-FakeComfyUI "copy-to-junction"
    & $InstallScript -ComfyUIPath $CopyMigrationRoot -Mode Copy -Synchronize -Confirm:$false
    $CopyMigrationTarget = Get-NodeTarget $CopyMigrationRoot
    & $InstallScript -ComfyUIPath $CopyMigrationRoot -Mode Junction -Synchronize -Confirm:$false
    $MigratedItem = Get-Item -LiteralPath $CopyMigrationTarget -Force
    Assert-True ([string]$MigratedItem.LinkType -eq "Junction") "Managed Copy did not migrate to Junction."
    Assert-True ([System.IO.Path]::GetFullPath([string]@($MigratedItem.Target)[0]) -eq $Source) "Migrated Junction source is incorrect."
    $MigrationMarker = Join-Path $CopyMigrationRoot "ComfyUI\custom_nodes\.qwen_tts_api_nodes-install.json"
    $MigrationMarkerInfo = Get-Content -Raw -LiteralPath $MigrationMarker | ConvertFrom-Json
    Assert-True ([string]$MigrationMarkerInfo.mode -eq "Junction") "Migrated install marker does not record Junction mode."

    $CopyRoot = New-FakeComfyUI "copy-refresh"
    & $InstallScript -ComfyUIPath $CopyRoot -Mode Copy -Synchronize -Confirm:$false
    $CopyTarget = Get-NodeTarget $CopyRoot
    Set-Content -LiteralPath (Join-Path $CopyTarget "nodes.py") -Value "stale managed copy" -Encoding UTF8
    & $InstallScript -ComfyUIPath $CopyRoot -Mode Copy -Synchronize -Confirm:$false
    $CopyItem = Get-Item -LiteralPath $CopyTarget -Force
    Assert-True (-not $CopyItem.LinkType) "Explicit Copy synchronization changed installation mode."
    $SourceHash = (Get-FileHash -LiteralPath (Join-Path $Source "nodes.py") -Algorithm SHA256).Hash
    $CopyHash = (Get-FileHash -LiteralPath (Join-Path $CopyTarget "nodes.py") -Algorithm SHA256).Hash
    Assert-True ($SourceHash -eq $CopyHash) "Stale managed Copy was not refreshed."

    $UnmanagedRoot = New-FakeComfyUI "unmanaged"
    $UnmanagedTarget = Get-NodeTarget $UnmanagedRoot
    New-Item -ItemType Directory -Force -Path $UnmanagedTarget | Out-Null
    $Sentinel = Join-Path $UnmanagedTarget "unmanaged.txt"
    Set-Content -LiteralPath $Sentinel -Value "preserve" -Encoding UTF8
    $Refused = $false
    try {
        & $InstallScript -ComfyUIPath $UnmanagedRoot -Mode Junction -Synchronize -Confirm:$false
    } catch {
        $Refused = $_.Exception.Message -match "Unmanaged qwen_tts_api_nodes"
    }
    Assert-True $Refused "Unmanaged target was not refused."
    Assert-True (Test-Path -LiteralPath $Sentinel) "Unmanaged target was modified."

    Write-Host "ComfyUI node synchronization tests passed."
} finally {
    if (Test-Path -LiteralPath $FullTestRoot) {
        $ResolvedCleanup = [System.IO.Path]::GetFullPath($FullTestRoot)
        if (
            -not $ResolvedCleanup.StartsWith($TempBase + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase) -or
            (Split-Path -Leaf $ResolvedCleanup) -notlike "qwen-tts-comfy-sync-*"
        ) {
            throw "Refusing unsafe cleanup path: $ResolvedCleanup"
        }
        @(Get-ChildItem -LiteralPath $ResolvedCleanup -Recurse -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Attributes -band [System.IO.FileAttributes]::ReparsePoint } |
            Sort-Object { $_.FullName.Length } -Descending) |
            ForEach-Object {
                if ($_.PSIsContainer) { [System.IO.Directory]::Delete($_.FullName) }
                else { Remove-Item -LiteralPath $_.FullName -Force }
            }
        Remove-Item -LiteralPath $ResolvedCleanup -Recurse -Force
    }
}
