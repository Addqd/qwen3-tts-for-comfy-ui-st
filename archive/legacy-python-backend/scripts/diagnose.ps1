$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
Write-Host "Windows: $([Environment]::OSVersion.VersionString)"
Write-Host "PowerShell: $($PSVersionTable.PSVersion)"
Write-Host "Git: $(git --version 2>&1)"
Write-Host "FFmpeg: $((ffmpeg -version 2>&1 | Select-Object -First 1))"
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.free,memory.used,utilization.gpu --format=csv,noheader
if (Test-Path -LiteralPath $Python) {
    & $Python -c "import json,torch,sys; print(sys.version); print(json.dumps({'torch':torch.__version__,'cuda_available':torch.cuda.is_available(),'cuda':torch.version.cuda,'device':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}))"
} else { Write-Warning ".venv is missing" }
foreach($Port in 8020,8188) { $L=Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue; Write-Host "Port $Port free=$(-not [bool]$L) pid=$($L.OwningProcess -join ',')" }
