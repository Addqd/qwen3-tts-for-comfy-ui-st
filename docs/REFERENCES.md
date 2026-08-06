# Проверенные источники

Дата проверки всех записей: 2026-08-05. При отсутствии релизного тега указан наблюдавшийся branch snapshot; это честнее выдуманного commit hash.

| Источник | Версия/snapshot | Что подтверждено | Решение/ограничение |
|---|---|---|---|
| [QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) | `main`, package `qwen-tts 0.1.1` | Python 3.12 fresh env, `Qwen3TTSModel`, Base voice cloning, `language="Russian"`, ICL prompt с reference audio + transcript | Основной официальный inference API; никаких неподтверждённых instruct style для Base |
| [Qwen model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base) | snapshot `5d83992436eae1d760afd27aff78a71d676296fc` | 0.6B Base, voice clone, Russian среди поддерживаемых языков | Единственная модель backend; скачана и загружена реально |
| [Qwen3-TTS technical report](https://arxiv.org/abs/2601.15621) | arXiv:2601.15621 | multilingual/Russian evaluation; ICL text-speech prompt лучше сохраняет просодию, чем speaker embedding only | Выбран `clone_mode: icl` и точная транскрипция |
| [qwen-tts PyPI metadata](https://pypi.org/project/qwen-tts/) | 0.1.1 | published dependency surface | Закреплено в pyproject; processor cache quirk исправлен локальным HF cache env |
| [PyTorch Start Locally](https://pytorch.org/get-started/locally/) | current 2026-08-05 | Windows wheels and CUDA selection | Сверялся перед установкой, глобальный toolkit не нужен |
| [PyTorch previous versions](https://pytorch.org/get-started/previous-versions/) | current 2026-08-05 | официальный Windows комплект torch/torchaudio 2.11.0 cu126 | Финальный согласованный CUDA wheel; CPU также работает |
| [SillyTavern TTS docs](https://docs.sillytavern.app/extensions/tts/) | `release` docs snapshot | Extensions → TTS, Enable, Auto-generation, manual megaphone, Voice Map, Apply, quotes/actions filters | Использован штатный provider, исходники ST не менялись |
| [SillyTavern OpenAI-compatible TTS frontend](https://github.com/SillyTavern/SillyTavern/blob/release/public/scripts/extensions/tts/openai-compatible.js) | `release` 2026-08-05 | полный Provider Endpoint; ручной voice list; body model/input/voice/mp3/speed | Наш API повторяет эту форму; реальный shaped request прошёл |
| [SillyTavern proxy source](https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/openai.js) | `release` 2026-08-05 | proxy POST точно на `provider_endpoint`, Bearer key может быть пустым | В документации указан полный `/v1/audio/speech` |
| [ComfyUI custom-node lifecycle](https://docs.comfy.org/custom-nodes/backend/lifecycle) | current 2026-08-05 | package init, class mappings, node schema | Реализована актуальная регистрация |
| [ComfyUI custom-node install](https://docs.comfy.org/installation/install_custom_node) | current 2026-08-05 | `custom_nodes`, restart, dependency isolation concerns | Безопасный junction/copy installer |
| [ComfyUI audio nodes source](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_audio.py) | `master` 2026-08-05 | AUDIO = waveform tensor `[batch,channels,samples]` + sample_rate; встроенные preview/save возможности | Возвращается настоящий AUDIO; отдельный Save Audio не дублируется |
| [ComfyUI-Manager](https://github.com/Comfy-Org/ComfyUI-Manager) | `main`, V3-era snapshot | manager is current ecosystem installer | Наш локальный пакет пока не публикуется в registry, ставится скриптом |
| [groxaxo/Qwen3-TTS-Openai-Fastapi](https://github.com/groxaxo/Qwen3-TTS-Openai-Fastapi) | `main` 2026-08-05 | внешний OpenAI-shaped FastAPI server — архитектурно жизнеспособен | Не скопирован: наш server строже 127.0.0.1, shared voices/router/resources |
| [1038lab/ComfyUI-QwenTTS](https://github.com/1038lab/ComfyUI-QwenTTS) | `main` 2026-08-05 | пример прямой загрузки Qwen в ComfyUI | Не установлен: дублирование модели и зависимости |
| [DarioFT/ComfyUI-Qwen3-TTS](https://github.com/DarioFT/ComfyUI-Qwen3-TTS) | `main` 2026-08-05 | альтернативные in-process workflows | Только технический референс, не основной режим |
| [flybirdxx/ComfyUI-Qwen-TTS](https://github.com/flybirdxx/ComfyUI-Qwen-TTS) | `main` 2026-08-05 | ещё один direct-node вариант | Не смешиваем backend и ComfyUI Python |

Поиск открытых Windows/Turing/CUDA/FP16/SDPA проблем не дал одного официального issue, который гарантированно объясняет наблюдаемое зависание. Поэтому совместимость записана по собственным воспроизводимым тестам, а не приписана сторонней issue. FlashAttention 2, BF16, Triton и compile намеренно не тестировались как автоматические режимы на Turing.

## Архитектурное сравнение

Direct ComfyUI mode проще визуально, но загружает модель/torch/transformers в процесс ComfyUI, расходует VRAM повторно рядом с SillyTavern backend и повышает риск конфликтов. API-client mode добавляет localhost HTTP hop, зато даёт один model owner, общие voices/emotions, независимый restart и минимальный custom node. Для этой платформы выбран API-client mode.
