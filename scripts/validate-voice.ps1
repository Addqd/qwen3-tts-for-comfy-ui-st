[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Wav, [Parameter(Mandatory=$true)][string]$RefText)
$Python = Join-Path (Split-Path -Parent $PSScriptRoot) ".venv\Scripts\python.exe"
& $Python -m qwen3_tts_st.validate_cli $Wav --ref-text $RefText
exit $LASTEXITCODE

