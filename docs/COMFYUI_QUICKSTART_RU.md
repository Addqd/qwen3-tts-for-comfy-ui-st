# ComfyUI Voice Lab

Запустите проект через `start-tts-and-comfyui.bat`, затем откройте:

`integrations/comfyui/example_workflows/voice_profile_from_wav_ru.json`

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

Runtime Settings содержит только работающие параметры qwentts: Russian normalization, pronunciation dictionary, seed, `max_new_tokens`, temperature, top-k, top-p и repetition penalty. Production model фиксирован: Qwen3-TTS 1.7B Base Q8_0.
