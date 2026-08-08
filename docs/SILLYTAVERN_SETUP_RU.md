# Ручная интеграция с SillyTavern

Проект использует встроенный TTS provider **OpenAI Compatible**. Отдельное расширение, копирование файлов и изменение core SillyTavern не нужны. Все шаги ниже выполняет пользователь в интерфейсе; скрипты проекта не переписывают `settings.json`, карточки, чаты, Regex или Voice Map.

## Архитектура

```text
ответ персонажа
  → встроенная TTS-очередь SillyTavern
  → OpenAI Compatible provider и локальный proxy
  → http://127.0.0.1:8020/v1/audio/speech
  → preprocessing, voice library и Emotion Router
  → один MP3
  → штатный player SillyTavern
```

Qwen-модель загружается только backend-процессом. Backend не меняет LLM, промпты и отображаемый текст чата.

## Запуск

1. Запустите только backend двойным щелчком по `start-tts.bat` либо командой `./start.ps1` из корня проекта.
2. Запустите существующий `Start.bat` самой SillyTavern.
3. Откройте `http://127.0.0.1:8000`.
4. Проверьте backend: `http://127.0.0.1:8020/health`.

Эти два запуска независимы. `start-tts.bat` не запускает и не останавливает SillyTavern. Существующий `start-tts-and-comfyui.bat` предназначен только для backend + ComfyUI и не изменён этой интеграцией.

## Настройка provider вручную

В SillyTavern откройте **Extensions → TTS**:

1. Provider: **OpenAI Compatible**.
2. Endpoint: `http://127.0.0.1:8020/v1/audio/speech`.
3. Model: `tts-1-ru`.
4. Speed: `1`.
5. Добавьте нужные voice IDs вручную: OpenAI-compatible provider не обязан получать их через Refresh.
6. Включите **Enable**; при необходимости включите **Auto-generation**.
7. В Voice Map назначьте персонажу neutral-профиль его голосовой семьи и нажмите **Apply**.

Список доступных IDs: `http://127.0.0.1:8020/v1/voices`. Примеры:

```text
clone:olga_pletneva_neutral
clone:olga_zubkova_neutral
clone:elena_shulman_neutral
clone:lina_ivanova_neutral
clone:irina_kireeva_neutral
clone:veronika_sarkisova_neutral
clone:eliza_martirosova_neutral
clone:larisa_nekipelova_neutral
```

## Контракт Emotion Router

Озвучивается весь ответ. Повествование и реплики без тега всегда neutral. Тег относится только к непосредственно следующей полной реплике в обычных ASCII-кавычках `"..."`:

```text
Она подошла к двери. [voice:happy] "Ты всё-таки пришёл!" Она улыбнулась.
[voice:whisper] "Только никому не говори." После этого она снова замолчала.
```

После закрывающей кавычки стиль автоматически сбрасывается в neutral. Поддерживаются `neutral`, `soft`, `whisper`, `breathy`, `happy`, `sad`, `angry`, `tense`, `pleasure`, `intimate`. Последние два требуют отдельных реальных profiles и не смешиваются из других styles. Неизвестный, malformed или стоящий не перед цитатой tag удаляется и не произносится; соответствующая речь остаётся neutral.

Для style backend ищет `<та же семья>_<style>`. Если его нет, используется `<та же семья>_neutral`; если отсутствует и он, применяется настроенный безопасный fallback. Поэтому в Voice Map следует назначать именно neutral-профиль.

Не включайте SillyTavern **Only narrate quotes**: backend должен получить и повествование, и реплики. Regex, который удаляет квадратные скобки или кавычки до TTS, тоже нарушит контракт; проект сам Regex не создаёт и не меняет.

## Проверка транспорта

Когда оба сервиса уже запущены пользователем:

```powershell
./scripts/test-sillytavern-integration.ps1
```

Скрипт не меняет настройки и не управляет жизненным циклом SillyTavern. Он получает CSRF token, отправляет тест через локальный proxy и сохраняет MP3 в игнорируемую папку `artifacts/audio-tests`.

HTTP-тест не подтверждает browser autoplay, Stop, replay, group chat и сохранение UI-настроек после перезапуска — это проверяется вручную.

Официальная справка: [SillyTavern TTS](https://docs.sillytavern.app/extensions/tts/) и [Regex](https://docs.sillytavern.app/extensions/regex/).
