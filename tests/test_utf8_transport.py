from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_quality_inputs_are_exact_utf8_without_bom_and_http_json_round_trip():
    path = ROOT / "scripts" / "qwentts-quality-inputs.json"
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    decoded = raw.decode("utf-8")
    assert decoded.encode("utf-8") == raw
    samples = json.loads(decoded)
    expected = "Я внимательно проверил все изменения и убедился, что система работает стабильно. Теперь можно спокойно продолжить работу: настройки сохранены, нужные файлы находятся на своих местах, а следующий запуск не потребует ручного восстановления."
    assert samples["sample-3"] == expected
    body = json.dumps({"input": expected}, ensure_ascii=False).encode("utf-8")
    assert json.loads(body.decode("utf-8"))["input"] == expected
    assert hashlib.sha256(expected.encode("utf-8")).hexdigest() == "5c363a351f1929a0d77061b0b74e7cdfdf683f60dc81dcbe30e29c81c279c0f0"
