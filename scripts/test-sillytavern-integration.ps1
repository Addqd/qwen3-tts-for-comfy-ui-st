[CmdletBinding()]
param(
    [string]$BackendUrl = "http://127.0.0.1:8020",
    [string]$SillyTavernUrl = "http://127.0.0.1:8000",
    [string]$Voice = "clone:test_ru_dima_neutral",
    [int]$TimeoutSeconds = 900
)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
& $Python (Join-Path $PSScriptRoot "test-sillytavern-integration.py") --backend $BackendUrl --sillytavern $SillyTavernUrl --voice $Voice --timeout $TimeoutSeconds
if ($LASTEXITCODE -ne 0) { throw "SillyTavern integration test failed." }
