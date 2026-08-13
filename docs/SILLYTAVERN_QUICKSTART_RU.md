# SillyTavern

Используйте обычный OpenAI Compatible TTS provider:

- endpoint: `http://127.0.0.1:8020/v1/audio/speech`;
- model: `tts-1-ru`;
- voice: `clone:test_ru_dima_neutral`;
- response format: `mp3`;
- speed: `1`.

Facade получает готовый one-shot WAV от persistent qwentts, при необходимости преобразует его в MP3 через FFmpeg и возвращает полный файл. SillyTavern, его Voice Map и исходный код менять не требуется.

Smoke test для уже запущенного SillyTavern:

```powershell
.\scripts\test-sillytavern-integration.ps1
```
