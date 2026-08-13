# Legacy Python backend

This directory preserves the former PyTorch/Qwen inference implementation as a manual fallback.

- Last active legacy commit: `cc1d638a898784fcecb528ec95f7669507138cc2`.
- Restore the complete historical tree with Git from that commit if a rollback is required.
- Nothing under this directory participates in normal startup or imports.
- Heavy local data (`.venv`, Hugging Face caches, trained weights, generated audio and logs) is intentionally not archived.

The active project uses the persistent `qwentts.cpp` server and a lightweight HTTP facade. Do not add this directory to active Python paths.
