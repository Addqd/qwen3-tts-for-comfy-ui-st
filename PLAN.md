# План реализации

Статусы: `TODO`, `IN PROGRESS`, `DONE`, `BLOCKED`.

1. **DONE — Основа и аудит**
   - Сохранить постановку, правила и журнал.
   - Инициализировать Git и .gitignore.
   - Проверить Windows, PowerShell, Python, Git, FFmpeg, NVIDIA/CUDA, RAM/CPU, порты и безопасно обнаруживаемые установки клиентов.
   - Сохранить факты в `docs/SYSTEM_AUDIT.md`.

2. **DONE — Исследование и выбор стека**
   - Проверить официальные Qwen3-TTS, model card, technical report, PyTorch, SillyTavern и ComfyUI.
   - Сверить текущий API Base-модели, Windows/Turing ограничения, ComfyUI AUDIO.
   - Сравнить внешние серверы и прямые ComfyUI-ноды.
   - Зафиксировать источники и версии в `docs/REFERENCES.md`.

3. **DONE — Backend и окружение**
   - Создать `pyproject.toml`, lock-файл, конфигурацию и повторяемый install-скрипт.
   - Реализовать FastAPI endpoints, очередь, timeout, shutdown и метрики.
   - Реализовать worker-режимы cpu/cuda/cuda_on_demand/auto.
   - Реально загрузить `Qwen/Qwen3-TTS-12Hz-0.6B-Base` и получить русский WAV.

4. **DONE — Голоса, preprocessing, emotion-router**
   - Создать voice library и валидатор WAV.
   - Реализовать конфигурируемую очистку/сегментацию русского текста.
   - Реализовать voice-теги, fallback, паузы, ресемплинг и edge fades при объединении.
   - Неизвестный корректный tag нормализован в neutral и удаляется до worker; порядок profiles фиксируется в metrics.

5. **DONE — SillyTavern API-совместимость; реальная настройка не выполнялась**
   - Проверить OpenAI-compatible схему текущего штатного provider.
   - Выполнить реальный совместимый HTTP-тест.
   - Описать точную настройку на русском.

6. **DONE — ComfyUI**
   - Создать лёгкий custom-node package без torch/transformers/qwen_tts.
   - Реализовать server, synthesize, clone, selector, emotion и health по актуальному API; использовать штатное сохранение AUDIO.
   - Подготовить безопасные install/uninstall/test-скрипты и workflow JSON.
   - Проверить импорт, mappings, AUDIO и при доступной установке реальный workflow API.
   - Установлена официальная Portable 0.30.0 и Manager 4.2.2; семь API-client nodes зарегистрированы, четыре workflow не имеют missing nodes.
   - Реальные `/prompt` + `/history` проверки health/models/voices/emotion и Qwen synthesis → PreviewAudio прошли; очередь очищена, второй Qwen в ComfyUI отсутствует.

7. **IN PROGRESS — Производительность и сдача**
   - Протестировать CPU float32, CUDA FP16/FP32 SDPA, on-demand и auto; FP16 оставить документированной несовместимостью.
   - Зафиксировать память, загрузку, стабильность, время, RTF и освобождение ресурсов.
   - Выполнить аудионабор и запросить субъективную оценку пользователя.
   - Завершить русскоязычную документацию и TASK_LOG.
   - Техническая ComfyUI-проверка и документация завершены; пользовательское прослушивание остаётся отложенным до ревью.

8. **DONE — Открытые русские samples и доказательный Emotion Router**
   - Проверен актуальный открытый Russian emotional dataset с license/consent; скачано 25 WAV в игнорируемую локальную папку.
   - Воспроизводимо подготовлен 21 selected sample в трёх same-speaker families; 4 rejected и отсутствующие styles документированы.
   - Создано 15 временных test profiles; полная Dima-family покрывает neutral/happy/sad/angry/whisper/tense.
   - Реальные backend neutral/emotion/4-segment WAV и ComfyUI Server → Emotion Script → Synthesize → PreviewAudio прошли.
   - Добавлены четыре рабочих workflow, Router audit, ComfyUI guide и только технический план SillyTavern.

## Блокировки, требующие пользователя

- Собственный разрешённый русский WAV и точная транскрипция, если нужен конкретный пользовательский/персонажный голос.
- Субъективная оценка финального аудио.
- Прослушивание public primary/backup samples и подтверждение dataset-транскрипций на слух.
