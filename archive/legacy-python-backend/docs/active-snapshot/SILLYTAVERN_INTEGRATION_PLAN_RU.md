# Состояние интеграции с SillyTavern

## Завершено

- локальные provider/proxy sources установленной SillyTavern 1.18.0 изучены;
- выбран встроенный OpenAI Compatible provider;
- backend принимает совместимый `POST /v1/audio/speech` и возвращает MP3/WAV;
- Emotion Router и voice-family fallback находятся полностью в backend;
- создан независимый `start-tts.bat`;
- создан read-only transport smoke script, который не меняет пользовательские настройки;
- прежнее экспериментальное изменение TTS-секции `settings.json` восстановлено из byte-for-byte backup;
- объединённые start/stop/configure/update/uninstall scripts SillyTavern удалены из рабочего набора.

## Реализовано, но требует живых сервисов

- HTTP smoke через CSRF session и `/api/openai/custom/generate-voice`;
- проверка одного MP3 с neutral narration и tagged quoted dialogue.

## Требует ручного подтверждения пользователя

- ручная настройка OpenAI Compatible provider и Voice Map;
- browser autoplay, Stop и replay;
- group chat с разными neutral-профилями;
- сохранение TTS settings после полного restart.

Проект намеренно не меняет SillyTavern, её карточки, чаты, промпты, Regex и пользовательские настройки.
