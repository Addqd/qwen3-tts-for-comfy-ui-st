[CmdletBinding()]
param(
    [string]$Config = "config/config.local.yaml",
    [switch]$AllModels
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ManifestPath = Join-Path $ProjectRoot "config\qwentts-runtime.json"
$Manifest = Get-Content -Raw -LiteralPath $ManifestPath -Encoding UTF8 | ConvertFrom-Json
$ModelDir = Join-Path $ProjectRoot "runtime\qwentts\models"
New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null

if ($AllModels) {
    $Names = @($Manifest.models.files.PSObject.Properties.Name)
} else {
    if (-not (Test-Path -LiteralPath $Python)) { throw ".venv was not found. Run .\scripts\install.ps1" }
    $ConfigPath = if ([IO.Path]::IsPathRooted($Config)) { $Config } else { Join-Path $ProjectRoot $Config }
    $Names = @(& $Python -c "from qwen3_tts_st.config import load_config; import sys; _,talker,codec=load_config(sys.argv[1]).qwentts_model(); print(talker.name); print(codec.name)" $ConfigPath)
    if ($LASTEXITCODE -ne 0 -or $Names.Count -ne 2) { throw "Unable to resolve the selected qwentts model pair." }
}

foreach ($Name in $Names) {
    $Asset = $Manifest.models.files.PSObject.Properties[$Name]
    if (-not $Asset) { throw "Selected qwentts model is absent from the pinned manifest: $Name" }
    $Destination = Join-Path $ModelDir $Name
    if (Test-Path -LiteralPath $Destination) {
        $Existing = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
        if ($Existing -eq ([string]$Asset.Value).ToLowerInvariant()) { continue }
    }
    $Temporary = "$Destination.download"
    $Url = ([string]$Manifest.models.download_base_url).TrimEnd('/') + "/" + $Name + "?download=true"
    $Curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $Curl) { throw "curl.exe is required for resumable qwentts model downloads." }
    Write-Host "Downloading pinned qwentts model asset: $Name"
    & $Curl.Source -L --fail --retry 10 --retry-all-errors --retry-delay 2 --progress-bar -C - -o $Temporary $Url
    if ($LASTEXITCODE -ne 0) { throw "Model download failed for $Name (curl exit $LASTEXITCODE); partial download was preserved for resume." }
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Temporary).Hash.ToLowerInvariant()
    if ($Actual -ne ([string]$Asset.Value).ToLowerInvariant()) {
        Remove-Item -LiteralPath $Temporary -Force
        throw "Downloaded model hash mismatch: $Name"
    }
    Move-Item -LiteralPath $Temporary -Destination $Destination -Force
}
