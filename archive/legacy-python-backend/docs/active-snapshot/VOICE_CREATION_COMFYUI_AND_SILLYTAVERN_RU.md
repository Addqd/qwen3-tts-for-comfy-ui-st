# Создание голоса и эмоций: ComfyUI → backend → SillyTavern

Это практический маршрут без копирования модели в ComfyUI и без автоматического изменения SillyTavern. Все голосовые данные хранятся в backend voice library.

## 1. Подготовьте разрешённый reference

Используйте чистый WAV с одним голосом, без музыки, реверберации и чужой речи. Для Base-модели критична точная дословная `ref_text`: паузы можно передать пунктуацией, но нельзя заменять или додумывать слова. ComfyUI-нода сводит AUDIO в mono PCM16, сохраняя исходный sample rate; прямой API clone сохраняет переданный WAV без обязательного resample. `qwen-tts` принимает исходный rate, передаёт его speech tokenizer и внутренне ресемплирует для speaker encoder. Текущий generated output модели имеет 24 kHz, но это не требование к reference.

Официальный API Qwen Base клонирует голос по `ref_audio` и `ref_text`; язык синтеза в проекте всегда передаётся явно как Russian. Используйте только голос и запись, на которые у вас есть разрешение.

## 2. Запустите backend и ComfyUI

Двойным щелчком откройте `start-tts-and-comfyui.bat`. Backend остаётся единственным процессом с Qwen/torch/model cache; custom nodes — лёгкие HTTP-клиенты.

Проверки:

```text
http://127.0.0.1:8020/health
http://127.0.0.1:8020/v1/voices
http://127.0.0.1:8188
```

## 3. Загрузите workflow

JSON находятся в `integrations/comfyui/example_workflows`. Перетащите файл на canvas:

- `voice_profile_from_wav_ru.json` — регистрация одного profile;
- `compare_voice_samples_ru.json` — сравнение candidates;
- `emotion_router_test_ru.json` — quote-aware Router и единый AUDIO;
- `text_to_speech_with_emotions_ru.json` — готовая схема синтеза;
- `voice_clone_and_synthesize_ru.json` — низкоуровневый вариант, запускать только с разрешённым WAV.

## 4. Создайте voice family

Каждая эмоция — отдельный reference того же персонажа с общим `character_name`:

```text
clone:my_character_neutral
clone:my_character_happy
clone:my_character_sad
clone:my_character_angry
clone:my_character_whisper
clone:my_character_pleasure
clone:my_character_intimate
```

`pleasure` — удовольствие/наслаждение, явно довольная чувственно-положительная подача. `intimate` — близкая, приватная, нежно-чувственная манера речи. Они не вычисляются из happy/soft/breathy: каждый вариант требует отдельного реального reference того же speaker. Отсутствие этих папок в старой семье нормально и ведёт к family neutral fallback.

Рекомендуемый порядок:

1. Создайте и прослушайте neutral.
2. Для каждой эмоции используйте реальную эмоциональную реплику того же человека, а не DSP-искажение neutral.
3. Укажите точную transcript и style.
4. Не включайте overwrite при первом сравнении — создайте новый candidate ID.
5. Выполните одинаковую контрольную фразу для candidates и выберите лучший на слух.
6. Только после выбора заменяйте основной profile осознанно; voice library создаёт backup прежней style-папки в `voice_library/backups/<character>/`, вне active scan path.

После изменения library вызовите `/admin/reload-voices` либо перезапустите backend.

## 5. Проверьте Emotion Router в ComfyUI

Используйте обычные ASCII-кавычки:

```text
Она вошла в комнату. [voice:happy] "Я так рада тебя видеть!"
Она прикрыла дверь. [voice:whisper] "Теперь говори тише."
```

Нода Emotion Script показывает:

- normalized script;
- JSON segments с `kind`, style и profile;
- clean text без service tags;
- recognized styles.

Повествование всегда neutral. Тег действует только на следующую полную цитату и затем сбрасывается. Если `<family>_<style>` отсутствует, backend использует `<family>_neutral`, а не голос другого персонажа.

## 6. Подключите к SillyTavern

Запустите backend отдельно через `start-tts.bat`, а SillyTavern — её существующим `Start.bat`. В **Extensions → TTS** вручную настройте OpenAI Compatible:

```text
Endpoint: http://127.0.0.1:8020/v1/audio/speech
Model: tts-1-ru
Speed: 1
```

Добавьте IDs вручную, назначьте персонажу `<family>_neutral`, включите Enable и нажмите Apply. Не включайте Only narrate quotes: backend должен получить повествование. Проект не меняет Settings, Regex, карточки или Voice Map автоматически.

## 7. Критерии приёмки

- reference читается, имеет корректный sample rate и не клиппует; ComfyUI clone path делает mono PCM16 без принудительного 24 kHz resample;
- transcript дословная;
- neutral узнаваем и стабилен на нескольких фразах;
- emotion отличается подачей, но сохраняет speaker identity;
- в worker/audio не попадает `[voice:...]`;
- narration использует family neutral;
- после каждой цитаты стиль сбрасывается;
- единый WAV/MP3 не имеет заметных щелчков на стыках;
- subjective quality подтверждена прослушиванием, а не только автоматическими метриками.

## 8. Где лежат результаты

- рабочие profiles: `voice_library/profiles`;
- backups после overwrite: `voice_library/backups`;
- локальное непубликуемое портфолио: `local_voice_samples/readytouseprofiles`;
- тестовые аудио: `artifacts/audio-tests`;
- workflow JSON: `integrations/comfyui/example_workflows`.

Официальные источники: [Qwen3-TTS Base model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base), [SillyTavern TTS](https://docs.sillytavern.app/extensions/tts/), [SillyTavern Regex](https://docs.sillytavern.app/extensions/regex/).
