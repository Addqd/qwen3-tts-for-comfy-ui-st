[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "training\russian_adaptation\requirements-training.lock.txt"
$LogRoot = Join-Path $ProjectRoot "logs\training"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogPath = Join-Path $LogRoot "russian-adaptation-$Timestamp.log"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project virtual environment is missing: $Python"
}

New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$env:PYTHONUTF8 = "1"
$env:TOKENIZERS_PARALLELISM = "false"
$env:HF_HOME = Join-Path $ProjectRoot "model_cache"
$env:HF_HUB_CACHE = Join-Path $ProjectRoot "model_cache"
$env:HUGGINGFACE_HUB_CACHE = Join-Path $ProjectRoot "model_cache"

function Invoke-LoggedStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $heading = "[{0}] {1}" -f (Get-Date -Format "s"), $Name
    $heading | Tee-Object -FilePath $LogPath -Append
    & $Python @Arguments 2>&1 | Tee-Object -FilePath $LogPath -Append
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE. See $LogPath"
    }
}

Push-Location $ProjectRoot
try {
    Invoke-LoggedStep -Name "Install pinned training-only dependency" -Arguments @(
        "-m", "pip", "install", "--requirement", $Requirements
    )
    Invoke-LoggedStep -Name "Check dependency consistency" -Arguments @("-m", "pip", "check")
    Invoke-LoggedStep -Name "Validate architecture and safety plan" -Arguments @(
        "-m", "training.russian_adaptation.validate", "--require-cuda"
    )
    Invoke-LoggedStep -Name "Prepare deterministic train and held-out manifests" -Arguments @(
        "-m", "training.russian_adaptation.prepare_dataset"
    )
    Invoke-LoggedStep -Name "Prepare official 12Hz audio codes" -Arguments @(
        "-m", "training.russian_adaptation.prepare_codes"
    )
    Invoke-LoggedStep -Name "Train 0.6B Base LoRA for one epoch" -Arguments @(
        "-m", "training.russian_adaptation.train_lora", "--model-key", "0.6b"
    )
    Invoke-LoggedStep -Name "Train 1.7B Base LoRA for one epoch" -Arguments @(
        "-m", "training.russian_adaptation.train_lora", "--model-key", "1.7b"
    )
    "Training completed successfully. Log: $LogPath" | Tee-Object -FilePath $LogPath -Append
}
catch {
    "Training stopped: $($_.Exception.Message)" | Tee-Object -FilePath $LogPath -Append
    Write-Error $_ -ErrorAction Continue
    exit 1
}
finally {
    Pop-Location
}
