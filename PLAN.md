# Production plan

1. Keep official binary/model hashes pinned and verified before startup.
2. Keep one persistent CUDA qwentts process and one lightweight facade.
3. Keep voice identity shared across API, SillyTavern and ComfyUI.
4. Validate managed ComfyUI synchronization and `/object_info` after schema changes.
5. Preserve the legacy snapshot only as a manual Git-restorable fallback.
