# Фактический аудит Emotion Router

Дата актуализации: 2026-08-08.

## Контракт

- озвучивается весь входной текст;
- повествование всегда `neutral`;
- реплика распознаётся только по паре обычных ASCII-кавычек `"..."`;
- `[voice:style]` применяется лишь к непосредственно следующей полной реплике;
- после закрывающей кавычки стиль сбрасывается в `neutral`;
- реплика без тега neutral;
- неизвестные, malformed, лишние и незакрытые service tags удаляются и никогда не передаются worker;
- незакрытая цитата безопасно трактуется как neutral narration.

Поддерживаемые styles: `neutral`, `soft`, `whisper`, `breathy`, `happy`, `sad`, `angry`, `tense`.

```text
Она остановилась. [voice:tense] "Ты слышал?" Она замерла.
[voice:whisper] "Тише." После реплики повествование снова neutral.
```

Внутри внешней ASCII-реплики допускаются русские кавычки `«...»`, Unicode, `ё`, emoji, переносы строк и экранированные `\"`.

## Реализация

- scanner, stripping и безопасные warning codes: `src/qwen3_tts_st/emotion.py`;
- orchestration: `TTSService.synthesize` в `src/qwen3_tts_st/service.py`;
- family/style fallback: `src/qwen3_tts_st/voices.py`;
- очистка/direct-speech: `src/qwen3_tts_st/preprocess.py`;
- merge: `src/qwen3_tts_st/audio.py`;
- HTTP entry: `POST /v1/audio/speech`;
- Comfy preview: `QwenTTSEmotionScriptNode`.

## Voice fallback

Request voice определяет character family. Neutral narration выбирает `<family>_neutral`, даже если в запросе ошибочно указан эмоциональный профиль. Для dialogue style порядок такой:

1. `<family>_<style>`;
2. `<family>_neutral`;
3. настроенный безопасный fallback profile.

Если не существует ни одного безопасного профиля, API возвращает понятный JSON 422 вместо выбора произвольного голоса.

## Безопасные предупреждения

Metadata может содержать коды `unknown_voice_tag_neutral_fallback`, `malformed_voice_tag_neutral_fallback`, `unterminated_voice_tag_removed`, `multiple_voice_tags_last_wins`, `voice_tag_ignored_no_following_quoted_dialogue`, `empty_dialogue_ignored`, `unclosed_dialogue_treated_as_neutral`. В них нет пользовательского текста.

## Проверки

Unit/API/Comfy tests покрывают neutral narration, tagged/untagged dialogue, tag на той же и новой строке, reset, несколько реплик, многострочную цитату, вложенные русские кавычки, Unicode/emoji, escaped quotes, tag перед narration, unknown/malformed/empty/unterminated tags, несколько tags, пустую и незакрытую цитату, отсутствие style profile, отсутствие family neutral, пустой API input и отсутствие service tags в worker text.

Старые реальные WAV с четырьмя явно размеченными сегментами остаются пригодны как портфолио, но сами по себе не считаются подтверждением нового quote-aware контракта. Отдельный реальный CPU/Qwen smoke 2026-08-08 дал 3.84 s mono WAV 24 kHz и metrics: `neutral narration → happy dialogue → neutral narration`, соответствующие `test_ru_dima_neutral → test_ru_dima_happy → test_ru_dima_neutral`, warnings = 0, failed = 0. Новые модели не скачивались, массовая генерация не выполнялась.

`pauses.sentence_ms` и `pauses.paragraph_ms` присутствуют в config, однако при объединении Router parts service использует `pauses.segment_ms`. Название `crossfade_ms` историческое: текущий stitch применяет fades вокруг вставленной тишины, а не музыкальный overlap.
