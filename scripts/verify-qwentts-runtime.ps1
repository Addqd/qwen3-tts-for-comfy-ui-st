[CmdletBinding()]
param([string]$Config = "config/config.local.yaml", [switch]$AllModels)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $ProjectRoot "config\qwentts-runtime.json"
$Manifest = Get-Content -Raw -LiteralPath $ManifestPath -Encoding UTF8 | ConvertFrom-Json
$Bin = Join-Path $ProjectRoot "runtime\qwentts\bin"
$Models = Join-Path $ProjectRoot "runtime\qwentts\models"
$Failures = @()
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

foreach ($Property in $Manifest.files.PSObject.Properties) {
    $Path = Join-Path $Bin $Property.Name
    if (-not (Test-Path -LiteralPath $Path)) { $Failures += "missing: $Path"; continue }
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($Actual -ne ([string]$Property.Value).ToLowerInvariant()) { $Failures += "hash mismatch: $Path" }
}
$ModelProperties = if ($AllModels) {
    @($Manifest.models.files.PSObject.Properties)
} else {
    $ConfigPath = if ([IO.Path]::IsPathRooted($Config)) { $Config } else { Join-Path $ProjectRoot $Config }
    $Names = @(& $Python -c "from qwen3_tts_st.config import load_config; import sys; _,talker,codec=load_config(sys.argv[1]).qwentts_model(); print(talker.name); print(codec.name)" $ConfigPath)
    if ($LASTEXITCODE -ne 0 -or $Names.Count -ne 2) { throw "Unable to resolve the selected qwentts model pair." }
    @($Names | ForEach-Object { $Manifest.models.files.PSObject.Properties[$_] })
}
foreach ($Property in $ModelProperties) {
    $Path = Join-Path $Models $Property.Name
    if (-not (Test-Path -LiteralPath $Path)) { $Failures += "missing: $Path"; continue }
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($Actual -ne ([string]$Property.Value).ToLowerInvariant()) { $Failures += "hash mismatch: $Path" }
}
if ($Failures.Count) { throw "qwentts runtime verification failed:`n$($Failures -join "`n")" }

[PSCustomObject]@{
    status = "ok"
    revision = $Manifest.upstream.revision
    files_checked = @($Manifest.files.PSObject.Properties).Count + @($ModelProperties).Count
    prebuilt_url = $Manifest.upstream.prebuilt_base_url
}
