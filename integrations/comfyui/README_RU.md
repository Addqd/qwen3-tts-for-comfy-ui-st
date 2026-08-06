# Qwen TTS API nodes

Версия 0.1.0. Лёгкие клиентские ноды для внешнего backend `http://127.0.0.1:8020`; они не импортируют torch, transformers, qwen_tts или CUDA.

Установка, проверка и удаление описаны в `docs/COMFYUI_SETUP_RU.md`. Категория меню: **Qwen TTS API**.

- Server хранит endpoint/timeout/model/format и проверяет health.
- Synthesize отправляет русский текст/voice tags и возвращает AUDIO, temp path, metadata, duration.
- Clone Voice преобразует входной AUDIO в mono WAV и вызывает consent-gated API.
- Voice Selector читает общий список backend.
- Emotion Script разбирает теги и mapping.
- Health показывает device/model/voices/queue/resources.

Для сохранения используйте штатную PreviewAudio/Save Audio ноду ComfyUI. Temp WAV создаётся в системной temp/ComfyUI temp, не в custom node.

Endpoint намеренно принимает только `http://127.0.0.1:<port>`. Workflow-примеры находятся в `example_workflows`; clone workflow по умолчанию имеет `consent_confirmed=false`.
