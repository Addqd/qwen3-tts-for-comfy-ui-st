$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -c "from qwen3_tts_st.resources import snapshot; import json; print(json.dumps(snapshot().to_dict(), indent=2))"
nvidia-smi --query-gpu=name,memory.total,memory.free,memory.used,utilization.gpu --format=csv,noheader
