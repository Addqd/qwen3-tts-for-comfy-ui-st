# Qwen TTS API nodes для ComfyUI

Версия нод `0.4.0`. Это лёгкие localhost HTTP-клиенты для отдельного backend `http://127.0.0.1:8020`. В пакет не входят `torch`, `transformers`, `qwen_tts` или CUDA-библиотеки; модель и voice library остаются в backend-процессе.

## Ноды

- Server — endpoint, timeout, model, format и состояние соединения.
- Health — `/health` и `/v1/voices`.
- Models — `/v1/models`.
- Voice Selector — список voices и понятное сообщение для отсутствующего ID.
- Emotion Script — quote-aware preview десяти styles, включая `pleasure` и `intimate`: narration всегда neutral, `[voice:style]` действует только на следующую полную ASCII-цитату, затем сбрасывается; service tags не попадают в clean text. Parity tests сравнивают mirror parser с backend source of truth.
- Synthesize — `/v1/audio/speech`, ComfyUI `AUDIO`, temp WAV, metadata и duration.
- Clone Voice — `AUDIO` → mono PCM16 WAV с сохранением исходного sample rate → consent-gated `/v1/audio/voice-clone`.

Установка на проверенную Portable:

```powershell
.\scripts\install-comfyui-nodes.ps1 -ComfyUIPath ".\ComfyUI_windows_portable" -Mode Copy -WhatIf
.\scripts\install-comfyui-nodes.ps1 -ComfyUIPath ".\ComfyUI_windows_portable" -Mode Copy -ReplaceExisting
.\scripts\start-tts-and-comfyui.ps1
.\scripts\test-comfyui-integration.ps1 -SkipSynthesis
```

Без `-SkipSynthesis` тест отправляет настоящий Queue Prompt и получает audio через встроенный `PreviewAudio`. Установщик никогда не заменяет существующую папку без `-ReplaceExisting`; backup хранится вне `custom_nodes` в `ComfyUI\.qwen_tts_api_nodes-backups`. Uninstaller требует marker и удаляет только точную цель.

Workflow-примеры:

- `backend_health_and_voices.json`;
- `emotion_script_preview.json`;
- `text_to_speech_ru.json`;
- `text_to_speech_models_ru.json` — 0.6B Fast, 1.7B Quality и Stable/Full Russian;
- `voice_clone_and_synthesize_ru.json` — не запускать без разрешённого WAV и точной транскрипции;
- `voice_profile_from_wav_ru.json` — регистрация профиля и контрольный AUDIO;
- `emotion_router_test_ru.json` — preview segments/profiles и объединённый AUDIO;
- `compare_voice_samples_ru.json` — три reference WAV и раздельное сохранение результатов;
- `text_to_speech_with_emotions_ru.json` — готовая много-сегментная схема.

Подробные инструкции: [../../docs/COMFYUI_SETUP_RU.md](../../docs/COMFYUI_SETUP_RU.md), [../../docs/VOICE_SAMPLES_AND_EMOTIONS_RU.md](../../docs/VOICE_SAMPLES_AND_EMOTIONS_RU.md) и [../../docs/VOICE_CREATION_COMFYUI_AND_SILLYTAVERN_RU.md](../../docs/VOICE_CREATION_COMFYUI_AND_SILLYTAVERN_RU.md). Endpoint намеренно принимает только `http://127.0.0.1:<port>`.
