[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $ProjectRoot "config\qwentts-runtime.json"
$Manifest = Get-Content -Raw -LiteralPath $ManifestPath -Encoding UTF8 | ConvertFrom-Json
$ModelDir = Join-Path $ProjectRoot "runtime\qwentts\models"
New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null

$Names = @($Manifest.models.files.PSObject.Properties.Name)

foreach ($Name in $Names) {
    $Asset = $Manifest.models.files.PSObject.Properties[$Name]
    if (-not $Asset) { throw "BF16 qwentts model is absent from the pinned manifest: $Name" }
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
