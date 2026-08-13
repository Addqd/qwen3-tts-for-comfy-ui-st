# Voice library

Каждый active profile хранит:

- `metadata.json` с постоянным profile/public ID, `ref_text` и языком;
- `reference.wav`;
- `reference.spk`;
- `reference.rvq`.

Public voice ID имеет вид `clone:<profile_name>` и одинаков для ComfyUI, SillyTavern и API. `.spk/.rvq` создаются `qwen-codec` один раз и повторно регистрируются в persistent qwentts при старте.

Папка `profiles/` исключена из Git, потому что содержит локальные голосовые данные. Не публикуйте чужие WAV и транскрипции.
