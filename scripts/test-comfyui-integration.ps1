[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ComfyUIPath)
$TargetScript = Join-Path (Split-Path -Parent $PSScriptRoot) "integrations\comfyui\test-install.ps1"
& $TargetScript -ComfyUIPath $ComfyUIPath
