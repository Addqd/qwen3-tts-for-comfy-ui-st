# SillyTavern

Проверено по ветке `release` SillyTavern 2026-08-05. Исходные файлы SillyTavern менять не нужно.

1. Запустите backend: `.\start.ps1`.
2. В SillyTavern откройте верхнюю панель **Extensions** → секцию **TTS**.
3. Provider: **OpenAI Compatible**.
4. Provider Endpoint: `http://127.0.0.1:8020/v1/audio/speech` — это полный endpoint, не только `/v1`.
5. API Key: оставьте пустым. Если UI требует значение, введите произвольное локальное `local`; backend его игнорирует.
6. Model: `tts-1-ru`.
7. Available Voices: перечислите через запятую, например `clone:QwenDemoRussianNeutral,clone:QwenDemoSeed`. Актуальный список: `http://127.0.0.1:8020/v1/voices`.
8. Speed: `1.0`. Нажмите **Apply**.
9. В Voice Map назначьте персонажу один из введённых voice ID и снова нажмите **Apply**.

OpenAI Compatible хранит список голосов вручную: его Refresh не запрашивает `/v1/voices`. Текущий frontend отправляет `{model,input,voice,response_format:"mp3",speed}` на полный Provider Endpoint через локальный proxy SillyTavern — именно эта форма реально протестирована и вернула `audio/mpeg`.

Для автоматической речи включите **Enable** и **Auto-generation**. Для ручной — значок мегафона у сообщения. Опции **Only narrate quotes** и **Ignore text inside asterisks** находятся в общей TTS-секции: первая соответствует прямой речи, выключенное значение — полному ответу. Backend дополнительно поддерживает `preprocessing_mode`, но штатный provider SillyTavern это поле не отправляет, поэтому основной выбор делайте в UI SillyTavern.

Типовые ошибки:

- `HTTP 500`: проверьте, что указан полный `/v1/audio/speech`, backend запущен и voice ID существует.
- Voice not found: вручную обновите Available Voices и Voice Map.
- Нет звука: включите Enable, нажмите Apply, проверьте browser autoplay.
- Долгое ожидание: CPU на этой машине работает медленнее реального времени; смотрите `status.ps1` и `logs/server.err.log`.
- Кириллица испорчена только в старой консоли PowerShell: API отдаёт UTF-8 с charset; браузер и Python-клиент проверены корректно.
