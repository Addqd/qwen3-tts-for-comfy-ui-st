# Диагностика

Сначала:

```powershell
.\scripts\diagnose.ps1
.\status.ps1
Get-Content .\logs\server.err.log -Tail 100
```

**Python 3.12 was not found.** На этой машине `py` пуст. Установите официальный CPython 3.12 или передайте существующий `python.exe` параметром `-Python`; глобальные пакеты не требуются.

**PermissionError в `C:\Users\...\.cache\huggingface`.** Backend уже устанавливает `HF_HOME` и hub cache в проектный `model_cache`. Если ошибка повторилась, убедитесь, что используется текущий код и запускается через `.venv`.

**Hugging Face symlink/Xet warning.** Это предупреждение скорости/занимаемого места. Обычная HTTP-загрузка была успешной. Developer Mode и `hf_xet` необязательны.

**SoX could not be found.** Предупреждение идёт из зависимости. Реальные WAV/MP3 прошли через soundfile/FFmpeg без SoX. Устанавливать неизвестный бинарник ради него не требуется.

**Flash-attn is not installed.** Ожидаемо: FlashAttention 2 не включается автоматически на Turing. Используется SDPA.

**CUDA FP16 долго не завершается.** Это воспроизведено и на cu130, и на согласованной cu126 паре. Используйте проверенный `config.cuda.yaml` с float32 либо CPU; не меняйте его обратно на FP16 и не убивайте внешние GPU-процессы.

**CUDA OOM / мало VRAM.** Проверьте `nvidia-smi`, затем CPU. Не уменьшайте safety reserve вслепую. Проверенный FP32 `cuda_on_demand` освобождает VRAM после запроса, но для загрузки всё равно требует безопасный начальный запас.

**Port 8020 занят.** `start.ps1` покажет PID. Измените только `server.port` в `config.local.yaml` и endpoint клиентов; не завершайте неизвестный процесс.

**Voice not found.** `Invoke-RestMethod http://127.0.0.1:8020/v1/voices`. Проверьте `metadata.json`, `reference.wav` и fallback. Перезагрузите: `Invoke-RestMethod -Method Post http://127.0.0.1:8020/admin/reload-voices`.

**WAV отклонён.** Нужен настоящий RIFF/WAVE; запустите `.\scripts\validate-voice.ps1 -Path ... -RefText "..."`. Исправляйте копию, оригинал сохраните.

**ComfyUI missing nodes.** Перезапустите ComfyUI, проверьте marker/junction, затем `test-comfyui-integration.ps1`. Backend Python и ComfyUI Python должны оставаться раздельными.

**ComfyUI не запускается или порт 8188 занят.** Выполните `.\scripts\status-comfyui.ps1` и прочитайте `logs\comfyui.err.log`. Launcher не завершает владельца занятого порта. Не используйте `0.0.0.0`.

**Backup нод импортируется второй раз.** Текущий installer хранит backups в `ComfyUI\.qwen_tts_api_nodes-backups`, а не в `custom_nodes`. Если осталась старая папка `custom_nodes\qwen_tts_api_nodes.backup-*`, остановите ComfyUI и перенесите её за пределы `custom_nodes`; не удаляйте вслепую.

**Manager сообщает, что нет matrix-nio.** Необязательная matrix-sharing функция отключена; для Manager UI и Qwen API nodes это не import error. Не устанавливайте пакет без отдельной необходимости.

**Проверка реального workflow.** Запустите оба сервиса, затем `.\scripts\test-comfyui-integration.ps1 -SkipSynthesis`. Без флага выполняется Qwen synthesis с техническим demo-профилем. Скрипт проверяет `/prompt`, `/history`, пустую `/queue`, workflows и отсутствие `qwen_tts` в ComfyUI Python.

**PowerShell показывает кракозябры.** Это ограничение Windows PowerShell 5.1 при интерпретации некоторых UTF-8 ответов/потоков. API содержит `charset=utf-8`; браузер/Python корректны. Скрипты проекта сохранены ASCII-only, русские test strings передаются JSON Unicode escapes.
