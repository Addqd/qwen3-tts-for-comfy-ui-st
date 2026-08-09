# Локальная Qwen3-TTS для Windows

Один локальный backend загружает `Qwen/Qwen3-TTS-12Hz-0.6B-Base` и обслуживает SillyTavern, ComfyUI и другие OpenAI-compatible TTS-клиенты через `127.0.0.1:8020`. Модель не загружается в ComfyUI и не связана с используемой LLM.

## Что уже проверено на этой машине

- Python 3.12 `.venv`, `qwen-tts 0.1.1`, `transformers 4.57.3`.
- Настоящая модель скачана в `model_cache` и дважды синтезировала русский WAV на CPU.
- Русский ICL-профиль `clone:QwenDemoRussianNeutral` создан через API из синтетического теста.
- OpenAI-compatible WAV и настоящий Qwen SillyTavern-shaped MP3 запросы получили корректный аудиоответ.
- ComfyUI API-нода получила WAV и вернула `AUDIO` формы `[batch, channels, samples]`.
- CUDA FP32/SDPA и on-demand реально синтезируют; persistent после загрузки дал 5.53 с вычисления для 2.24 с WAV. FP16 на RTX 2070 Super не завершил ограниченную генерацию за 5 минут. `auto` выбирает FP32 on-demand при безопасном запасе VRAM и CPU при нехватке.

Это техническая проверка, не оценка качества голоса. Прослушайте файлы из `artifacts/audio-tests`.

## Быстрый запуск

В корне проекта:

```powershell
.\start.ps1
.\status.ps1
.\scripts\test-russian.ps1
.\stop.ps1
```

`start.ps1` использует локальный `config/config.local.yaml` и слушает только `127.0.0.1`. Swagger: `http://127.0.0.1:8020/docs`; health: `http://127.0.0.1:8020/health`.

Backend вместе с установленной ComfyUI:

```powershell
.\scripts\start-tts-and-comfyui.ps1
.\scripts\test-comfyui-integration.ps1 -SkipSynthesis
# без -SkipSynthesis выполняется реальный короткий Qwen workflow
```

Для запуска двойным щелчком используйте `start-tts-and-comfyui.bat` в корне проекта. Пока окно BAT открыто, скрытый session watcher следит за обоими сервисами. Закрытие окна BAT, отдельной консоли ComfyUI или аварийное завершение backend автоматически останавливает **оба** подтверждённых проектных процесса. Если ComfyUI из `ComfyUI_windows_portable` уже работает, launcher безопасно восстанавливает её PID по listener-порту и точному пути к embedded Python; процесс из другой папки он не завершает. ComfyUI: `http://127.0.0.1:8188`. Краткая инструкция: [docs/COMFYUI_QUICKSTART_RU.md](docs/COMFYUI_QUICKSTART_RU.md).

Только backend, без ComfyUI и без управления SillyTavern:

```powershell
.\start-tts.bat
# либо .\start.ps1
```

SillyTavern запускается отдельно своим существующим `Start.bat` и настраивается вручную через встроенный OpenAI Compatible provider. Проект не меняет её settings, карточки, чаты, prompts, Regex или Voice Map. Проверка proxy после ручного запуска: `.\scripts\test-sillytavern-integration.ps1`. Quick Start: [docs/SILLYTAVERN_QUICKSTART_RU.md](docs/SILLYTAVERN_QUICKSTART_RU.md).

## Повторная установка

Нужен CPython 3.12. На этой системе `py` не содержит установленного Python, поэтому путь передаётся явно:

```powershell
.\scripts\install.ps1 -Python "C:\path\to\Python312\python.exe" -TorchVariant CPU
```

Для экспериментальной CUDA 12.6 сборки:

```powershell
.\scripts\install.ps1 -Python "C:\path\to\Python312\python.exe" -TorchVariant CUDA126
```

Скрипт создаёт только `.venv` проекта, пишет лог в `logs`, использует `uv`, проверяет импорты и зависимости. Модель скачивается при первом реальном запросе.

## API

```powershell
$body = @{
  model = "tts-1-ru"
  voice = "clone:QwenDemoRussianNeutral"
  input = "Русский текст для озвучивания."
  response_format = "wav"
  speed = 1.0
} | ConvertTo-Json
Invoke-WebRequest -Uri http://127.0.0.1:8020/v1/audio/speech `
  -Method Post -ContentType "application/json; charset=utf-8" `
  -Body ([Text.Encoding]::UTF8.GetBytes($body)) -OutFile result.wav
```

Endpoints: `GET /health`, `/v1/models`, `/v1/voices`, `/metrics`; `POST /v1/audio/speech`, `/v1/audio/voice-clone`, `/admin/reload-voices`.

## Голоса и эмоции

Для наилучшего подтверждённого режима используйте русский WAV и его точную дословную расшифровку (`clone_mode: icl`). Добавление и проверка описаны в [voice_library/README_RU.md](voice_library/README_RU.md). Клонируйте только голос, на использование которого есть разрешение.

Эмоции реализованы отдельными референсами одного персонажа: `neutral`, `soft`, `whisper`, `breathy`, `happy`, `sad`, `angry`, `tense`, `pleasure`, `intimate`. `pleasure` означает довольную/чувственно-положительную подачу, `intimate` — близкую приватную манеру; это самостоятельные profiles, а не смеси существующих styles. Повествование всегда neutral. Тег вида `[voice:happy]` действует только на непосредственно следующую полную реплику в ASCII-кавычках `"..."`, затем стиль сбрасывается. Неизвестные и malformed service tags удаляются до worker. Fallback: `<family>_<style>` → `<family>_neutral` → настроенный безопасный профиль.

Подготовка открытых русских samples, 15 временных test profiles и пошаговая работа в ComfyUI: [docs/VOICE_SAMPLES_AND_EMOTIONS_RU.md](docs/VOICE_SAMPLES_AND_EMOTIONS_RU.md). Доказательный аудит Router: [docs/EMOTION_ROUTER_AUDIT_RU.md](docs/EMOTION_ROUTER_AUDIT_RU.md).

Локальное портфолио восьми актрис находится в игнорируемой папке `local_voice_samples/readytouseprofiles`: 39 подготовленных профилей и 24 настоящих Qwen-примера. Те же 39 профилей установлены в локальную `voice_library/profiles`. Состав и ограничения: [docs/ACTRESS_VOICE_PROFILES_RU.md](docs/ACTRESS_VOICE_PROFILES_RU.md).

## Интеграции

- [SillyTavern: полная настройка](docs/SILLYTAVERN_SETUP_RU.md)
- [SillyTavern: Quick Start](docs/SILLYTAVERN_QUICKSTART_RU.md)
- [SillyTavern: диагностика](docs/SILLYTAVERN_TROUBLESHOOTING_RU.md)
- [ComfyUI](docs/COMFYUI_SETUP_RU.md)
- [Создание голосов и эмоций: ComfyUI → SillyTavern](docs/VOICE_CREATION_COMFYUI_AND_SILLYTAVERN_RU.md)
- Workflow JSON: `integrations/comfyui/example_workflows`

Официальная ComfyUI Windows Portable NVIDIA `0.30.0` находится в подпапке `ComfyUI_windows_portable` внутри корня проекта. На этом компьютере проект можно перемещать одной папкой: конфигурация использует относительный путь. При переносе на другой компьютер обычный `.venv` может потребовать пересоздания под установленный там Python; embedded Python ComfyUI остаётся самодостаточным. Доступны Server, Health, Models, Voice Selector, Emotion Script, Synthesize и Clone Voice. Ноды установлены копированием и не содержат Qwen/torch/transformers; Qwen-модель остаётся только в backend. Пользовательские настройки SillyTavern проектом не изменяются.

## Режимы

- `CPU`: подтверждён, `scripts/start-tts-cpu.ps1`.
- `CUDA`: подтверждён только FP32/SDPA, `scripts/start-tts-gpu.ps1`; persistent занимает суммарно около 6.4 ГБ VRAM.
- `CUDA on demand`: подтверждён FP32 inference и фактический возврат VRAM.
- `auto`: подтверждён; оценивает VRAM/RAM/GPU-процессы, выбирает on-demand при внешних GPU-клиентах и пишет причину.

Результаты и команды: [docs/PERFORMANCE_RU.md](docs/PERFORMANCE_RU.md). Диагностика: `.\scripts\diagnose.ps1` и [docs/TROUBLESHOOTING_RU.md](docs/TROUBLESHOOTING_RU.md).

## Модели и русский TTS

- `tts-1-ru` — совместимый backend default;
- `tts-1-ru-fast` — Qwen3-TTS Base 0.6B;
- `tts-1-ru-quality` — Qwen3-TTS Base 1.7B.

В одном backend-процессе находится не более одной модели: при смене алиаса предыдущая модель выгружается до загрузки следующей. Доступны request-level `generation_preset=stable_russian`, `russian_normalization=off|basic|full` и `pronunciation_overrides`. Подробности и реальные ComfyUI screenshots: [docs/MODELS_AND_RUSSIAN_TTS_RU.md](docs/MODELS_AND_RUSSIAN_TTS_RU.md) и [docs/COMFYUI_QUICKSTART_RU.md](docs/COMFYUI_QUICKSTART_RU.md).

Backend defaults — `stable_russian + full`: это даёт рекомендуемый русский путь SillyTavern и другим legacy OpenAI-compatible запросам без нестандартных полей. Явно переданные `default` и `off` всегда имеют приоритет. Из-за единственного model manager полный synthesis lifecycle сериализован даже при большем `queue.max_concurrent`; фактические значения видны в `/health`.

## Обновление и резервная копия

Перед обновлением сохраните `config/config.local.yaml` и весь `voice_library/profiles`. Не удаляйте `reference.wav`: точная транскрипция связана именно с ним. После обновления снова выполните install, тесты и один контрольный WAV. `model_cache`, `.venv`, логи и runtime можно восстановить, голоса — нет.
