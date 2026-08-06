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
