from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROVENANCE_PATH = PROJECT_ROOT / "config" / "silero-runtime.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _download_verified(url: str, destination: Path, expected_sha256: str) -> None:
    if destination.is_file() and _digest(destination) == expected_sha256:
        return
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = response.read()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected_sha256:
        raise RuntimeError(f"Silero asset SHA-256 mismatch: expected {expected_sha256}, got {actual}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(f"{destination}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision project Silero CPU preprocessing models")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    import torch

    if torch.version.cuda is not None:
        raise RuntimeError("Silero provisioning requires the CPU-only PyTorch wheel")
    torch.set_num_threads(1)

    import silero
    import silero_stress
    import yaml

    provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    catalogue_provenance = provenance["catalogue"]
    te_provenance = provenance["text_enhancement_model"]

    # silero.silero_te() otherwise downloads this catalogue into the current
    # working directory. Provision it during install so requests stay offline.
    # silero.py resolves ../../models.yml from the package directory, which is
    # the venv Lib directory on Windows (not site-packages).
    catalogue = Path(silero.__file__).resolve().parents[2] / "models.yml"
    _download_verified(catalogue_provenance["url"], catalogue, catalogue_provenance["sha256"])
    catalogue_data = yaml.safe_load(catalogue.read_text(encoding="utf-8"))
    te_url = str(catalogue_data["te_models"]["latest"]["package"])
    if te_url != te_provenance["url"]:
        raise RuntimeError("Pinned Silero catalogue references an unexpected Text Enhancement model")
    te_model_path = Path(silero.__file__).resolve().parent / "model" / Path(te_url).name
    _download_verified(te_url, te_model_path, te_provenance["sha256"])

    accentor = silero_stress.load_accentor()
    if not callable(accentor):
        raise RuntimeError("Silero Stress returned an invalid accentor")
    loaded_te = silero.silero_te()
    te_model = loaded_te[0] if isinstance(loaded_te, tuple) else loaded_te
    if not callable(getattr(te_model, "enhance_text", None)):
        raise RuntimeError("Silero Text Enhancement returned an invalid model")

    te_root = Path(silero.__file__).resolve().parent
    stress_root = Path(silero_stress.__file__).resolve().parent
    stress_assets = sorted(
        str(path.resolve()) for path in stress_root.rglob("*")
        if path.is_file() and path.suffix.lower() not in {".py", ".pyc"}
    )
    text_enhancement_assets = sorted({
        str(path.resolve()) for path in te_root.rglob("*")
        if path.is_file() and path.suffix.lower() not in {".py", ".pyc"}
    } | {str(catalogue.resolve())})
    model_files = sorted(set(stress_assets) | set(text_enhancement_assets))
    if not model_files:
        raise RuntimeError("Silero provisioning did not produce persistent model assets")

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    asset_sha256 = {path: _digest(Path(path)) for path in model_files}
    state = {
        "schema": 3,
        "torch": importlib.metadata.version("torch"),
        "torch_cuda": None,
        "silero_stress": importlib.metadata.version("silero-stress"),
        "silero": importlib.metadata.version("silero"),
        "catalogue": str(catalogue),
        "catalogue_revision": catalogue_provenance["revision"],
        "catalogue_sha256": catalogue_provenance["sha256"],
        "te_model": str(te_model_path.resolve()),
        "te_model_sha256": te_provenance["sha256"],
        "model_files": model_files,
        "stress_assets": stress_assets,
        "text_enhancement_assets": text_enhancement_assets,
        "asset_sha256": asset_sha256,
    }
    temporary = Path(f"{output}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(f"Silero CPU preprocessing provisioned: {len(model_files)} model files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
