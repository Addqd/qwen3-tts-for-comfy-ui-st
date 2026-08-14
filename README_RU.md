# Qwen3-TTS для SillyTavern и ComfyUI

Production neural engine проекта — persistent `qwentts.cpp` на `127.0.0.1:8030`:

- Qwen3-TTS 1.7B Base, только BF16;
- `qwen-talker-1.7b-base-BF16.gguf`;
- `qwen-tokenizer-12hz-BF16.gguf`;
- CUDA на RTX 2070 SUPER;
- основной голос `clone:test_ru_dima_neutral`;
- один и тот же voice ID используется API, SillyTavern и ComfyUI.

Лёгкий Python facade на `127.0.0.1:8020` выполняет OpenAI-compatible API, нормализацию, pronunciation replacements, voice profiles и преобразование аудио. Он также содержит optional CPU preprocessing: Silero Stress (по умолчанию включён) и Silero Text Enhancement (по умолчанию выключен). Обе модели используют один CPU-only PyTorch с одним потоком, загружаются лениво по одному разу и не занимают VRAM.

## Запуск

```powershell
.\start.ps1
.\status.ps1
.\stop.ps1
```

Backend и ComfyUI вместе:

```powershell
.\start-tts-and-comfyui.bat
```

Startup проверяет SHA-256 официального prebuilt и BF16 GGUF, запускает одну persistent CUDA-модель, регистрирует сохранённые `.spk/.rvq`, затем запускает facade. Все поддерживаемые пути создают или присоединяются к одной project session; основные компоненты контролируются Windows Job Object. Корневой BAT остаётся живым и регистрируется как owner, а fire-and-forget `start.ps1` и standalone ComfyUI launcher после успешного запуска освобождают временного owner и контролируются по запущенным компонентам.

Silero Stress и Text Enhancement независимы и могут работать вместе. Настройки сохраняются существующим `GET/PUT /admin/runtime-settings`. Ручные pronunciation overrides имеют приоритет над автоматической обработкой. Форматы ударений `+`, combining acute и apostrophe экспериментальны и требуют ручного A/B-прослушивания; Text Enhancement может изменить авторскую пунктуацию.

## API

```text
GET  http://127.0.0.1:8020/health
GET  http://127.0.0.1:8020/v1/models
GET  http://127.0.0.1:8020/v1/voices
POST http://127.0.0.1:8020/v1/audio/speech
POST http://127.0.0.1:8020/v1/audio/voice-clone
```

Русский JSON всегда отправляйте как UTF-8:

```powershell
$body = @{ model="tts-1-ru"; voice="clone:test_ru_dima_neutral"; input="Проверка русского текста."; response_format="mp3" } | ConvertTo-Json
Invoke-WebRequest http://127.0.0.1:8020/v1/audio/speech -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body ([Text.Encoding]::UTF8.GetBytes($body)) -OutFile result.mp3
```

Canonical ComfyUI Voice Lab: [voice_profile_from_wav_ru.json](integrations/comfyui/example_workflows/voice_profile_from_wav_ru.json).

Подробности: [ComfyUI](docs/COMFYUI_QUICKSTART_RU.md), [SillyTavern](docs/SILLYTAVERN_QUICKSTART_RU.md), [performance](docs/PERFORMANCE_RU.md).

Старый Python inference и training сохранены только как ручной fallback в [archive/legacy-python-backend](archive/legacy-python-backend/README.md); active startup их не использует.

Streaming в SillyTavern намеренно не интегрирован: проект возвращает готовый WAV/MP3 целиком.
