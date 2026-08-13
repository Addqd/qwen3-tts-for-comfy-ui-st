# Task log

Previous project history is preserved without modification in `archive/legacy-python-backend/TASK_LOG.md`.

## 2026-08-13 — qwentts.cpp production migration

- Confirmed official prebuilt provenance and embedded upstream revision.
- Recorded and verified SHA-256 for binaries, NVIDIA runtime DLLs and both GGUF files.
- Prepared the primary `clone:test_ru_dima_neutral` profile as reusable `.spk/.rvq`.
- Passed the subjective voice-quality gate.
- Corrected the Windows test input path to explicit UTF-8 without BOM and exact JSON round-trip.
- Correct warm representative result: 3.537 s wall time, 13.52 s WAV, RTF 0.262.
- Replaced active neural inference with persistent qwentts plus a lightweight compatibility facade.
- Archived the former Python inference/training implementation as a manual fallback.
