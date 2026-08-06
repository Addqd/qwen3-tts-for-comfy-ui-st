# ComfyUI

Custom node сначала полностью подготовлена в проекте. Существующая ComfyUI не изменялась, потому что её путь не найден.

Предварительный просмотр без изменений:

```powershell
.\scripts\install-comfyui-nodes.ps1 -ComfyUIPath "D:\path\to\ComfyUI" -WhatIf
```

Установка junction с подтверждением:

```powershell
.\scripts\install-comfyui-nodes.ps1 -ComfyUIPath "D:\path\to\ComfyUI" -Mode Junction
```

Для Portable передайте корень, содержащий `ComfyUI\custom_nodes`; для manual/Desktop — корень с `custom_nodes`. Если junction запрещён, используйте `-Mode Copy`. Существующая одноимённая папка не меняется без `-ReplaceExisting`; при замене она переносится в timestamped backup. Затем перезапустите ComfyUI.

Проверка импорта в Python именно этой ComfyUI:

```powershell
.\scripts\test-comfyui-integration.ps1 -ComfyUIPath "D:\path\to\ComfyUI"
```

Загрузите JSON из `integrations/comfyui/example_workflows`. Сначала запустите `health_and_voices.json`, затем `text_to_speech_ru.json`. В `voice_clone_and_synthesize_ru.json` выберите разрешённый WAV, впишите точную расшифровку и только затем включите `consent_confirmed`.

Ноды находятся в категории **Qwen TTS API**: Server, Synthesize, Clone Voice, Voice Selector, Emotion Script, Health. Для сохранения используйте штатную ComfyUI audio output/save node; отдельная Qwen Save Audio намеренно не дублирует встроенную функцию.

Удаление:

```powershell
.\scripts\uninstall-comfyui-nodes.ps1 -ComfyUIPath "D:\path\to\ComfyUI"
```

Uninstall требует marker и удаляет только точную цель `custom_nodes\qwen_tts_api_nodes`; backup-папки сохраняются.

API-client mode рекомендуется: одна модель, один router, одна библиотека голосов, нет Qwen/torch/transformers в ComfyUI Python. Direct in-process ноды 1038lab/DarioFT/flybirdxx полезны как референсы, но могут дублировать VRAM и конфликтовать с окружением; они не установлены и остаются отдельной экспериментальной опцией.

Что проверено без установленной ComfyUI: package import, mappings всех шести нод, локальный endpoint guard, русский emotion parser, реальный HTTP вызов Server/Synthesize/Health/VoiceSelector, WAV → ComfyUI `AUDIO` `[1,1,T]` 24 кГц, JSON workflows. Полный Queue Prompt внутри реальной ComfyUI требует от пользователя её путь и не заявлен выполненным.
