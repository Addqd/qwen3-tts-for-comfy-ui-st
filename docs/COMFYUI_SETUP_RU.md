# ComfyUI и Qwen3-TTS: подробная инструкция

Проверено 6 августа 2026 года на официальной ComfyUI Windows Portable NVIDIA `0.30.0`. Локальная установка находится в `D:\Folder\ia\ComfyUI_windows_portable`, интерфейс открывается только на `http://127.0.0.1:8188`, TTS backend — на `http://127.0.0.1:8020`.

## Что здесь работает

ComfyUI — визуальный редактор графов. Прямоугольник на холсте называется **node (нода)**. Линия между выходом одной ноды и входом другой — **connection**. Полный граф с настройками — **workflow**. Кнопка **Queue** отправляет граф в очередь выполнения.

Qwen-модель не загружается в ComfyUI. Ноды категории **Qwen TTS API** — лёгкие HTTP-клиенты; отдельный backend владеет моделью, preprocessing, emotion-router и общей библиотекой голосов. `qwen_tts` в embedded Python ComfyUI отсутствует.

Установлены семь нод:

- **Qwen TTS Server** — endpoint, timeout, модель и проверка соединения;
- **Qwen TTS Health** — health, устройство, backend-модель, очередь, ресурсы и voices;
- **Qwen TTS Models** — `GET /v1/models`;
- **Qwen TTS Voice Selector** — `GET /v1/voices` и понятное сообщение для отсутствующего профиля;
- **Qwen TTS Emotion Script** — разбор тегов без синтеза;
- **Qwen TTS Synthesize** — `POST /v1/audio/speech`, результат типа `AUDIO`;
- **Qwen TTS Clone Voice** — consent-gated создание профиля по разрешённому WAV и точной транскрипции.

## Запуск, статус и остановка

В корне TTS-проекта:

```powershell
# Оба сервиса; ComfyUI запускается скрыто и пишет лог
.\scripts\start-tts-and-comfyui.ps1

# То же, но с видимой консолью ComfyUI
.\scripts\start-tts-and-comfyui.ps1 -VisibleComfyUIConsole

# Только ComfyUI; Manager включён из config.local.yaml
.\scripts\start-comfyui.ps1

.\status.ps1
.\scripts\status-comfyui.ps1
.\scripts\stop-comfyui.ps1
.\stop.ps1
```

Самый простой вариант — дважды щёлкнуть `start-tts-and-comfyui.bat` в Проводнике. BAT переходит в каталог проекта, запускает тот же `scripts\start-tts-and-comfyui.ps1` и показывает отдельную консоль ComfyUI. Оставьте BAT-окно открытым: после закрытия консоли ComfyUI наблюдатель очистит её PID, остановит backend, только если запустил его сам, и завершится. Уже работавший до запуска BAT backend намеренно не останавливается. Системная Execution Policy не изменяется.

PID backend хранится в `runtime/server.json`, PID ComfyUI — в `runtime/comfyui.json`. Stop-скрипты сверяют PID и время запуска и не завершают чужие Python-процессы. Лог скрытого запуска: `logs/comfyui.log` и `logs/comfyui.err.log`; Manager также пишет `ComfyUI\user\comfyui.log`.

Manager 4.2.2 установлен официальной командой из `ComfyUI\manager_requirements.txt` и включается флагом `--enable-manager`. Отключить его разово можно `start-comfyui.ps1 -NoManager`, постоянно — `manager_enabled: false` в игнорируемом `config/config.local.yaml`.

## Первое знакомство с интерфейсом

1. Откройте `http://127.0.0.1:8188`.
2. Перемещайте холст зажатой средней кнопкой мыши или пробелом с перетаскиванием; масштабируйте колёсиком.
3. Workflow открывается через меню **Workflow → Open** либо перетаскиванием JSON на холст. Перед несохранёнными изменениями сохраните текущий граф.
4. Файлы находятся в `integrations\comfyui\example_workflows`.
5. Новую ноду добавляют двойным щелчком по пустому месту или поиском; введите `Qwen TTS API`.
6. Соединение создают перетаскиванием от цветного выхода к совместимому входу.
7. Запустите граф кнопкой **Queue**. Текущее и завершённое выполнение видно в очереди/history интерфейса.
8. Сохраните изменённый граф через **Workflow → Save**; не сохраняйте приватные пути и тексты в Git.

## Рекомендуемый порядок workflows

1. `backend_health_and_voices.json` — Server, Health, Models и список voices; WAV не нужен.
2. `emotion_script_preview.json` — чистый текст, JSON сегментов и распознанные стили; модель не загружается.
3. `text_to_speech_ru.json` — русский текст → backend → `PreviewAudio`.
4. `voice_clone_and_synthesize_ru.json` — шаблон на будущее; без разрешённого WAV, точной транскрипции и включённого consent запускать нельзя.

В **Server** оставьте `http://127.0.0.1:8020`. В **Voice Selector** вставьте точный `voice_id`, полученный из списка voices. В **Synthesize** вставьте русский текст и выберите `response_format=wav`. После Queue результат появляется в **Preview Audio**. Preview хранит временный FLAC для браузера; нода Synthesize одновременно возвращает `temporary_path` к исходному WAV 24 kHz. Для постоянного FLAC можно подключить встроенную `Save Audio`; если нужен именно WAV, скопируйте файл из `temporary_path` или вызовите backend API с `response_format=wav`.

## Emotion Script

Поддерживаются:

```text
[voice:neutral] [voice:soft] [voice:whisper] [voice:breathy]
[voice:happy] [voice:sad] [voice:angry] [voice:tense]
```

`character_profile_mapping` — JSON, например:

```json
{"neutral":"clone:CharacterNeutral","happy":"clone:CharacterHappy"}
```

Теги удаляются из произносимого текста. Неизвестный тег также удаляется и безопасно становится `neutral`. Если профиля нужного стиля нет, backend использует neutral/fallback-профиль персонажа. Название стиля не создаёт эмоцию само по себе: для реального `happy`, `whisper` и других вариантов нужны отдельные разрешённые референсы.

## Когда появится пользовательский WAV

До WAV полностью работают health, models, voices, emotion-router, workflow API и синтез из технических demo-профилей. Не проверены и не заявлены: сходство с голосом пользователя, акцент, whisper/breathy, эмоциональность и качество пользовательского клона.

Для клонирования используйте только голос, на который есть разрешение:

1. Сохраните оригинал вне Git; рабочую копию можно положить в `voice_library\inbox` или загрузить через ComfyUI `Load Audio` (она попадёт в `ComfyUI\input`).
2. Запустите `.\scripts\validate-voice.ps1 -Path "...\reference.wav" -RefText "точная дословная транскрипция"`.
3. Откройте `voice_clone_and_synthesize_ru.json`, выберите WAV в **Load Audio**.
4. В `ref_text` укажите точную дословную транскрипцию, включая произнесённые междометия; не описание записи.
5. Задайте новый `profile_name`, персонажа, стиль, `language=Russian`, `clone_mode=icl`.
6. Только после проверки разрешения включите `consent_confirmed=true` и Queue.
7. Обновите список: `Invoke-RestMethod -Method Post http://127.0.0.1:8020/admin/reload-voices`, затем снова Queue для Voice Selector.

## Установка, обновление и удаление наших нод

На этой машине использовано безопасное копирование: junction был заблокирован средой. Перед изменением всегда проверяйте `-WhatIf`:

```powershell
.\scripts\install-comfyui-nodes.ps1 -ComfyUIPath "D:\Folder\ia\ComfyUI_windows_portable" -Mode Copy -WhatIf
.\scripts\install-comfyui-nodes.ps1 -ComfyUIPath "D:\Folder\ia\ComfyUI_windows_portable" -Mode Copy -ReplaceExisting
.\scripts\test-comfyui-integration.ps1 -SkipSynthesis
```

При `-ReplaceExisting` старая копия перемещается в `ComfyUI\.qwen_tts_api_nodes-backups`, то есть вне сканируемого `custom_nodes`. Uninstall удаляет только цель, подтверждённую marker-файлом:

```powershell
.\scripts\uninstall-comfyui-nodes.ps1 -ComfyUIPath "D:\Folder\ia\ComfyUI_windows_portable" -WhatIf
.\scripts\uninstall-comfyui-nodes.ps1 -ComfyUIPath "D:\Folder\ia\ComfyUI_windows_portable"
```

## Безопасное обновление ComfyUI

1. Остановите ComfyUI и backend проектными скриптами.
2. Сохраните `ComfyUI\user`, собственные workflows и локальный конфиг проекта.
3. Откройте официальный release, проверьте asset и SHA256. Не распаковывайте новый archive поверх работающей папки вслепую — сначала используйте отдельную папку.
4. Для текущей Portable Manager ставится только из её `manager_requirements.txt`; не накладывайте старый отдельный Manager.
5. Подключите наши ноды installer-скриптом, запустите `test-install.ps1`, затем чистый health workflow.
6. Не переносите случайные checkpoints и сторонние custom nodes до проверки чистого запуска.

## Диагностика

- **backend unavailable**: проверьте `.\status.ps1`, `http://127.0.0.1:8020/health` и `logs/server.err.log`.
- **missing nodes**: остановите ComfyUI, переустановите наши ноды, перезапустите и выполните integration test.
- **IMPORT FAILED / ModuleNotFoundError**: читайте `logs/comfyui.err.log`; не устанавливайте `qwen-tts`, новый torch или transformers в ComfyUI Python.
- **timeout**: проверьте, не идёт ли первая загрузка модели; timeout по умолчанию 900 с. Не запускайте второй workflow параллельно.
- **CUDA OOM**: остановите только проектные сервисы, проверьте `nvidia-smi`, затем используйте CPU-конфиг. Не завершайте внешние процессы.
- **voice отсутствует**: обновите `/v1/voices`, проверьте точный ID и `reference.wav`; Voice Selector вернёт понятное сообщение.
- **порт 8188 занят**: launcher остановится, не трогая процесс. Измените только локальный config либо освободите порт вручную после идентификации владельца.
- **Manager предупреждает о `matrix-nio`**: это необязательная matrix-sharing функция; для Qwen-нód пакет не нужен.

Проверка отсутствия второй модели: в `object_info` наши ноды импортируются за 0,0 с, `qwen_tts` отсутствует в embedded Python, а после on-demand synthesis `/health` снова показывает `model_loaded=false` и VRAM возвращается к базовому уровню ComfyUI.
