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
   - Реализовать voice-теги, fallback, паузы, ресемплинг и бесщелочное объединение.

5. **DONE — SillyTavern**
   - Проверить OpenAI-compatible схему текущего штатного provider.
   - Выполнить реальный совместимый HTTP-тест.
   - Описать точную настройку на русском.

6. **BLOCKED — ComfyUI**
   - Создать лёгкий custom-node package без torch/transformers/qwen_tts.
   - Реализовать server, synthesize, clone, selector, emotion и health по актуальному API; использовать штатное сохранение AUDIO.
   - Подготовить безопасные install/uninstall/test-скрипты и workflow JSON.
   - Проверить импорт, mappings, AUDIO и при доступной установке реальный workflow API.

7. **DONE — Производительность и сдача**
   - Протестировать CPU float32, CUDA FP16/FP32 SDPA, on-demand и auto; FP16 оставить документированной несовместимостью.
   - Зафиксировать память, загрузку, стабильность, время, RTF и освобождение ресурсов.
   - Выполнить аудионабор и запросить субъективную оценку пользователя.
   - Завершить русскоязычную документацию и TASK_LOG.

## Блокировки, требующие пользователя

- Разрешённый русский WAV и точная транскрипция для настоящего voice cloning.
- Пути к ComfyUI/SillyTavern и подтверждение перед изменением их папок, если их нельзя безопасно определить.
- Субъективная оценка финального аудио.
