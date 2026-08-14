from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import urllib.request
from pathlib import Path


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

    # silero.silero_te() otherwise downloads this catalogue into the current
    # working directory. Provision it during install so requests stay offline.
    # silero.py resolves ../../models.yml from the package directory, which is
    # the venv Lib directory on Windows (not site-packages).
    catalogue = Path(silero.__file__).resolve().parents[2] / "models.yml"
    if not catalogue.is_file():
        catalogue.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(
            "https://raw.githubusercontent.com/snakers4/silero-models/master/models.yml",
            catalogue,
        )

    accentor = silero_stress.load_accentor()
    if not callable(accentor):
        raise RuntimeError("Silero Stress returned an invalid accentor")
    loaded_te = silero.silero_te()
    te_model = loaded_te[0] if isinstance(loaded_te, tuple) else loaded_te
    if not callable(getattr(te_model, "enhance_text", None)):
        raise RuntimeError("Silero Text Enhancement returned an invalid model")

    roots = [Path(silero.__file__).resolve().parent, Path(silero_stress.__file__).resolve().parent]
    model_files = sorted({
        str(path.resolve())
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() not in {".py", ".pyc"}
    } | {str(catalogue.resolve())})
    if not model_files:
        raise RuntimeError("Silero provisioning did not produce persistent model assets")

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "schema": 1,
        "torch": importlib.metadata.version("torch"),
        "torch_cuda": None,
        "silero_stress": importlib.metadata.version("silero-stress"),
        "silero": importlib.metadata.version("silero"),
        "catalogue": str(catalogue),
        "catalogue_sha256": hashlib.sha256(catalogue.read_bytes()).hexdigest(),
        "model_files": model_files,
    }
    temporary = Path(f"{output}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(f"Silero CPU preprocessing provisioned: {len(model_files)} model files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
