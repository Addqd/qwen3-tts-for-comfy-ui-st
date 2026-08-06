# План будущей интеграции с SillyTavern

Дата сверки upstream: 2026-08-06. Интеграция не реализовывалась. Подготовлен только технический план.

## Что уже совместимо

Текущий штатный OpenAI-compatible TTS provider SillyTavern отправляет запрос своему proxy `/api/openai/custom/generate-voice`, а proxy пересылает `input`, `response_format`, `voice`, `speed`, `model` на полный `provider_endpoint`. Это подтверждено [frontend provider source](https://github.com/SillyTavern/SillyTavern/blob/release/public/scripts/extensions/tts/openai-compatible.js) и [server proxy source](https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/openai.js).

Будущая настройка:

```text
Provider: OpenAI-compatible
Provider endpoint: http://127.0.0.1:8020/v1/audio/speech
Model: tts-1-ru
Available voices: clone:<character>_neutral,...
Speed: 1
```

Backend уже принимает эту форму и умеет MP3. SillyTavern-source, settings и extensions в этой задаче не изменялись.

## Назначение персонажей

- каждому SillyTavern character назначается neutral voice ID;
- этот neutral profile несёт backend `character_name`, то есть voice family;
- одноимённые family profiles с styles выбираются Router внутри backend;
- разные персонажи получают разные neutral voice IDs;
- missing request voice использует глобальный config fallback;
- missing style использует request base profile, поэтому base должен быть neutral.

## Где должен работать Router

Router остаётся только в backend. SillyTavern не должен загружать Qwen, склеивать WAV или знать путь к reference audio. Он передаёт один text и один neutral voice. Backend:

1. очищает текст;
2. удаляет `[voice:*]` tags;
3. делит на эмоциональные segments;
4. выбирает same-character profiles;
5. последовательно синтезирует;
6. возвращает один MP3/WAV.

Штатные quote/action filters SillyTavern применяются до provider call согласно [официальной TTS-документации](https://docs.sillytavern.app/extensions/tts/). Нужно заранее решить, откуда появляются voice-tags: вручную, regex/formatter или LLM prompt. Непроверенный LLM-output нельзя доверять как profile ID; backend принимает только фиксированные styles.

## Очередь и воспроизведение

- SillyTavern сохраняет штатную auto-generation/playback очередь;
- backend `max_concurrent=1` сериализует inference;
- `max_waiting` и wait timeout предотвращают неограниченную очередь;
- итоговый audio blob возвращается proxy и проигрывается штатным player;
- одинаковый ответ не следует отправлять повторно, пока предыдущий запрос активен.

Текущий backend не имеет endpoint отмены уже выполняющегося persistent generation. До реальной интеграции нужно спроектировать request/job ID и безопасную отмену только собственного job, либо принять, что UI cancel прекращает ожидание, а backend завершает текущий synthesis. Это открытый пункт.

## Этап реального внедрения

1. Сделать backup только SillyTavern settings.
2. Проверить текущую установленную версию и provider UI.
3. Запустить backend и проверить `/health`/`/v1/voices`.
4. Внести localhost endpoint через UI, не правкой исходников.
5. Назначить neutral voice одному тестовому character.
6. Проверить ручную короткую реплику без tags.
7. Проверить четыре tags и один итоговый audio blob.
8. Проверить auto-play, очередь, повторные сообщения и fallback.
9. Только затем масштабировать voice map на других персонажей.

Интеграция с SillyTavern только спроектирована. Никакие изменения в SillyTavern не выполнялись.
