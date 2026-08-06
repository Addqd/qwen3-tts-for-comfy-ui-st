$ProjectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $ProjectRoot "start.ps1") -Config "config/config.cuda-on-demand.yaml" @args

