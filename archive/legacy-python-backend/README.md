# Legacy Python backend

This directory preserves the former PyTorch/Qwen inference implementation as a manual fallback.

> Historical archive only. Do not run root-relative commands from archived documents in the active checkout: they would invoke the current qwentts.cpp runtime. Restore commit `cc1d638a898784fcecb528ec95f7669507138cc2` into a separate checkout before following any legacy procedure.

- Last active legacy commit: `cc1d638a898784fcecb528ec95f7669507138cc2`.
- Restore the complete historical tree with Git from that commit if a rollback is required.
- Nothing under this directory participates in normal startup or imports.
- Heavy local data (`.venv`, Hugging Face caches, trained weights, generated audio and logs) is intentionally not archived.

The active project uses the persistent `qwentts.cpp` server and a lightweight HTTP facade. Do not add this directory to active Python paths.
