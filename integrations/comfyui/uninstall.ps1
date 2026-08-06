[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact="High")]
param([Parameter(Mandatory=$true)][string]$ComfyUIPath)

$ErrorActionPreference = "Stop"
$ResolvedComfy = (Resolve-Path -LiteralPath $ComfyUIPath).Path
if (Test-Path -LiteralPath (Join-Path $ResolvedComfy "ComfyUI\custom_nodes")) {
    $CustomNodes = Join-Path $ResolvedComfy "ComfyUI\custom_nodes"
} elseif (Test-Path -LiteralPath (Join-Path $ResolvedComfy "custom_nodes")) {
    $CustomNodes = Join-Path $ResolvedComfy "custom_nodes"
} else {
    throw "ComfyUI custom_nodes was not found under: $ResolvedComfy"
}
$Marker = Join-Path $CustomNodes ".qwen_tts_api_nodes-install.json"
if (-not (Test-Path -LiteralPath $Marker)) { throw "Installation marker is absent; refusing an unverified recursive removal." }
$Info = Get-Content -LiteralPath $Marker -Raw | ConvertFrom-Json
$Target = [System.IO.Path]::GetFullPath([string]$Info.target)
$Expected = [System.IO.Path]::GetFullPath((Join-Path $CustomNodes "qwen_tts_api_nodes"))
if ($Target -ne $Expected -or -not $Target.StartsWith([System.IO.Path]::GetFullPath($CustomNodes), [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Marker target failed the safety check: $Target"
}
if ($PSCmdlet.ShouldProcess($Target, "Remove installed Qwen TTS API node")) {
    if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }
    Remove-Item -LiteralPath $Marker -Force
    Write-Host "Removed installed node. Timestamped backup folders, if any, were preserved."
}
