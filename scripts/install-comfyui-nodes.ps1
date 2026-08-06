[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact="High")]
param(
    [Parameter(Mandatory=$true)][string]$ComfyUIPath,
    [ValidateSet("Junction", "Copy")][string]$Mode = "Junction",
    [switch]$ReplaceExisting
)
$TargetScript = Join-Path (Split-Path -Parent $PSScriptRoot) "integrations\comfyui\install.ps1"
& $TargetScript -ComfyUIPath $ComfyUIPath -Mode $Mode -ReplaceExisting:$ReplaceExisting -WhatIf:$WhatIfPreference
