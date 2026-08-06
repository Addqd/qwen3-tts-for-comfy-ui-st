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
- По просьбе пользователя добавлен корневой `start-tts-and-comfyui.bat` для запуска двойным щелчком без изменения Execution Policy. Реальная проверка BAT: backend `ok`, ComfyUI 0.30.0, `visible_console=true`, Manager включён; после проверки PID 18884/15956 остановлены проектными скриптами, порты 8188/8020 свободны.
- После пользовательской проверки подтверждено, что прежний BAT оставлял скрытый backend на 8020 после закрытия консоли ComfyUI. Добавлен watcher `-WaitForComfyUIExit`: BAT остаётся открытым, ждёт завершения подтверждённого ComfyUI PID, очищает stale state и останавливает backend только если запустил его сам. End-to-end тест с имитацией закрытия окна: ComfyUI PID 4556 завершён, watcher автоматически остановил backend PID 4468, BAT вернул exit 0, оба порта свободны, `runtime_files=0`.
- По запросу пользователя вся папка `D:\Folder\ia\ComfyUI_windows_portable` (57 806 файлов, 6 582 034 297 bytes) перемещена без копирования в `qwen3-tts-st\ComfyUI_windows_portable`. Перед move создана checkpoint-ветка `codex/pre-comfyui-relocation-20260806` на `7e210db`. `config.local.yaml` и tracked default переведены на относительный путь, install marker обновлён; старый внешний путь больше не существует.
- После переноса выполнен реальный контрольный запуск корневого BAT: backend `/health` вернул `ok`, ComfyUI 0.30.0 запущена из нового embedded Python, Manager включён, `/object_info` содержит все 7 Qwen TTS-нод. После завершения подтверждённого ComfyUI PID 17976 watcher автоматически остановил backend PID 18404; оба endpoint недоступны и `runtime_files=0`. Оба окружения прошли `pip check`, 24 pytest-теста прошли, все проектные PowerShell-скрипты разбираются без ошибок.

## 2026-08-06 18:00–19:00 MSK — открытые русские samples и Emotion Router

- Обнаружено, что предыдущий PR #1 уже слит пользователем/на GitHub в `main` (`b38791b`); merge текущим сеансом не выполнялся. От актуального `origin/main` создана ветка `feature/russian-voice-samples-and-emotions`.
- Повторно проверены полный проект, `.venv`, embedded ComfyUI, sources, tests, logs, artifacts и runtime. Baseline: `24 passed`, оба `pip check` чистые, model snapshot уже локален.
- Исследованы актуальные открытые русские корпуса. Основным выбран `langswap/dialogs-ru-emotional-conversations` revision `e25ba617b2b56bd1dbf255d3905c51bd8da3d31f`: 3 студийных актёра, transcripts/emotions, OpenRAIL и явное written consent. DUSHA и RESD оставлены reference-кандидатами; повторные большие загрузки не выполнялись.
- В `local_voice_samples` скачано 25 WAV (14 343 256 bytes), подготовлен 21 selected sample в трёх несмешанных voice families, 4 rejected; soft/breathy и другие честно отсутствующие same-speaker styles записаны в reports. Исходные mono PCM16 44.1 kHz копируются byte-identical, без blind trimming/normalization/resampling.
- Добавлены воспроизводимые `scripts/prepare_voice_samples.py`, `config/voice_samples.example.json` и тест. Все WAV/transcripts/local metadata/reports подтверждённо игнорируются Git.
- Создано 15 локальных временных primary profiles. `TestRuDima` имеет neutral/happy/sad/angry/whisper/tense; tense использует помеченный medium-confidence proxy из source fear. Субъективное качество и точность transcripts требуют прослушивания.
- Аудит выявил рассогласование unknown-tag: Comfy делал neutral, backend оставлял неизвестный tag в тексте. Backend parser исправлен: корректный unknown identifier → neutral, tag удаляется. Metrics теперь записывают фактические voice IDs.
- Добавлен `tests/test_emotion_router.py`: plain/one/multi/Unicode/same-line/multiline/repeat/unknown/missing profile/global fallback/empty/punctuation/no tags/order/stitch. Целевые тесты после исправления: `20 passed`.
- Реальный CUDA FP32 backend увидел 19 profiles total и выполнил 3 Qwen-запроса без ошибок: neutral 4.72 s, happy 4.32 s, four-segment 7.32 s. Metrics подтвердили neutral → happy → sad → angry и соответствующие `clone:test_ru_dima_*` IDs.
- Созданы `voice_profile_from_wav_ru.json`, `emotion_router_test_ru.json`, `compare_voice_samples_ru.json`, `text_to_speech_with_emotions_ru.json`. Все JSON/links/types прошли unit validation; реальный ComfyUI `/object_info` подтвердил семь Qwen types и LoadAudio/PreviewAudio/SaveAudio.
- Первый sandboxed Comfy prompt выявил только запрет песочницы на запись в Comfy temp. После разрешённого unsandboxed localhost-запуска с project runtime temp реальный prompt `b371b073-570e-4700-a56e-05f2746e0aaa` завершился success: четыре clean Russian segments, четыре expected profiles, PreviewAudio FLAC mono 24 kHz 7.64 s.
- Добавлены подробные `VOICE_SAMPLES_AND_EMOTIONS_RU.md`, `EMOTION_ROUTER_AUDIT_RU.md` и только технический `SILLYTAVERN_INTEGRATION_PLAN_RU.md`. SillyTavern не изменялась.
- Финальная локальная проверка: `36 passed in 4.52s`, compileall success, оба `pip check` без конфликтов, все PowerShell scripts разобраны, все YAML/workflow JSON загружены, `git diff --check` чист, local/generated audio подтверждённо ignored. Тестовые процессы остановлены; порты 8020/8188 свободны.
- Исправлен повторный сбой BAT при уже работающей ComfyUI без `runtime/comfyui.json`: ownership/PID preflight теперь выполняется до запуска backend, startup cleanup охватывает только процессы, запущенные текущей командой, а сообщение прямо требует закрыть старую ComfyUI и повторить запуск. Регрессионная проверка на реально работающей неотслеживаемой ComfyUI подтвердила, что backend остаётся остановленным и существующая ComfyUI не затрагивается; полный pytest — `36 passed`.
