[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact="High")]
param(
    [Parameter(Mandatory=$true)][string]$ComfyUIPath,
    [ValidateSet("Junction", "Copy")][string]$Mode = "Junction",
    [switch]$ReplaceExisting
)

$ErrorActionPreference = "Stop"
$IntegrationRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Source = Join-Path $IntegrationRoot "qwen_tts_api_nodes"
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
Write-Host "Source : $Source"
Write-Host "Target : $Target"
Write-Host "Mode   : $Mode"

if ((Test-Path -LiteralPath $Target) -and -not $ReplaceExisting) {
    throw "Target already exists. Inspect it, then rerun with -ReplaceExisting if replacement is intended."
}

if ($PSCmdlet.ShouldProcess($Target, "Install Qwen TTS API custom node ($Mode)")) {
    if (Test-Path -LiteralPath $Target) {
        $BackupRoot = Join-Path (Split-Path -Parent $CustomNodes) ".qwen_tts_api_nodes-backups"
        New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
        $Backup = Join-Path $BackupRoot ("qwen_tts_api_nodes-{0:yyyyMMdd-HHmmss}" -f (Get-Date))
        Move-Item -LiteralPath $Target -Destination $Backup
        Write-Host "Existing node moved to: $Backup"
    }
    if ($Mode -eq "Junction") {
        New-Item -ItemType Junction -Path $Target -Target $Source | Out-Null
    } else {
        Copy-Item -LiteralPath $Source -Destination $Target -Recurse
    }
    $Info = [ordered]@{
        node = "qwen_tts_api_nodes"
        target = $Target
        source = $Source
        mode = $Mode
        installed_at = (Get-Date).ToString("o")
    }
    $Info | ConvertTo-Json | Set-Content -LiteralPath $Marker -Encoding UTF8
    Write-Host "Installed. Restart ComfyUI, then add nodes from category 'Qwen TTS API'."
}
