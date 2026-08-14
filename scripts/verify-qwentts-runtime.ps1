[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $ProjectRoot "config\qwentts-runtime.json"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { throw "Project Python is unavailable: $Python" }
$PreviousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
try {
    $ManifestJson = & $Python -c "import json,sys;from qwen3_tts_st.config import load_config;print(json.dumps(load_config(sys.argv[1]).qwentts_manifest()))" (Join-Path $ProjectRoot "config\config.local.yaml")
    if ($LASTEXITCODE -ne 0) { throw "Pinned qwentts runtime manifest validation failed." }
} finally {
    if ($null -eq $PreviousPythonPath) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
    else { $env:PYTHONPATH = $PreviousPythonPath }
}
$Manifest = $ManifestJson | ConvertFrom-Json
$Bin = Join-Path $ProjectRoot "runtime\qwentts\bin"
$Models = Join-Path $ProjectRoot "runtime\qwentts\models"
$Failures = @()

foreach ($Property in $Manifest.files.PSObject.Properties) {
    $Path = Join-Path $Bin $Property.Name
    if (-not (Test-Path -LiteralPath $Path)) { $Failures += "missing: $Path"; continue }
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($Actual -ne ([string]$Property.Value).ToLowerInvariant()) { $Failures += "hash mismatch: $Path" }
}
$ModelProperties = @($Manifest.models.files.PSObject.Properties)
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
