# Локальные профили актрис

Результат находится вне Git в `local_voice_samples/readytouseprofiles`, а рабочие копии — в `voice_library/profiles`. Это намеренно: исходные WAV, подготовленные references и синтезированные аудио не публикуются вместе с кодом.

## Состав

| Семейство | Стили |
|---|---|
| Ольга Плетнёва | neutral, happy, sad, angry, tense |
| Ольга Зубкова | neutral, happy, sad, angry |
| Елена Шульман | neutral, happy, sad, angry, whisper, tense |
| Лина Иванова | neutral, happy, sad, angry, tense |
| Ирина Киреева | neutral, happy, sad, angry, tense |
| Вероника Саркисова | neutral, happy, sad, angry, tense |
| Элиза Мартиросова | neutral, happy, sad, angry, tense |
| Лариса Некипелова | neutral, happy, sad, angry |

Всего: 39 profiles. Все references приведены к mono PCM16 24 kHz и прошли автоматическую проверку чтения, длительности, конечных samples и clipping.

## Структура

```text
local_voice_samples/readytouseprofiles/
  profiles/<family>/<style>/reference.wav
  prepared_references/
  examples/
  catalog.json
  validation_report.json
  README_RU.md
```

Neutral-профиль считается основным портфолио-голосом и назначается персонажу. Emotion Router выбирает другие стили того же `character_name`.

## Примеры

Для каждой актрисы настоящим Qwen созданы:

- `<family>-neutral.wav`;
- `<family>-happy-router.wav`;
- `<family>-emotion-router-4-segments.wav`.

Всего 24 examples; все mono PCM16 24 kHz, invalid = 0, clipping = 0. Четырёхсегментные примеры используют neutral → happy → sad → angry по тому же шаблону, который дал лучший результат для Димы.

Искомые готовые файлы «я нейтральная / радостная / грустная / злая» — это восемь `*-emotion-router-4-segments.wav` в `local_voice_samples/readytouseprofiles/examples`. Они уже сгенерированы; текущая доработка Router не перезаписывает портфолио и не запускает массовый synthesis.

## Ограничения качества

Автоматические ASR и speaker-similarity оценки помогали выбирать и размечать найденные записи, но не заменяют прослушивание. Особого ручного контроля требуют proxy-кандидаты `irina_kireeva_sad`, `veronika_sarkisova_sad` и `larisa_nekipelova_sad`. У Ларисы отдельный portfolio demo был невербальным, поэтому neutral выбран из speaker-matched диалога Ирен Адлер.

Пользователь подтвердил согласие актрис на использование голосов. Это согласие на голос не автоматически определяет права на каждую конкретную исходную запись; перед публичным распространением или коммерческой публикацией нужно отдельно проверить права на recording/source material.
