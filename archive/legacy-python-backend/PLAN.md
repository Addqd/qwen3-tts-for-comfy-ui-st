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
   - Quote-aware контракт реализован: narration neutral, tag только для следующей полной ASCII-цитаты, затем reset; неизвестные/malformed tags не попадают в worker.
   - Fallback реализован в порядке family style → family neutral → безопасный configured profile; порядок profiles фиксируется в metrics.

5. **IN PROGRESS — SillyTavern API-совместимость и существующая установка**
   - Проверить OpenAI-compatible схему текущего штатного provider.
   - Выполнить реальный совместимый HTTP-тест.
   - Описать точную настройку на русском.
   - Выбран встроенный OpenAI Compatible provider SillyTavern 1.18.0; core и LLM-настройки не менялись.
   - Экспериментальное изменение `extension_settings.tts` восстановлено из byte-for-byte backup; проект больше не пишет пользовательские настройки.
   - Созданы независимый `start-tts.bat`, ручная инструкция и read-only proxy smoke; объединённые lifecycle/config scripts удалены.
   - Backend/API compatibility и payload проверены тестами; живой proxy требует уже запущенной пользователем SillyTavern, browser UX остаётся ручной проверкой.

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

9. **DONE — Портфолио актрис и ready-to-use profiles**
   - Подготовлены 39 emotion/neutral профилей восьми актрис в локальной игнорируемой библиотеке.
   - Те же профили установлены в `voice_library/profiles`; backend видит 58 voices суммарно.
   - Для каждой актрисы настоящим Qwen созданы neutral, happy-router и four-segment Router примеры — 24 файла.
   - Все references и examples проверены как mono PCM16 24 kHz, invalid/clipping = 0.

10. **DONE — Pleasure/intimate и hardening voice library**
   - Allowlist расширен до десяти отдельных styles: `neutral`, `soft`, `whisper`, `breathy`, `happy`, `sad`, `angry`, `tense`, `pleasure`, `intimate`; quote-aware контракт и neutral fallback сохранены.
   - Voice-library backups вынесены из active `profiles/` в `backups/`; legacy `*.backup-*` внутри `profiles/` игнорируются loader-ом.
   - Qwen prompt cache учитывает reference identity, `ref_text` и `clone_mode`; regression tests не загружают настоящую модель.
   - Backend и лёгкий ComfyUI mirror защищены общим parity corpus; установленная Copy-нода обновлена до `0.3.0` без изменения ComfyUI/Manager/embedded dependencies.
   - Документация разделяет исходный sample rate reference и текущий output 24 kHz; `sentence_ms`/`paragraph_ms` помечены как reserved, `crossfade_ms` — legacy edge-fade key.
   - Полная suite: 65 tests; реальный CPU/Qwen fallback smoke подтвердил `neutral → pleasure → neutral → intimate` и четыре выбора family-neutral.

11. **DONE — Динамические модели и русский quality pipeline**
   - Добавлен registry для `tts-1-ru`, `tts-1-ru-fast` (0.6B) и `tts-1-ru-quality` (1.7B) с model-specific runtime policy.
   - Model manager держит не более одного persistent worker, выгружает предыдущую модель до switch и не делает тихий fallback.
   - Добавлены `default`/`stable_russian`, `off`/`basic`/`full` normalization и глобальный/request pronunciation dictionary.
   - API health/models/metrics/headers показывают requested/resolved model, action, mode/device/dtype/attention и настройки русского pipeline.
   - ComfyUI package 0.4.0 содержит выпадающий выбор модели, preset, normalization и pronunciation overrides; добавлены обновлённые workflow и реальные screenshots.
   - Реально проверены CPU 1.7B load/synthesis, переход 1.7B → 0.6B, project-local ComfyUI Queue → Preview Audio и отсутствие второй Qwen-модели в ComfyUI Python.

## Блокировки, требующие пользователя

- Собственный разрешённый русский WAV и точная транскрипция, если нужен конкретный пользовательский/персонажный голос.
- Субъективная оценка финального аудио.
- Прослушивание public primary/backup samples и подтверждение dataset-транскрипций на слух.
- Ручной запуск SillyTavern и подтверждение browser autoplay/Stop/replay/group chat после прохождения HTTP proxy test.
