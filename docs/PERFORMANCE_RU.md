# Производительность qwentts.cpp

Одинаковый representative Russian input и основной профиль `clone:test_ru_dima_neutral`:

- legacy Python backend: 1011.5 с, HTTP timeout, аудио не возвращено;
- qwentts.cpp CUDA warm: 3.537 с;
- длительность готового WAV: 13.52 с;
- warm RTF: 0.262;
- нижняя граница ускорения: 286×.

Финальный input прошёл точный UTF-8 file/JSON round-trip. Ранние measurements с повреждённым Windows text input аннулированы.

Pinned qwentts revision: `7b6ed4f6db964c14fd3ac36c1ca13f1ce6150f4e`.

Полный runtime provenance и ожидаемые hashes находятся в `config/qwentts-runtime.json`; многогигабайтные GGUF и binaries хранятся в ignored `runtime/qwentts/`.
