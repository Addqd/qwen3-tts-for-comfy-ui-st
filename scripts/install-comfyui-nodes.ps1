[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact="High")]
param(
    [Parameter(Mandatory=$true)][string]$ComfyUIPath,
    [ValidateSet("Junction", "Copy")][string]$Mode = "Junction",
    [switch]$ReplaceExisting
)
$ErrorActionPreference = "Stop"
$TargetScript = Join-Path (Split-Path -Parent $PSScriptRoot) "integrations\comfyui\install.ps1"
$Arguments = @{
    ComfyUIPath = $ComfyUIPath
    Mode = $Mode
    ReplaceExisting = $ReplaceExisting
    Confirm = $false
}
if ($PSBoundParameters.ContainsKey("WhatIf")) {
    $Arguments.WhatIf = $true
}
& $TargetScript @Arguments
