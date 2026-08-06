[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact="High")]
param([Parameter(Mandatory=$true)][string]$ComfyUIPath)
$ErrorActionPreference = "Stop"
$TargetScript = Join-Path (Split-Path -Parent $PSScriptRoot) "integrations\comfyui\uninstall.ps1"
$Arguments = @{ ComfyUIPath = $ComfyUIPath; Confirm = $false }
if ($PSBoundParameters.ContainsKey("WhatIf")) { $Arguments.WhatIf = $true }
& $TargetScript @Arguments
