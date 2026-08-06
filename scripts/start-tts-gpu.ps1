$ProjectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $ProjectRoot "start.ps1") -Config "config/config.cuda.yaml" @args

