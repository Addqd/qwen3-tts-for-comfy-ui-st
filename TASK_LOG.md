# Журнал выполнения

## 2026-08-05 — Этап 1 начат

- Получена и полностью прочитана постановка из вложения UTF-8.
- Папка проекта проверена: пользовательские файлы отсутствовали, Git ещё не был инициализирован.
- Созданы `PROJECT_CONTEXT.md`, `AGENTS.md`, `PLAN.md`, `TASK_LOG.md` и `.gitignore`.
- Следующий шаг: инициализация Git и фактический аудит Windows/оборудования.

## 2026-08-05 — Реализация и проверки завершены

- Git инициализирован; глобальный `safe.directory` не менялся.
- Выполнен системный аудит и проверены первичные Qwen, PyTorch, SillyTavern и ComfyUI источники.
- Создана Python 3.12 `.venv`; зависимости установлены и закреплены. Финальный стек использует `torch/torchaudio 2.11.0+cu126`.
- Реализован localhost-only FastAPI, очередь, timeout, shutdown, метрики, CPU/CUDA/on-demand/auto, preprocessing, emotion router и voice library.
- Исправлен отдельный cache path для Qwen AutoProcessor: все Hugging Face файлы остаются в `model_cache` проекта.
- Настоящая Base 0.6B загрузилась. CPU дал три HTTP 200 русских WAV; финальный прогон: 1.92 с аудио за 22.59 с.
- Через consent-gated endpoint создан синтетический русский ICL-профиль `clone:QwenDemoRussianNeutral`; пользовательский разрешённый голос ещё требуется.
- Создан синтетический `clone:QwenDemoHappyCandidate`; реальный двухсегментный neutral/happy router дал HTTP 200 и 4.44 s WAV. Эмоциональность кандидата требует прослушивания.
- Реальный SillyTavern-shaped MP3 запрос прошёл сначала на mock, затем на Qwen: HTTP 200, `audio/mpeg`, mono 24 kHz, 3.672 s.
- Создан лёгкий пакет из шести ComfyUI API-нод, safe installer/uninstaller/tester и четыре workflow JSON.
- ComfyUI-нода реально вызвала mock HTTP backend и вернула AUDIO `[1,1,T]` 24 kHz; настоящая ComfyUI не найдена, поэтому Queue Prompt и установка заблокированы до получения пути/подтверждения.
- CUDA FP16 модель загрузилась на cu130 и cu126 без OOM/assert, но bounded synthesis не завершился за 5+ минут. CUDA FP32 затем успешно дала три последовательных WAV; steady synthesis 5.53 с для 2.24 с аудио, RTF 2.47. On-demand и auto дали HTTP 200 и фактически освободили VRAM. Рабочий local default — auto с повышенным порогом VRAM.
- Unit/integration suite: 15 passed, включая queue timeout/full, resource auto-routing и audio stitch. Python compile, PowerShell parse/ASCII, workflow JSON, fresh background start/status/stop и UTF-8 content type прошли.
- Подготовлена русская документация, диагностика и benchmark scripts.

Оставшиеся действия пользователя: дать путь к ComfyUI для подтверждённой установки и реального workflow; предоставить разрешённый русский WAV с точной расшифровкой; прослушать аудио и оценить качество.

## 2026-08-06 — Восстановление фактического состояния

- Полностью прочитаны проектные правила, контекст, план, журнал, README, конфигурация, документация, исходный код, интеграции, скрипты и тесты. Git-история состоит из одного коммита `98ac79c`; `main` совпадает с `orgin/main`, рабочее дерево перед аудитом было чистым, tags отсутствуют.
- Создана контрольная backup-ветка `codex/pre-audit-20260806` на коммите `98ac79c` без переключения рабочей ветки.
- `.venv` восстановлению не требует: Python 3.12.13, `pip check` без конфликтов, закреплённые `torch/torchaudio 2.11.0+cu126`; Torch видит RTX 2070 SUPER и CUDA 12.6. Импорты Qwen, backend и конфигурации прошли.
- Snapshot модели `5d83992436eae1d760afd27aff78a71d676296fc` занимает около 2.52 GB и разрешается через `snapshot_download(..., local_files_only=True)`; повторная загрузка не требуется.
- Текущие безопасные проверки до расширения suite: `15 passed`, Python compileall, разбор всех PowerShell-скриптов, загрузка всех YAML-конфигов и четырёх workflow JSON. Backend стартовал на `127.0.0.1:8020`, выбрал `cuda_on_demand`, ответил на health/models/voices/metrics и штатно остановился без загрузки модели.
- Проверены сохранённые артефакты: 13 WAV и 2 MP3 читаются как mono 24 kHz; WAV содержат только конечные значения и не клиппуют. Runtime PID-файлов и временных job-папок после проверки нет. Русские voice metadata подтверждены корректными UTF-8 данными; кракозябры при отдельных командах были только эффектом Windows-консоли.
- Полностью завершены: основа/аудит, выбранный стек, backend и окружение, технические voice/preprocessing/emotion компоненты, OpenAI-compatible/SillyTavern-shaped HTTP путь, скрипты производительности и документация.
- Реализовано, но не проверено в целевой программе: установка custom nodes и Queue Prompt внутри реальной пользовательской ComfyUI.
- Частично завершено: production voice library и субъективная проверка emotion/качества используют пока только синтетические demo-профили.
- Не начато из-за отсутствия входных данных: импорт разрешённого пользовательского русского голоса.
- Заблокировано действиями пользователя: путь и подтверждение изменения папки ComfyUI; разрешённый WAV с точной транскрипцией; субъективная оценка аудио.
- Исправлено расхождение документации: фактический `config.local.yaml` задаёт `auto`, а не `cpu`. Добавлены быстрые тесты длинного русского текста/chunking, UTF-8 JSON, повреждённого RIFF, localhost guard, shutdown unload и поведения ComfyUI-нод при недоступном backend; итоговая suite — `20 passed`.

## 2026-08-06 14:05–15:10 MSK — официальная ComfyUI и реальная интеграция

- Работа ведётся только в `feature/comfyui-installation-and-integration`, созданной от `80038c0`. На GitHub обнаружено фактическое отличие от исходного ожидания: `origin/main` уже содержит `80038c0`; push в main, rebase, force push и merge не выполнялись.
- Установлен GitHub CLI 2.97.0 и 7-Zip 26.02 после подтверждения пользователя; `gh auth status` вне sandbox подтвердил авторизацию и доступ к приватному origin.
- С официального release ComfyUI `v0.30.0` (2026-08-03, commit `b1693ec`) скачан `ComfyUI_windows_portable_nvidia.7z` размером 2 110 797 220 bytes. SHA256 совпал: `f4353d069dd7342e3bef421f07f003cca53ca84168102705cfc83f66449f5ae5`; `7z t` — `Everything is Ok`.
- Вне репозитория создана `D:\Folder\ia\ComfyUI_windows_portable`: embedded Python 3.13.14, ComfyUI 0.30.0, torch 2.13.0+cu130. Чистый запуск до custom nodes: root и `/system_stats` HTTP 200, listener только `127.0.0.1:8188`, RTX 2070 Super распознана.
- Из shipped `ComfyUI\manager_requirements.txt` установлен официальный `comfyui-manager==4.2.2`; `pip check` прошёл. Добавлены только лёгкие Manager-зависимости (GitPython/PyGithub/toml/uv/crypto dependencies). `qwen_tts` в embedded Python отсутствует; отдельный torch и backend туда не устанавливались. Необязательный `matrix-nio` намеренно не установлен.
- Junction был отклонён средой безопасности; выбран разрешённый `-Mode Copy`. Наш пакет установлен в `ComfyUI\custom_nodes\qwen_tts_api_nodes`. При обновлениях backups перенесены за пределы сканируемой папки в `ComfyUI\.qwen_tts_api_nodes-backups`; повторный запуск показал ровно одну строку импорта нод.
- Исправлены реальные проблемы: неинициализированный `WhatIf` в wrappers; некорректный standalone import test; backup внутри `custom_nodes`; отсутствие Models node; отсутствие сохраняемых `ui` outputs в ComfyUI history; неизвестные voice-tags теперь удаляются и дают neutral fallback.
- Зарегистрированы семь нод категории `Qwen TTS API`: Server, Health, Models, Voice Selector, Emotion Script, Synthesize, Clone Voice. В логах нет `IMPORT FAILED`, `ModuleNotFoundError` или traceback наших нод. `object_info` подтверждает output-node статус диагностических нод.
- Выполнен настоящий диагностический Queue Prompt через `/prompt` и `/history`: health `ok`, models `tts-1-ru` и `Qwen/Qwen3-TTS-12Hz-0.6B-Base`, четыре текущих demo voice IDs, русский Unicode/emotion/unknown-tag fallback и понятное сообщение отсутствующего профиля. Все четыре workflow сверены с реальным `/object_info`; очередь после выполнения `running=0`, `pending=0`.
- Выполнен настоящий synthesis workflow `a1431233-b6dd-41fa-990b-9a3c18296836`: ComfyUI → localhost backend → Qwen on-demand → `PreviewAudio`. Статус `success`, audio 15.6 с, 24 kHz, temp `ComfyUI_temp_rxvin_00001.flac`; backend после job `model_loaded=false`, очередь пуста.
- Ресурсы: до сервисов RAM free 34 034 MiB / VRAM used 1 180 MiB; backend+ComfyUI до workflow 32 834 MiB / 1 281 MiB; во время Qwen workflow VRAM used 6 268 MiB; после job RAM free 32 907 MiB / VRAM used 1 289 MiB. Внешние AI-процессы не завершались.
- Фактически выполненные проверки: `pytest tests/test_comfyui_nodes.py -q` → `8 passed`; parse всех PowerShell scripts → OK; `test-install.ps1` → семь mappings; `test-comfyui-integration.ps1 -SkipSynthesis` → success; полный `test-comfyui-integration.ps1` → success. Полный pytest/compileall выполняются после завершения документации.
- Созданы коммиты: `fdf575d fix: harden ComfyUI node installation`, `c425df8 feat: expand ComfyUI API client diagnostics`, `9caaba7 feat: add ComfyUI service management and API tests`, `fe47640 docs: add Russian ComfyUI setup and test results`.
- Пользовательский WAV отсутствует. Реальное клонирование, сходство, русский акцент, whisper/breathy и субъективная эмоциональность остаются отложенными; технические demo-профили не выдаются за пользовательский голос.
- Финальная проверка после документации: полный `pytest -q` → `24 passed in 3.85s`; `compileall -q src integrations/comfyui/qwen_tts_api_nodes tests` → success; все YAML и workflow JSON прочитаны как UTF-8; все PowerShell scripts разобраны parser без ошибок; `pip check` прошёл и в проектной `.venv`, и в embedded Python ComfyUI.
- Перед остановкой `status.ps1` и `status-comfyui.ps1` подтвердили живые проектные PID и localhost APIs. Затем `stop-comfyui.ps1` остановил PID 14724, `stop.ps1` — PID 15868; порты 8188/8020 свободны, `runtime` пуст.
- Feature-ветка отправлена обычным push в origin. Открыт Draft PR [#1 Add ComfyUI installation and Qwen TTS integration](https://github.com/Addqd/qwen3-tts-for-comfy-ui-st/pull/1) с base `main`; merge, Ready for Review, force push и удаление ветки не выполнялись.
