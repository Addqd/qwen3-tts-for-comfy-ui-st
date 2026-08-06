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
