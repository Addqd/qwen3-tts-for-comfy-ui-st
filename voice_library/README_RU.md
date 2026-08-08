# Библиотека голосов

Каждый профиль — папка с неизменённым `reference.wav` и `metadata.json`. Рекомендуется чистый mono WAV длительностью примерно 3–15 секунд без музыки, реверберации и клиппинга. Обязательного предварительного resample в 24 кГц нет: API сохраняет исходный WAV и sample rate, а `qwen-tts` передаёт исходный rate speech tokenizer и внутренне ресемплирует сигнал для speaker encoder. Главное для ICL — точная дословная русская расшифровка, включая произнесённые междометия. Текущий generated output Qwen имеет 24 кГц — это отдельная характеристика результата.

```text
profiles/<character>/<style>/
  reference.wav
  metadata.json

backups/<character>/<style>-<timestamp>/
  reference.wav
  metadata.json
```

Проверка без импорта:

```powershell
.\scripts\validate-voice.ps1 -Path "D:\voice\reference.wav" -RefText "Точная фраза из записи."
```

Безопасный импорт можно сделать через `POST /v1/audio/voice-clone` или ноду **Qwen TTS Clone Voice**. Требуется `consent_confirmed=true`; существующий профиль не перезаписывается без `overwrite=true`. API сначала проверяет RIFF/WAVE, длительность, sample rate, каналы, peak/RMS, грубую оценку шума и транскрипцию. Оценка шума эвристическая и может дать предупреждение на выразительной речи.

Для воспроизводимой локальной подготовки открытых тестовых samples используйте `scripts/prepare_voice_samples.py`, а для создания только primary test profiles — `scripts/create_test_voice_profiles.py --consent-confirmed`. Аудио и созданные profiles остаются в игнорируемых `local_voice_samples` и `voice_library/profiles`; пример manifest без реального WAV находится в `config/voice_samples.example.json`. Подробности: [../docs/VOICE_SAMPLES_AND_EMOTIONS_RU.md](../docs/VOICE_SAMPLES_AND_EMOTIONS_RU.md).

Для одного персонажа запишите отдельные разрешённые референсы: neutral, soft, whisper, breathy, happy, sad, angry, tense, pleasure, intimate. `pleasure` и `intimate` не строятся автоматически из happy/soft/breathy: для каждого нужен реальный reference того же speaker с соответствующей подачей. При отсутствии style используется neutral той же семьи, затем configured safe fallback.

При `overwrite=true` прежняя style-папка копируется в `voice_library/backups`, который не сканируется как активная библиотека. Старые `*.backup-*` внутри `profiles` сохраняются на месте, но loader намеренно игнорирует их.

Текущие demo-профили:

- `clone:QwenDemoSeed` — официальный английский sample Qwen; только технический seed.
- `clone:QwenDemoRussianNeutral` — синтетический русский WAV, созданный этой моделью из seed; полезен для end-to-end теста, не замена разрешённому пользовательскому голосу.
- `clone:QwenDemoHappyCandidate` — синтетический восклицательный reference для технической проверки `[voice:happy]`; эмоциональность ещё должен подтвердить пользователь.

Для реального использования предоставьте разрешённый русский WAV и точную транскрипцию. Оригинал держите вне импортируемой папки либо сделайте резервную копию всего `voice_library/profiles`.
