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

Поддерживаемые styles: `neutral`, `soft`, `whisper`, `breathy`, `happy`, `sad`, `angry`, `tense`, `pleasure`, `intimate`.

`pleasure` и `intimate` выбирают соответственно `<family>_pleasure` и `<family>_intimate`. Это самостоятельные references; Router не смешивает их из happy/soft/breathy. При отсутствии profile действует обычный family neutral fallback.

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

Backend остаётся source of truth. ComfyUI не импортирует backend package и сохраняет лёгкую mirror implementation; обязательные parity tests прогоняют общий корпус через оба parser и сравнивают segments, warnings и полный allowlist. Это защищает семантику от тихого drift без установки Qwen/backend зависимостей в embedded Python.

## Voice fallback

Request voice определяет character family. Neutral narration выбирает `<family>_neutral`, даже если в запросе ошибочно указан эмоциональный профиль. Для dialogue style порядок такой:

1. `<family>_<style>`;
2. `<family>_neutral`;
3. настроенный безопасный fallback profile.

Если не существует ни одного безопасного профиля, API возвращает понятный JSON 422 вместо выбора произвольного голоса.

## Безопасные предупреждения

Metadata может содержать коды `unknown_voice_tag_neutral_fallback`, `malformed_voice_tag_neutral_fallback`, `unterminated_voice_tag_removed`, `multiple_voice_tags_last_wins`, `voice_tag_ignored_no_following_quoted_dialogue`, `empty_dialogue_ignored`, `unclosed_dialogue_treated_as_neutral`. В них нет пользовательского текста.

## Проверки

Unit/API/Comfy tests покрывают neutral narration, все десять styles, pleasure/intimate на той же и новой строке, tagged/untagged dialogue, reset, несколько реплик, многострочную цитату, вложенные русские кавычки, Unicode/emoji, escaped quotes, tag перед narration, unknown/malformed/empty/unterminated tags, несколько tags, пустую и незакрытую цитату, dynamic style availability, отсутствие family neutral, пустой API input, отсутствие service tags в worker text и parity backend/Comfy parser.

Старые реальные WAV с четырьмя явно размеченными сегментами остаются пригодны как портфолио, но сами по себе не считаются подтверждением нового quote-aware контракта. Отдельный реальный CPU/Qwen smoke 2026-08-08 дал 3.84 s mono WAV 24 kHz и metrics: `neutral narration → happy dialogue → neutral narration`, соответствующие `test_ru_dima_neutral → test_ru_dima_happy → test_ru_dima_neutral`, warnings = 0, failed = 0. Новые модели не скачивались, массовая генерация не выполнялась.

`pauses.sentence_ms` и `pauses.paragraph_ms` сохранены как зарезервированные compatibility keys, но synthesis их сейчас не применяет; отдельный NLP-сегментатор в этот hardening не добавлялся. Используется `pauses.segment_ms`. Название `crossfade_ms` — legacy compatibility key: текущий stitch применяет edge fades вокруг вставленной тишины, а не overlap crossfade.

## Voice library и prompt cache

Active loader сканирует только `voice_library/profiles` и игнорирует legacy directories с `.backup-` в имени. Новые overwrite backups сохраняются в `voice_library/backups/<character>/<style>-<timestamp>` и не могут попасть в `/v1/voices`, `resolve` или `find_style`.

Prompt cache по-прежнему локально инвалидируется по конкретному reference, но identity теперь включает canonical path, `st_mtime_ns`, file size, `ref_text` и normalized `clone_mode`. Исправление transcript или переключение ICL/x-vector после reload пересоздаёт prompt; неизменный следующий request использует cache hit.
