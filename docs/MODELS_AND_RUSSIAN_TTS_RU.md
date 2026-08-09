# Выбор 0.6B / 1.7B и настройка русского TTS

Backend поддерживает две официальные Base-модели Qwen3-TTS и загружает не более одной модели одновременно:

| Публичный model ID | Модель | Назначение |
|---|---|---|
| `tts-1-ru` | значение `models.default` | обратная совместимость; по умолчанию 0.6B |
| `tts-1-ru-fast` | `Qwen/Qwen3-TTS-12Hz-0.6B-Base` | быстрый вариант и меньшие требования к памяти |
| `tts-1-ru-quality` | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | приоритет качества; больше RAM/VRAM и более долгий cold start |

При смене model ID backend сначала выгружает текущую модель и очищает её prompt cache, затем загружает выбранную. Тихого fallback с 1.7B на 0.6B нет: ошибка загрузки возвращается клиенту с запрошенным алиасом, разрешённым Hugging Face ID, режимом, device, dtype и исходной причиной.

На RTX 2070 SUPER 8 GB автоматический профиль для 1.7B консервативен и обычно выбирает CPU. Явный `resources.mode: cuda` остаётся доступен, но может завершиться честной CUDA OOM без подмены модели. На Turing `float32 + sdpa` остаётся безопасным значением; BF16, FlashAttention 2, Triton и `torch.compile` автоматически не включаются.

## Параметры одного запроса

```json
{
  "model": "tts-1-ru-fast",
  "voice": "clone:QwenDemoRussianNeutral",
  "input": "Qwen и ComfyUI готовы на 25% к 12:30.",
  "response_format": "wav",
  "generation_preset": "stable_russian",
  "russian_normalization": "full",
  "pronunciation_overrides": {
    "Qwen": "куэн",
    "ComfyUI": "комфи ю ай"
  }
}
```

- `generation_preset`: `default` или `stable_russian`.
- `russian_normalization`: `off`, `basic` или `full`.
- `pronunciation_overrides`: JSON object либо строки `source = replacement` в ComfyUI.

`stable_russian` передаёт только параметры, которые реально принимает установленный `qwen-tts==0.1.1`: temperature/top-k/top-p/repetition penalty и соответствующие subtalker-настройки. `default` сохраняет поведение библиотеки, кроме заданного для модели `max_new_tokens`.

`basic` безопасно нормализует пробелы и пунктуацию и применяет только явно заданный словарь `ё`. `full` дополнительно раскрывает ограниченные целые числа, проценты, время и десятичные дроби. Глобальная замена всех `е` на `ё` не выполняется. Сначала применяется глобальный словарь `pronunciation.dictionary`, затем request-level overrides; overrides имеют приоритет.

## SillyTavern

Встроенный OpenAI Compatible provider SillyTavern передаёт стандартные поля `model`, `input`, `voice`, `response_format`, `speed`. Поэтому модель можно выбрать напрямую:

- `tts-1-ru-fast` — 0.6B;
- `tts-1-ru-quality` — 1.7B;
- `tts-1-ru` — backend default.

Дополнительные поля нормализации provider сейчас не отправляет. Поэтому общие `request_defaults` равны `stable_russian + full`, и обычный SillyTavern/legacy OpenAI-compatible request получает рекомендуемый русский путь без client detection или User-Agent hacks. ComfyUI и другие explicit API clients могут передать `default`/`off`; request-level значение всегда имеет приоритет.

## Диагностика

`GET /v1/models` показывает публичные алиасы и разрешённые HF ID. `GET /health` дополнительно показывает `default_model`, `available_models`, активную модель, режим, device/dtype/attention, время загрузки и configured/effective concurrency. Последний запрос виден в `GET /metrics`, включая выбранный preset, режим нормализации и количество словарных замен.

Официальные источники: [Qwen3-TTS 1.7B Base model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base) и [Qwen3-TTS inference API](https://github.com/QwenLM/Qwen3-TTS/blob/main/qwen_tts/inference/qwen3_tts_model.py).
