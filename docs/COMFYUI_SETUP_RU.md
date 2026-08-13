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
