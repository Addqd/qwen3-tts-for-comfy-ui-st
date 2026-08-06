# Библиотека голосов

Каждый профиль — папка с неизменённым `reference.wav` и `metadata.json`. Рекомендуется чистый mono WAV 24 кГц длительностью примерно 3–15 секунд без музыки, реверберации и клиппинга. Главное для ICL — точная дословная русская расшифровка, включая произнесённые междометия.

```text
profiles/<character>/<style>/
  reference.wav
  metadata.json
```

Проверка без импорта:

```powershell
.\scripts\validate-voice.ps1 -Path "D:\voice\reference.wav" -RefText "Точная фраза из записи."
```

Безопасный импорт можно сделать через `POST /v1/audio/voice-clone` или ноду **Qwen TTS Clone Voice**. Требуется `consent_confirmed=true`; существующий профиль не перезаписывается без `overwrite=true`. API сначала проверяет RIFF/WAVE, длительность, sample rate, каналы, peak/RMS, грубую оценку шума и транскрипцию. Оценка шума эвристическая и может дать предупреждение на выразительной речи.

Для воспроизводимой локальной подготовки открытых тестовых samples используйте `scripts/prepare_voice_samples.py`, а для создания только primary test profiles — `scripts/create_test_voice_profiles.py --consent-confirmed`. Аудио и созданные profiles остаются в игнорируемых `local_voice_samples` и `voice_library/profiles`; пример manifest без реального WAV находится в `config/voice_samples.example.json`. Подробности: [../docs/VOICE_SAMPLES_AND_EMOTIONS_RU.md](../docs/VOICE_SAMPLES_AND_EMOTIONS_RU.md).

Для одного персонажа запишите отдельные разрешённые референсы: neutral, soft, whisper, breathy, happy, sad, angry, tense. Не создавайте их простой сменой названия одного WAV: emotion router переносит подачу именно из записи. При отсутствии style используется base profile запроса, поэтому base рекомендуется задавать neutral.

Текущие demo-профили:

- `clone:QwenDemoSeed` — официальный английский sample Qwen; только технический seed.
- `clone:QwenDemoRussianNeutral` — синтетический русский WAV, созданный этой моделью из seed; полезен для end-to-end теста, не замена разрешённому пользовательскому голосу.
- `clone:QwenDemoHappyCandidate` — синтетический восклицательный reference для технической проверки `[voice:happy]`; эмоциональность ещё должен подтвердить пользователь.

Для реального использования предоставьте разрешённый русский WAV и точную транскрипцию. Оригинал держите вне импортируемой папки либо сделайте резервную копию всего `voice_library/profiles`.
