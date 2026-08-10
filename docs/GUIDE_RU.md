# Qwen3-TTS: единое руководство

Проект — локальный TTS engine для Windows. Backend на `127.0.0.1:8020` единолично загружает Qwen, хранит voice library и применяет весь quality pipeline. ComfyUI — Voice Lab и панель настройки. OpenAI-compatible клиенты, включая будущее подключение SillyTavern, могут оставаться простыми клиентами.

## Установка и запуск

Используется Python 3.12 и только проектная `.venv`. Если окружение уже существует и `pip check` проходит, повторная установка не нужна.

```powershell
.\scripts\install.ps1 -Python "C:\path\to\Python312\python.exe" -TorchVariant CPU
.\start.ps1
.\status.ps1
.\stop.ps1
```

Backend + проектная ComfyUI:

```powershell
.\scripts\start-tts-and-comfyui.ps1
```

Для двойного щелчка есть `start-tts.bat` и `start-tts-and-comfyui.bat`. Backend слушает только localhost. ComfyUI обычно доступна на `http://127.0.0.1:8188`.

Реализованные режимы: `cpu`, `cuda`, `cuda_on_demand`, `auto`. На RTX 2070 Super безопасным проверенным CUDA-вариантом остаётся FP32/SDPA; этот quality PR не добавляет hybrid/offload.

## API и модели

Основные endpoints:

- `GET /health`, `/v1/models`, `/v1/voices`, `/metrics`;
- `POST /v1/audio/speech`, `/v1/audio/voice-clone`, `/admin/reload-voices`;
- `GET /admin/runtime-settings`;
- `PUT /admin/runtime-settings`.

Модельные aliases:

- `tts-1-ru` — generic/default alias, следующий сохранённой active backend model;
- `tts-1-ru-fast` — явная 0.6B Fast;
- `tts-1-ru-quality` — явная 1.7B Quality.

Если пользователь ничего не сохранял, `tts-1-ru` следует обычному `models.default` из config. Явные Fast/Quality всегда переопределяют active default. Одновременно resident остаётся только одна heavy model; первый запрос после смены является cold request.

Минимальный запрос:

```json
{
  "model": "tts-1-ru",
  "voice": "clone:some_voice",
  "input": "Она открыла Visual Studio Code и сказала hello.",
  "response_format": "mp3"
}
```

Даже без custom fields backend применяет сохранённые quality defaults.

## Приоритет настроек

Единый порядок:

1. explicit request override;
2. optional voice-specific override, если он когда-либо будет добавлен;
3. сохранённые active backend runtime settings;
4. статические defaults из `config/config.example.yaml` и `config.local.yaml`.

Voice profile отвечает на вопрос «кто говорит»: reference WAV, точный `ref_text`, character/style, clone mode и identity. Runtime settings отвечают «как синтезировать»: модель, generation preset, normalization, multilingual routing, chunking и edge padding. Эти сущности не смешиваются.

## Voice Lab в ComfyUI

Canonical workflow:

`integrations/comfyui/example_workflows/voice_profile_from_wav_ru.json`

Схема:

```text
Qwen TTS Server
  → Qwen TTS Runtime Settings
      → Qwen TTS Clone Voice ← Load Audio
      → Qwen TTS Synthesize ← voice_id
          → Preview Audio
```

Порядок работы:

1. В `Load Audio` выберите только разрешённый reference WAV.
2. В `Clone Voice` вставьте точную дословную транскрипцию `ref_text`.
3. Задайте profile/character/style. Для обычного русского cloning используйте `Russian` и `icl`.
4. `consent_confirmed` и `overwrite` по умолчанию выключены; включайте их только осознанно.
5. Сразу прослушайте результат через Synthesize → Preview Audio.
6. В Runtime Settings выберите модель и quality defaults. `apply_and_save=false` только читает текущие значения; `true` сохраняет их локально.
7. После сохранения ComfyUI можно закрыть: backend использует те же defaults для последующих API/SillyTavern-запросов.

Runtime settings сохраняются в игнорируемом `runtime/tts-settings.json` и не предназначены для Git. Пользовательские WAV и voice profiles также не следует коммитить.

## Quality pipeline

Все общие quality safeguards language-agnostic: pronunciation dictionaries, semantic chunking, stitching без внутренней тишины, speed processing, edge fade и outer padding одинаково применяются к Russian и English spans. Различаются только language routing и language-specific normalization; отдельного ухудшенного английского pipeline нет.

Рекомендуемая база:

- `Stable Russian`;
- `Full Russian normalization`;
- `Auto Russian + English`;
- `Semantic / prosody-aware chunking`;
- leading silence 100 мс;
- trailing silence 150 мс.

`Stable Russian` использует ограниченный preset параметров, реально поддерживаемых Qwen. Full normalization безопасно обрабатывает пробелы, пунктуацию, ограниченные числа/время/дроби и однозначные случаи `ё`. Безусловной замены `все → всё` нет.

Pronunciation dictionary предназначен для явных исключений (`Qwen = куэн`), а не для маскировки системных дефектов chunking. Сохранённый словарь действует на все клиенты; request-level replacements имеют приоритет.

## Русский + английский

В режиме `auto` backend разделяет смешанный текст только по границам Cyrillic/Latin spans. Русские spans передаются Qwen с `language="Russian"`, английские — с `language="English"`. Английские слова не транслитерируются кириллицей, а voice profile остаётся тем же.

Пример:

```text
Она открыла | Visual Studio Code | и сказала | hello.
Russian       English              Russian      English
```

Фрагменты собираются в одну реплику небольшим overlap join без фиксированных пауз. Русский reference не гарантирует идеальный английский accent — это ограничение cross-language cloning самой модели.

## Prosody и chunking

Semantic chunking:

- не дробит короткие предложения;
- сначала выбирает конец предложения;
- затем `;`/`:`, запятую/тире и только потом обычный пробел;
- никогда не режет слово ради достижения точного числа символов;
- сохраняет punctuation;
- применяется до language spans так, чтобы смешанная речь оставалась управляемой.

Корпус с `Она пришла`, `Это она`, `Он и она` защищает отсутствие искусственной границы внутри слова. Это устраняет backend-side разрывы, но не может детерминированно исправить любой стохастический фонетический дефект Qwen. Тяжёлый ASR и недоказанный automatic retry намеренно не добавлены.

## Тишина и сборка аудио

Внутренние chunks, language spans и emotion/style segments соединяются без массивов нулей. Небольшой overlap/crossfade предотвращает clicks, но не создаёт fixed silence gap.

После полной сборки выполняются speed processing и небольшой edge fade. Только затем один раз добавляются leading/trailing silence на абсолютных краях готовой реплики. Поэтому их длительность не меняется при `speed != 1` и они никогда не оказываются между словами или segments.

## Эмоции и styles

Voice families могут иметь profiles `neutral`, `soft`, `whisper`, `breathy`, `happy`, `sad`, `angry`, `tense`, `pleasure`, `intimate`. Повествование остаётся neutral. `[voice:style]` действует только на следующую полную ASCII-цитату `"..."`, после чего сбрасывается. Backend выбирает style profile той же семьи с fallback на neutral.

## SillyTavern и другие клиенты

Исходный код SillyTavern проект не меняет. Для будущего/ручного OpenAI Compatible подключения достаточно:

- endpoint `http://127.0.0.1:8020/v1/audio/speech`;
- model `tts-1-ru`;
- voice ID нужного neutral profile.

Такой dumb client автоматически получает active model, Stable Russian, normalization, pronunciation defaults, RU/EN routing, semantic chunking, emotion routing и edge padding. Чтобы принудительно выбрать модель для конкретного клиента, используйте explicit `tts-1-ru-fast` или `tts-1-ru-quality`.

## Проверка и диагностика

После настройки:

1. проверьте `/health`: active/default model и runtime settings;
2. откройте `/v1/voices` и убедитесь, что profile найден;
3. синтезируйте короткий русский пример, mixed RU/EN и эмоциональную цитату;
4. прослушайте начало/конец, переход RU/EN, слово «она» в разных контекстах и отсутствие механических пауз;
5. проверьте `/metrics`: фактическую модель, languages, chunking и padding.

Типичные проблемы:

- первый запрос после смены модели медленный — это cold load;
- отсутствующий voice ID — проверьте profile metadata/reference WAV и `/v1/voices`;
- CUDA OOM — используйте CPU или существующий conservative auto/on-demand режим;
- плохой accent английского — ограничение reference/model, а не повод транслитерировать текст;
- редкий неправильный вариант «она» при целых spans — стохастическое ограничение Qwen; сравните несколько ручных генераций и качество reference/ref_text.

Проект не обещает автоматический выбор «лучшего» стохастического дубля без надёжного ASR/scorer. Финальное качество voice profile всегда подтверждается прослушиванием.
