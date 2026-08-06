[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ComfyUIPath)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path -LiteralPath $ComfyUIPath).Path
$PortablePython = Join-Path $Root "python_embeded\python.exe"
if (Test-Path -LiteralPath $PortablePython) {
    $Python = $PortablePython
} elseif (Test-Path -LiteralPath (Join-Path $Root ".venv\Scripts\python.exe")) {
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
} else {
    throw "ComfyUI Python was not detected. For manual installs, pass a root containing .venv."
}
$Node = if (Test-Path -LiteralPath (Join-Path $Root "ComfyUI\custom_nodes\qwen_tts_api_nodes")) {
    Join-Path $Root "ComfyUI\custom_nodes\qwen_tts_api_nodes"
} else { Join-Path $Root "custom_nodes\qwen_tts_api_nodes" }
if (-not (Test-Path -LiteralPath $Node)) { throw "Installed node was not found: $Node" }
$Code = "import importlib.util, sys; p=r'$Node\__init__.py'; s=importlib.util.spec_from_file_location('qwen_tts_api_nodes',p,submodule_search_locations=[r'$Node']); m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); print(sorted(m.NODE_CLASS_MAPPINGS))"
& $Python -c $Code
if ($LASTEXITCODE -ne 0) { throw "ComfyUI node import failed." }
Write-Host "Import passed. A full workflow execution still requires a running ComfyUI and backend."
