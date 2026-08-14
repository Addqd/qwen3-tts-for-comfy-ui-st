# ComfyUI Voice Lab

Запустите проект через `start-tts-and-comfyui.bat`, затем откройте:

`integrations/comfyui/example_workflows/voice_profile_from_wav_ru.json`

Открывайте этот канонический JSON прямо из репозитория. Launcher автоматически обновляет только копию с project ownership marker; одноимённые пользовательские workflow без marker никогда не перезаписываются.

Canonical workflow:

```text
Qwen TTS Server → Runtime Settings → Clone Voice → Synthesize → Preview Audio
                         ↑
                     Load Audio
```

1. В `Load Audio` выберите WAV.
2. В `Clone Voice` введите точную дословную транскрипцию `ref_text`, постоянный `profile_name` и имя персонажа.
3. `overwrite` включайте только для намеренной замены существующего profile ID.
4. Запустите workflow. Backend создаст `reference.spk` и `reference.rvq`, сохранит профиль в общей voice library и сразу зарегистрирует тот же `clone:<profile_name>` для ComfyUI и SillyTavern.
5. Результат `voice_id` подключён к `Synthesize`; аудио поступает в `Preview Audio`.

Runtime Settings содержит Russian normalization, pronunciation dictionary, Silero Stress, формат ударения, Silero Text Enhancement, seed, `max_new_tokens`, temperature, top-k, top-p и repetition penalty. Нода сохраняет параметры через существующий backend `GET/PUT /admin/runtime-settings`.

Production model фиксирован: Qwen3-TTS 1.7B Base BF16; model selector удалён. Silero Stress работает на CPU и включён по умолчанию, Text Enhancement также работает на CPU и выключен по умолчанию. Переключатели независимы и могут быть включены одновременно. Ручные pronunciation replacements защищаются от neural preprocessing и имеют приоритет. Text Enhancement может менять авторскую пунктуацию, а лучший формат ударения всё ещё нужно определить ручным A/B-прослушиванием.

`control after generate` для `seed` уже сериализован отдельным значением `fixed`, поэтому следующие sampling-параметры в canonical workflow не сдвигаются.
