# SillyTavern + Qwen3-TTS: быстрый запуск

1. Запустите `start-tts.bat` в корне проекта.
2. Отдельно запустите существующий `Start.bat` SillyTavern.
3. В **Extensions → TTS** вручную выберите **OpenAI Compatible** и укажите:

   ```text
   Endpoint: http://127.0.0.1:8020/v1/audio/speech
   Model: tts-1-ru
   Speed: 1
   ```

4. Добавьте нужные voice IDs, назначьте персонажу `<family>_neutral`, включите **Enable** и нажмите **Apply**.
5. Не включайте **Only narrate quotes**: Router должен озвучить и neutral-повествование.
6. Тестовый ответ:

   ```text
   Она посмотрела на дверь. [voice:happy] "Ты пришёл!" Она улыбнулась. [voice:whisper] "Я правда тебя ждала."
   ```

Тег действует только на следующую полную реплику в ASCII-кавычках. Всё остальное neutral. Backend останавливается `./stop.ps1`; SillyTavern — её собственным способом.

Подробности: [SILLYTAVERN_SETUP_RU.md](SILLYTAVERN_SETUP_RU.md). Ошибки: [SILLYTAVERN_TROUBLESHOOTING_RU.md](SILLYTAVERN_TROUBLESHOOTING_RU.md).
