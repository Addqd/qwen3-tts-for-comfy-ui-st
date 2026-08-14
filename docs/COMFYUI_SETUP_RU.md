# Установка ComfyUI nodes

Исходник нод в репозитории является authoritative:

`integrations/comfyui/qwen_tts_api_nodes/`

Для проекта с portable ComfyUI:

```powershell
.\scripts\install-comfyui-nodes.ps1
.\scripts\start-tts-and-comfyui.ps1
```

Установщик сохраняет managed marker, предпочитает Junction и умеет безопасно синхронизировать managed Copy. Unmanaged каталог не перезаписывается. Перед запуском ComfyUI project launcher обновляет managed node и затем проверяет фактически загруженную схему через `/object_info`.

Canonical workflow находится только в репозитории: `integrations/comfyui/example_workflows/voice_profile_from_wav_ru.json`. Проект не перезаписывает произвольные пользовательские workflows.

Runtime использует только BF16 talker/tokenizer; Q8 и выбор модели удалены из active production и из ComfyUI. `Runtime Settings` сохраняет Silero Stress, формат ударения и Silero Text Enhancement через существующий backend runtime-settings API. Оба Silero-компонента работают на общем CPU-only PyTorch и не используют VRAM.

`start-tts-and-comfyui.bat`, `start.ps1` и standalone ComfyUI launcher создают или присоединяются к единой управляемой project session. Owner регистрируется только для BAT-пути, который реально остаётся ждать ComfyUI; fire-and-forget launchers контролируются по основным компонентам. Завершение managed core component должно инициировать bounded shutdown session, но полный реальный BAT close-smoke ещё нужно повторить после устранения локального ACL blocker пользовательского voice profile. Прямой запуск внутренних facade/qwentts-runner не является поддерживаемым пользовательским entrypoint.
