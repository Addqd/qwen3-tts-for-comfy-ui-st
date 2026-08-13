# Qwen TTS API nodes

Лёгкие HTTP client nodes для production facade на `127.0.0.1:8020`. ComfyUI не загружает Qwen/PyTorch.

Canonical workflow: `example_workflows/voice_profile_from_wav_ru.json`.

Рекомендуется открывать его непосредственно из репозитория. Автосинхронизация применяется только к явно project-managed copy с marker; произвольные workflow в `ComfyUI/user/...` не изменяются.

Nodes: Server, Runtime Settings, Clone Voice, Synthesize, Voice Selector, Models и Health.

Managed установка:

```powershell
.\scripts\install-comfyui-nodes.ps1
```
