# Русские reference WAV, эмоциональные профили и ComfyUI

Дата фактической проверки: 2026-08-06. Это руководство относится к текущему checkout и установленной внутри проекта ComfyUI Portable 0.30.0.

## Что подготовлено

Основной открытый источник — [langswap/dialogs-ru-emotional-conversations](https://huggingface.co/datasets/langswap/dialogs-ru-emotional-conversations), revision `e25ba617b2b56bd1dbf255d3905c51bd8da3d31f`. В нём раздельно представлены три профессиональных русскоязычных актёра (`D`, `M`, `S`), студийные mono PCM16 WAV 44,1 кГц, дословные тексты и эмоциональные метки. [Лицензия OpenRAIL](https://huggingface.co/datasets/langswap/dialogs-ru-emotional-conversations/blob/e25ba617b2b56bd1dbf255d3905c51bd8da3d31f/LICENSE.md) прямо сообщает о письменном информированном согласии актёров на открытую публикацию и законное использование, включая коммерческое, с ограничениями самой лицензии.

Локально подготовлено:

- 25 скачанных WAV;
- 21 selected sample;
- 4 избыточных rejected sample;
- 3 независимые voice families: `test_ru_dima`, `test_ru_masha`, `test_ru_sveta`;
- 15 временных primary profiles;
- полная тестовая семья `TestRuDima`: `neutral`, `happy`, `sad`, `angry`, `whisper`, `tense`;
- `soft` и `breathy` не заполнены: надёжные same-speaker references не найдены;
- `tense` у Dima — честно обозначенный proxy из исходной метки `fear`, confidence `medium`.

Голоса между семьями не смешиваются. Для одного персонажа все style-профили должны иметь одинаковый `character_name` и происходить от одного говорящего.

## Где находятся локальные файлы

Всё аудио намеренно исключено из Git:

```text
local_voice_samples/
  downloaded/       исходные файлы и metadata источника
  prepared/         byte-identical подготовленные копии
  transcripts/      transcript каждого sample
  metadata/         provenance и объективные audio metrics
  selected/         voice_family / emotion / primary|backup
  rejected/         резервная папка
  reports/          selected, rejected, quality, created profiles
  selection_manifest.json
```

Сгенерированные результаты находятся в `artifacts/audio-tests/` и тоже игнорируются. Проверка `git check-ignore` обязательна перед commit.

## Воспроизводимая подготовка

```powershell
.\.venv\Scripts\python.exe scripts\prepare_voice_samples.py `
  --manifest local_voice_samples\selection_manifest.json `
  --workspace local_voice_samples
```

Скрипт:

1. сверяет `speaker_id` и исходную emotion с CSV;
2. запрещает выход source path за пределы download-root;
3. копирует пригодные WAV без resampling, normalization и trimming;
4. пишет точную dataset-транскрипцию в UTF-8;
5. измеряет sample rate, channels, subtype/bit depth, duration, peak, RMS, clipping fraction, edge silence и SHA256;
6. сортирует samples по семье и mapped emotion;
7. сохраняет причины rejected/unavailable состояний.

Исходники уже имеют подходящий mono PCM16 44,1 кГц формат, поэтому преобразование только ухудшило бы воспроизводимость. Автоматическое trimming не применялось, особенно для whisper. Все выбранные клипы находятся примерно в диапазоне 4–9,4 с, кроме backup fear 3,1 с; объективный clipping не обнаружен.

`validate_audio` использует грубую edge-SNR эвристику. Её предупреждение не равно доказанному шуму: речь может начинаться или заканчиваться внутри измеряемого края. Решение о качестве, точности транскрипции и силе эмоции принимается только после прослушивания.

## Создание временных test profiles

Только после проверки лицензии и consent:

```powershell
.\.venv\Scripts\python.exe scripts\create_test_voice_profiles.py `
  --workspace local_voice_samples `
  --library voice_library `
  --consent-confirmed
```

Скрипт берёт только `selection_status=primary`. Повторный запуск без `--overwrite` безопасно откажется заменять существующие style-папки. `--overwrite` создаёт backup средствами `VoiceLibrary`.

Основные IDs:

```text
clone:test_ru_dima_neutral
clone:test_ru_dima_happy
clone:test_ru_dima_sad
clone:test_ru_dima_angry
clone:test_ru_dima_whisper
clone:test_ru_dima_tense
```

Аналогично созданы `test_ru_masha_{neutral,happy,sad,angry,whisper}` и `test_ru_sveta_{neutral,happy,sad,angry}`. Профили временные и не означают, что качество уже одобрено пользователем.

## Запуск backend и ComfyUI

Самый простой способ — двойной щелчок по `start-tts-and-comfyui.bat`. Окно BAT должно оставаться открытым во время работы. Закрытие BAT-окна, отдельного окна ComfyUI или аварийное завершение одного сервиса заставляет скрытый watcher остановить оба подтверждённых проектных процесса.

Из PowerShell:

```powershell
.\scripts\start-tts-and-comfyui.ps1
```

Проверочные URL:

- backend health: `http://127.0.0.1:8020/health`;
- backend voices: `http://127.0.0.1:8020/v1/voices`;
- backend Swagger: `http://127.0.0.1:8020/docs`;
- ComfyUI: `http://127.0.0.1:8188`.

Оба сервера должны оставаться только на `127.0.0.1`.

## Где найти ноды

В ComfyUI откройте Node Library и категорию `Qwen TTS API`:

- `Qwen TTS Server` — backend URL, timeout, model, format;
- `Qwen TTS Health` — health, device, model, voices, queue, resources;
- `Qwen TTS Models` — `/v1/models`;
- `Qwen TTS Voice Selector` — проверка выбранного voice ID;
- `Qwen TTS Clone Voice` — регистрация разрешённого AUDIO + transcript;
- `Qwen TTS Emotion Script` — segments/mapping preview;
- `Qwen TTS Synthesize` — реальный запрос и выход `AUDIO`.

ComfyUI-ноды не импортируют `torch`, `transformers` или `qwen_tts`. Единственная Qwen-модель принадлежит отдельному backend-процессу.

## Как загрузить workflow

Перетащите JSON на canvas либо используйте штатное открытие workflow. Файлы:

- `voice_profile_from_wav_ru.json`;
- `emotion_router_test_ru.json`;
- `compare_voice_samples_ru.json`;
- `text_to_speech_with_emotions_ru.json`.

Все они используют только зарегистрированные Qwen-ноды и штатные `LoadAudio`, `PreviewAudio`, `SaveAudio`. JSON, node IDs и links проверяются unit-тестом; типы нод дополнительно сверены с реальным `/object_info` ComfyUI 0.30.0.

## Создание neutral profile через ComfyUI

1. Откройте `voice_profile_from_wav_ru.json`.
2. В `LoadAudio` выберите разрешённый mono WAV с одним говорящим.
3. В `Qwen TTS Clone Voice` вставьте точную дословную `ref_text`, включая реально произнесённые слова.
4. Укажите `profile_name`, например `my_hero_neutral`.
5. Укажите общий `character_name`, например `MyHero`.
6. Выберите style `neutral`, language `Russian`, clone mode `icl`.
7. Включите `consent_confirmed` только если разрешение действительно есть.
8. `overwrite=false` оставляет существующий профиль нетронутым.
9. Нажмите Queue/Run. Связанный Synthesize заставляет clone-node выполниться и возвращает тестовый AUDIO.
10. Прослушайте `PreviewAudio`; voice ID будет `clone:my_hero_neutral`.

## Создание эмоциональной семьи

Повторите clone workflow для каждого WAV одного и того же говорящего:

| style | profile_name | character_name |
|---|---|---|
| neutral | `my_hero_neutral` | `MyHero` |
| happy | `my_hero_happy` | `MyHero` |
| sad | `my_hero_sad` | `MyHero` |
| angry | `my_hero_angry` | `MyHero` |
| whisper | `my_hero_whisper` | `MyHero` |
| tense | `my_hero_tense` | `MyHero` |

Именно `character_name`, а не общий префикс строки, образует фактическую voice family в backend. Для наиболее стабильного персонажа references должны принадлежать одному говорящему, иметь близкие микрофон/комнату/громкость и содержать одно устойчивое состояние.

После API-clone библиотека перечитывается автоматически. Если metadata добавлялась вручную, вызовите `POST /admin/reload-voices`, затем снова выполните `Qwen TTS Health` или `Voice Selector`.

## Mapping и fallback

В `Qwen TTS Emotion Script` mapping нужен для наглядного preview выбранных profiles:

```json
{
  "neutral": "clone:my_hero_neutral",
  "happy": "clone:my_hero_happy",
  "sad": "clone:my_hero_sad",
  "angry": "clone:my_hero_angry"
}
```

Реальный backend получает neutral/base `voice` из `Qwen TTS Synthesize`, определяет его `character_name` и для каждого тега ищет same-character profile с нужным `style`. Поэтому mapping в preview и фактическая семья должны совпадать.

Fallback устроен так:

1. определяется neutral-профиль выбранной character family;
2. для реплики ищется same-family style;
3. если style отсутствует, используется family neutral;
4. если отсутствует и family neutral, используется `voices.fallback_profile`.

Практически request voice следует задавать neutral. Неизвестные, malformed и не относящиеся к цитате tags удаляются до worker, а речь остаётся neutral.

## Emotion Script

```text
Сейчас я говорю спокойно. [voice:happy] "А теперь я очень рад!"
Повествование снова neutral. [voice:sad] "Мне стало грустно."
[voice:angry] "И наконец я разозлился!"
```

Поддерживаются `neutral`, `soft`, `whisper`, `breathy`, `happy`, `sad`, `angry`, `tense`, `pleasure`, `intimate`. Pleasure и intimate требуют отдельных same-speaker references; существующие families без них корректно используют neutral fallback. Повествование и реплики без tag neutral; tag применяется только к непосредственно следующей полной ASCII-цитате и после неё сбрасывается. `Qwen TTS Emotion Script` показывает JSON segments, clean text и recognized styles. Подключите `normalized_script` к optional input `emotion_script` ноды Synthesize и нажмите Queue.

Backend синтезирует сегменты последовательно, добавляет настроенную паузу, выполняет короткие edge fades, ресемплирует при необходимости и возвращает один WAV/AUDIO. Служебные теги worker не получает.

## Сравнение нескольких reference WAV

`compare_voice_samples_ru.json` содержит три независимые цепочки:

1. выберите WAV A/B/C;
2. вставьте отдельную точную транскрипцию для каждого;
3. используйте разные `profile_name` и `character_name`, чтобы samples не перезаписали друг друга;
4. подтвердите consent;
5. оставьте одну и ту же контрольную фразу во всех трёх Synthesize;
6. Queue сохранит результаты раздельно через `SaveAudio` в `ComfyUI/output/qwen_compare`.

Сравнивайте разборчивость, сходство тембра, русский акцент, стабильность высоты, артефакты, паузы и соответствие эмоции. Самый громкий sample не обязательно лучший.

## Проверенные результаты 2026-08-06

- backend `/health`, `/v1/models`, `/v1/voices`: OK;
- 15 временных profiles видны, `reference_available=true`;
- real Qwen neutral: mono PCM16 24 кГц, 4,72 с;
- real Qwen happy Router: mono PCM16 24 кГц, 4,32 с;
- real Qwen 4-segment Router: 7,32 с, `completed=3`, `failed=0`;
- зафиксированный порядок profiles: neutral, happy, sad, angry;
- ComfyUI prompt `b371b073-570e-4700-a56e-05f2746e0aaa`: `success`;
- ComfyUI PreviewAudio: FLAC mono 24 кГц, 7,64 с;
- все результаты находятся в игнорируемом `artifacts/audio-tests/`.

Техническая успешность не доказывает субъективное качество или сходство. Пользователю нужно прослушать primary/backup references и outputs.

## Типичные ошибки

| Ошибка | Причина | Что делать |
|---|---|---|
| backend unavailable | backend не запущен или endpoint не `127.0.0.1:8020` | проверить BAT, `/health`, firewall только для localhost |
| requested voice is not available | опечатка или library не перечитана | выполнить Health/Selector или `/admin/reload-voices` |
| consent required | checkbox не включён | включать только при реальном разрешении |
| profile already exists | `overwrite=false` | дать новый ID либо осознанно включить overwrite |
| ref_text mismatch | transcript не совпадает с WAV | переслушать и исправить дословно |
| emotion звучит как neutral | style отсутствует в семье | создать same-character profile или проверить spelling style |
| tag не сработал | tag не стоит непосредственно перед полной ASCII-цитатой | использовать `[voice:happy] "Реплика."` |
| щелчок на границе | reference/output требует прослушивания или edge fade мал | проверить source, затем осторожно изменить legacy-key `pauses.crossfade_ms`; это fade вокруг паузы, не overlap |

## Что делать, если reference плохой

Не нормализуйте и не фильтруйте вслепую. Сначала замените reference на более чистый same-speaker clip с точной транскрипцией. Создайте новый profile ID и сравните одинаковую фразу. Только после прослушивания можно применить `overwrite=true`; VoiceLibrary создаст backup прежней style-папки.

## Что ещё требует пользователя

- прослушать primary и backup WAV;
- подтвердить точность dataset-транскрипций на слух;
- выбрать лучшие семьи и эмоции;
- оценить, подходит ли `fear → tense`;
- проверить сходство, естественность, русский акцент и стыки;
- предоставить собственный разрешённый WAV, если нужен конкретный персонаж.

Интеграция с SillyTavern только спроектирована. Никакие изменения в SillyTavern не выполнялись. См. [SILLYTAVERN_INTEGRATION_PLAN_RU.md](SILLYTAVERN_INTEGRATION_PLAN_RU.md).
