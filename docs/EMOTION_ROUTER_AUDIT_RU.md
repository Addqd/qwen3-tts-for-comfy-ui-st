# Фактический аудит Emotion Router

Дата: 2026-08-06.

## Реализация

- parser и tag stripping: `src/qwen3_tts_st/emotion.py`;
- orchestration: `TTSService.synthesize` в `src/qwen3_tts_st/service.py`;
- family/style lookup: `VoiceLibrary.find_style` в `src/qwen3_tts_st/voices.py`;
- merge: `stitch` в `src/qwen3_tts_st/audio.py`;
- HTTP entry: `POST /v1/audio/speech` в `src/qwen3_tts_st/app.py`;
- Comfy preview/parser: `QwenTTSEmotionScriptNode`;
- Comfy execution: `QwenTTSSynthesizeNode`.

Поддерживаемые styles: `neutral`, `soft`, `whisper`, `breathy`, `happy`, `sad`, `angry`, `tense`.

Parser распознаёт корректную форму `[voice:<identifier>]`. Известный identifier выбирает style, неизвестный становится `neutral`. Тег удаляется из текста сегмента. Malformed tags, например `[voice: happy]`, намеренно не считаются служебными.

## Последовательность

1. HTTP schema проверяет непустой input.
2. `preprocess` удаляет разрешённую разметку и private blocks.
3. `parse_emotion_script` сохраняет порядок и формирует непустые segments.
4. Request voice разрешается; отсутствующий request voice использует глобальный `voices.fallback_profile`.
5. `base.character` образует voice family.
6. Для каждого segment style ищется same-character profile; отсутствующий style использует base profile.
7. Сегмент делится на chunks без перестановки.
8. Каждый chunk отдельно передаётся worker с `language="Russian"`.
9. `stitch` приводит sample rates, добавляет `pauses.segment_ms` и короткие fades на краях.
10. Возвращается один encoded audio response.

Название `crossfade_ms` историческое: текущий `stitch` не делает overlap двух речевых сигналов, а затухает/наращивает края вокруг вставленной тишины. Это снижает риск щелчка, но не является музыкальным overlap-crossfade.

`pauses.sentence_ms` и `pauses.paragraph_ms` присутствуют в config, однако текущий service при объединении Router parts использует только `pauses.segment_ms`. Это известное ограничение, а не скрытая завершённая функция.

## Исправленный дефект

До этого аудита backend regex перечислял только известные styles. Поэтому `[voice:excited]` не распознавался и мог попасть в worker text, хотя Comfy preview уже превращал его в neutral. Regex backend сделан согласованным с Comfy: любой синтаксически корректный identifier распознаётся, неизвестный style нормализуется в neutral, тег удаляется.

## Проверки

`tests/test_emotion_router.py` покрывает:

1. текст без тегов;
2. один neutral segment;
3. несколько эмоций;
4. русский Unicode;
5. теги в одной строке;
6. теги в разных строках;
7. повтор style;
8. неизвестный tag;
9. отсутствующий style profile;
10. отсутствующий request voice и глобальный fallback;
11. пустой API input;
12. смешанную русскую пунктуацию;
13. отсутствие тегов в фактическом worker text;
14. порядок текста и profiles;
15. непустой объединённый WAV.

Worker перехватывается тестом до генерации: четыре тега дают четыре чистых текста и IDs в нужном порядке. Это более надёжное доказательство отсутствия произносимого служебного текста, чем поиск байтов `voice:` внутри encoded WAV.

Реальный backend test дал metadata:

```json
{
  "segments": 4,
  "styles": ["neutral", "happy", "sad", "angry"],
  "voices": [
    "clone:test_ru_dima_neutral",
    "clone:test_ru_dima_happy",
    "clone:test_ru_dima_sad",
    "clone:test_ru_dima_angry"
  ],
  "duration_seconds": 7.32
}
```

Реальный ComfyUI `/prompt` прошёл через Server → Emotion Script → Synthesize → PreviewAudio. `/history` вернул status `success`, четыре clean Russian segments, те же profile IDs и AUDIO 24 кГц длительностью 7,64 с.

## Фактический статус

Router реализован и подтверждён unit-, real-backend- и real-ComfyUI-тестами. Не подтверждены субъективная сила эмоции, качество всех references и отсутствие слышимого шва: это требует пользовательского прослушивания. Отдельного Router endpoint нет; вход всегда `POST /v1/audio/speech`. Mapping Comfy-ноды служит preview, а фактический выбор same-character profile выполняется backend.
