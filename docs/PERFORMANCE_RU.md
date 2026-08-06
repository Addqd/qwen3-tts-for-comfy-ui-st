# Производительность и совместимость

Дата тестов: 2026-08-05. Модель: `Qwen/Qwen3-TTS-12Hz-0.6B-Base`, Russian, ICL voice cloning, SDPA. Внешние процессы не завершались.

| Режим | Стек | Результат |
|---|---|---|
| CPU FP32 | torch 2.13 CPU | 4.32 с WAV за 50.00 с, RTF 11.57; второй 3.44 с за 29.79 с, RTF 8.66 |
| CPU FP32 финальный | torch 2.11.0+cu126 | 1.92 с WAV за 22.59 с, RTF 11.77; HTTP 200 |
| CUDA FP16 | torch 2.13.0+cu130 | модель загрузилась, ~3.6 GiB total VRAM used; 2 запуска не завершились за 5–6.5 мин |
| CUDA FP16 | torch/torchaudio 2.11.0+cu126 | модель загрузилась; bounded 256-token запуск не завершился за 5 мин |
| CUDA FP32 | torch/torchaudio 2.11.0+cu126 | 3 стабильных запуска: 17.50 с (с load), 6.18 с, 5.53 с; последний WAV 2.24 с, RTF 2.47 |
| CUDA on demand FP32 | torch/torchaudio 2.11.0+cu126 | HTTP 200, 2.16 с WAV за 35.22 с с load/unload; VRAM освобождена |
| auto | выбрал FP32 on-demand из-за 16 GPU-клиентов | HTTP 200, 2.08 с WAV за 22.49 с; VRAM освобождена |
| mock API | CPU | WAV, MP3, Unicode, emotion tags, SillyTavern request и ComfyUI node прошли |

CPU/CUDA WAV проверены `ffprobe`/soundfile: PCM s16le, mono, 24 кГц, конечные значения, без clipping. Persistent FP32 использовал суммарно около 6.41 GiB VRAM (с внешними клиентами); после stop вернулось к ~0.92 GiB. On-demand после ответа вернул VRAM с 1.03 GiB исходного use к ~0.91 GiB.

Рабочая Turing CUDA-точность — FP32. FP16 корректно загружает модель, но генерация зависает; BF16, FlashAttention 2, Triton и compile не включались. Локальный default — `auto`: требуется не менее 6000 + 750 MiB свободной VRAM; при внешних GPU-процессах выбирается on-demand, при меньшем запасе — CPU.

Повторение:

```powershell
.\scripts\benchmark-baseline.ps1
.\scripts\benchmark-tts-cpu.ps1
.\scripts\benchmark-tts-gpu.ps1       # экспериментально, может ждать до timeout
.\scripts\benchmark-tts-on-demand.ps1 # экспериментально
.\scripts\benchmark-coexistence.ps1
```

Не запускайте GPU benchmark при малом запасе VRAM. `auto` полезен как policy engine, но не знает измеренную скорость конкретной версии. Текущий локальный `config.local.yaml` задаёт `auto`; при необходимости предсказуемого CPU-запуска используйте `config/config.cpu.yaml`.

Артефакты:

- `qwen-cpu-first-ru.wav` — первый настоящий русский результат.
- `qwen-cpu-russian-profile.wav` — синтез из русского reference-профиля.
- `qwen-cpu-final-ru.wav` — контроль на финальном torch/cu126.
- `sillytavern-qwen-real.mp3` — настоящий Qwen-запрос в форме текущего SillyTavern provider: 3.672 с MP3, mono 24 кГц, HTTP 200 `audio/mpeg`.
- `qwen-emotion-router-real.wav` — настоящий двухсегментный neutral → happy-candidate запрос; HTTP 200, 4.44 с WAV, voice-теги удалены до worker.

Субъективные акцент, сходство, эмоции, артефакты и ударения автоматически не подтверждаются. Их должен оценить пользователь прослушиванием.

## ComfyUI coexistence — 2026-08-06

ComfyUI 0.30.0 с Manager и API-client nodes добавила около 101 MiB VRAM относительно измеренного перед запуском уровня (1 180 → 1 281 MiB); рабочий процесс использовал около 931 MiB RAM. Реальный Queue Prompt с Qwen on-demand достигал 6 268 MiB VRAM used, завершился за 83 с с учётом повторной import-проверки и первой загрузки, создал 15.6 с audio 24 kHz и вернул VRAM к 1 289 MiB. Очередь после job: running 0, pending 0; backend `model_loaded=false`. Эти цифры зависят от внешней графической нагрузки и не являются гарантированным benchmark.
