# ComfyUI Quick Start

1. Откройте папку `qwen3-tts-st` в Проводнике и дважды щёлкните `start-tts-and-comfyui.bat`. Альтернатива из PowerShell: `.\scripts\start-tts-and-comfyui.ps1 -VisibleComfyUIConsole`.
2. Оставьте открытыми BAT-окно и отдельную консоль ComfyUI. Когда закончите, закройте консоль ComfyUI: BAT автоматически остановит запущенный им backend и закроется сам.
3. Дождитесь готовности `http://127.0.0.1:8020/health` и `http://127.0.0.1:8188/system_stats`.
4. Откройте в браузере `http://127.0.0.1:8188`.
5. Перетащите на холст `integrations\comfyui\example_workflows\backend_health_and_voices.json`.
6. Нажмите **Queue** и убедитесь, что Health показывает `ok`, Models — `tts-1-ru`, Voices — текущие профили.
7. Откройте `emotion_script_preview.json` и нажмите **Queue**; WAV и модель для него не нужны.
8. Откройте `text_to_speech_ru.json`, оставьте endpoint `http://127.0.0.1:8020`, выберите существующий voice и введите русский текст.
9. Нажмите **Queue**; первая on-demand генерация может занять около минуты. Прослушайте результат в `Preview Audio`.
10. Для автоматической проверки выполните `.\scripts\test-comfyui-integration.ps1 -SkipSynthesis`; без флага выполняется реальный короткий синтез.
11. Остановите ComfyUI: `.\scripts\stop-comfyui.ps1`; backend: `.\stop.ps1`.

`voice_clone_and_synthesize_ru.json` оставьте до появления разрешённого WAV и точной дословной транскрипции. Подробности: [COMFYUI_SETUP_RU.md](COMFYUI_SETUP_RU.md).
