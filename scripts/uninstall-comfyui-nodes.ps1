[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact="High")]
param([Parameter(Mandatory=$true)][string]$ComfyUIPath)
$TargetScript = Join-Path (Split-Path -Parent $PSScriptRoot) "integrations\comfyui\uninstall.ps1"
& $TargetScript -ComfyUIPath $ComfyUIPath -WhatIf:$WhatIfPreference
