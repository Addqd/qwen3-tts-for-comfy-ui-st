# Current production context

- Neural engine: pinned `qwentts.cpp` revision `7b6ed4f6db964c14fd3ac36c1ca13f1ce6150f4e`.
- Model: Qwen3-TTS 1.7B Base Q8_0 on CUDA0.
- Public facade: `127.0.0.1:8020`; internal persistent engine: `127.0.0.1:8030`.
- Default voice: `clone:test_ru_dima_neutral`, prepared once as `.spk/.rvq`.
- Canonical ComfyUI workflow: `integrations/comfyui/example_workflows/voice_profile_from_wav_ru.json`.
- SillyTavern uses ordinary one-shot OpenAI-compatible MP3; no streaming integration.
- Historical Python inference and training are manual fallback material only under `archive/legacy-python-backend`.
