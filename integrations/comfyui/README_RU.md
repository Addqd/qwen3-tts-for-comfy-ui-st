# Qwen TTS API nodes

Лёгкие HTTP client nodes для production facade на `127.0.0.1:8020`. ComfyUI не загружает Qwen/PyTorch.

Canonical workflow: `example_workflows/voice_profile_from_wav_ru.json`.

Nodes: Server, Runtime Settings, Clone Voice, Synthesize, Voice Selector, Models и Health.

Managed установка:

```powershell
.\scripts\install-comfyui-nodes.ps1
```
