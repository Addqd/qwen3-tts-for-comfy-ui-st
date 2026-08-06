from __future__ import annotations

import argparse
import json
from pathlib import Path

from qwen3_tts_st.voices import VoiceLibrary


def create_profiles(workspace: Path, library_root: Path, overwrite: bool) -> list[dict]:
    manifest = json.loads((workspace / "selection_manifest.json").read_text(encoding="utf-8"))
    library = VoiceLibrary(library_root)
    created: list[dict] = []
    for family in manifest["voice_families"]:
        for sample in family["samples"]:
            if sample["selection_status"] != "primary":
                continue
            sample_id = sample["sample_id"]
            metadata = json.loads((workspace / "metadata" / f"{sample_id}.json").read_text(encoding="utf-8"))
            style = sample["mapped_emotion"]
            display_name = f"{family['voice_family']}_{style}"
            profile, validation = library.create(
                workspace / "prepared" / f"{sample_id}.wav",
                {
                    "character": family["character_name"],
                    "profile_id": display_name,
                    "display_name": display_name,
                    "style": style,
                    "ref_text": metadata["exact_transcript"],
                    "language": "Russian",
                    "clone_mode": "icl",
                    "notes": (
                        f"Temporary public-dataset test profile. Source: {metadata['original_dataset']} "
                        f"at revision {metadata['source_revision']}; sample {sample_id}. "
                        "Dataset transcript has not yet been verified by project-side listening."
                    ),
                },
                overwrite=overwrite,
            )
            created.append({
                "voice_id": profile.voice_id,
                "character": profile.character,
                "style": profile.style,
                "sample_id": sample_id,
                "validation": validation,
            })
    report = workspace / "reports" / "created_test_profiles.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(created, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Create ignored temporary profiles from selected public samples.")
    parser.add_argument("--workspace", type=Path, default=Path("local_voice_samples"))
    parser.add_argument("--library", type=Path, default=Path("voice_library"))
    parser.add_argument("--consent-confirmed", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not args.consent_confirmed:
        parser.error("--consent-confirmed is required; verify dataset consent and license first")
    created = create_profiles(args.workspace.resolve(), args.library.resolve(), args.overwrite)
    print(json.dumps({"created": len(created), "profiles": created}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
