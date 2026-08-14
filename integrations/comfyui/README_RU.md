# Qwen TTS API nodes

Лёгкие HTTP client nodes для production facade на `127.0.0.1:8020`. ComfyUI не загружает Qwen/PyTorch.

Canonical workflow: `example_workflows/voice_profile_from_wav_ru.json`.

Рекомендуется открывать его непосредственно из репозитория. Автосинхронизация применяется только к явно project-managed copy с marker; произвольные workflow в `ComfyUI/user/...` не изменяются.

Nodes: Server, Runtime Settings, Clone Voice, Synthesize, Voice Selector, Models и Health.

Production model фиксирован на Qwen3-TTS 1.7B Base BF16, поэтому model selector отсутствует. Runtime Settings управляет optional Silero Stress и Silero Text Enhancement через существующий backend runtime-settings API; оба preprocessing-компонента CPU-only, независимы и могут работать вместе.

Managed установка:

```powershell
.\scripts\install-comfyui-nodes.ps1
```
