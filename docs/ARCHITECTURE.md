# Архитектура

```text
SillyTavern ─┐
ComfyUI ─────┼─ HTTP 127.0.0.1:8020 ─ FastAPI ─ preprocessing ─ emotion router
другой клиент┘                                      │
                                             voice library
                                                    │
                                     один Qwen worker / request worker
                                                    │
                                      stitch + speed + WAV/FFmpeg
```

Backend — единственный владелец Qwen/PyTorch и общей библиотеки голосов. ComfyUI-пакет использует только стандартную библиотеку и NumPy, загружает ответ штатным ComfyUI audio loader и возвращает `{"waveform": [B,C,T], "sample_rate": int}`. Это исключает вторую копию модели и конфликт зависимостей ComfyUI.

Persistent CPU/CUDA создаёт один worker и сериализует работу семафором. `cuda_on_demand` запускает отдельный процесс на запрос, ждёт результат с timeout и завершает процесс, чтобы вернуть VRAM. `auto` только наблюдает CPU/RAM/VRAM/процессы; внешнюю AI-нагрузку не запускает и не останавливает.

Base 0.6B не получает выдуманные style-инструкции. Emotion router выбирает реальные ICL-профили, синтезирует сегменты, приводит их к одному sample rate, добавляет паузы и короткий crossfade. Все правила текста, очереди и пауз находятся в YAML.

Привязка сервера к `127.0.0.1` проверяется и в конфиге, и launcher-скриптом. API-ключ локальному backend не нужен; публикация наружу архитектурой не предусмотрена.

ComfyUI 0.30.0 работает отдельным процессом на `127.0.0.1:8188`. `scripts/start-tts-and-comfyui.ps1` не создаёт вторую копию уже готового сервиса; PID каждого процесса хранится раздельно. Custom nodes вызывают только backend endpoints, а результаты output-nodes публикуют в стандартной форме `ui + result`, поэтому они доступны downstream-соединениям и в `/history/{prompt_id}`. Реальный путь проверки: `/prompt` → Qwen TTS nodes → backend → on-demand Qwen worker → ComfyUI `PreviewAudio` → execution history.

Windows Portable хранится в `ComfyUI_windows_portable` внутри корня проекта. Значение `comfyui.install_path` задаётся относительно корня и при загрузке конфигурации преобразуется в абсолютный путь. Backend, его локальные данные, ComfyUI, embedded Python, Manager и custom nodes находятся под одним корнем; тяжёлая подпапка ComfyUI исключена из Git. При переносе на другой компьютер обычный backend `.venv` может потребовать пересоздания, так как Windows venv сохраняет ссылку на базовый Python.
