from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import torch
from qwen_tts.core.models.modeling_qwen3_tts import mel_spectrogram
from torch.utils.data import Dataset

from training.russian_adaptation.common import project_path


class RussianAdaptationDataset(Dataset):
    """Official 12Hz SFT layout with explicit Russian conditioning and per-row references."""

    def __init__(self, rows: list[dict[str, Any]], processor: Any, config: Any):
        self.rows = rows
        self.processor = processor
        self.config = config
        language_ids = config.talker_config.codec_language_id
        if "russian" not in language_ids:
            raise ValueError("Base model config does not expose a Russian language codec id")
        self.russian_language_id = int(language_ids["russian"])

    def __len__(self) -> int:
        return len(self.rows)

    def _text_ids(self, text: str) -> torch.Tensor:
        rendered = f"<|im_start|>assistant\n{text}<|im_end|>\n<|im_start|>assistant\n"
        tokens = self.processor(text=rendered, return_tensors="pt", padding=True)["input_ids"]
        if tokens.dim() == 1:
            tokens = tokens.unsqueeze(0)
        return tokens[:, :-5]

    @staticmethod
    @torch.inference_mode()
    def _reference_mel(path: Path) -> torch.Tensor:
        audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
        if sample_rate != 24000:
            raise ValueError(f"Reference is not 24 kHz: {path}")
        mono = np.mean(audio, axis=1, dtype=np.float32)
        return mel_spectrogram(
            torch.from_numpy(mono).unsqueeze(0),
            n_fft=1024,
            num_mels=128,
            sampling_rate=24000,
            hop_size=256,
            win_size=1024,
            fmin=0,
            fmax=12000,
        ).transpose(1, 2)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        row = self.rows[index]
        if row.get("language") != "Russian":
            raise ValueError(f"Unexpected training language for {row.get('id')}")
        codes = torch.tensor(row["audio_codes"], dtype=torch.long)
        if codes.dim() != 2 or codes.shape[1] != 16:
            raise ValueError(f"Expected [time, 16] audio codes for {row.get('id')}, got {tuple(codes.shape)}")
        return {
            "text_ids": self._text_ids(row["text"]),
            "audio_codes": codes,
            "ref_mel": self._reference_mel(project_path(row["reference_audio"])),
        }

    def collate_fn(self, batch: list[dict[str, torch.Tensor]]) -> dict[str, torch.Tensor]:
        lengths = [row["text_ids"].shape[1] + row["audio_codes"].shape[0] for row in batch]
        max_length = max(lengths) + 9
        batch_size = len(batch)
        input_ids = torch.zeros((batch_size, max_length, 2), dtype=torch.long)
        codec_ids = torch.zeros((batch_size, max_length, 16), dtype=torch.long)
        text_embedding_mask = torch.zeros((batch_size, max_length), dtype=torch.bool)
        codec_embedding_mask = torch.zeros((batch_size, max_length), dtype=torch.bool)
        codec_mask = torch.zeros((batch_size, max_length), dtype=torch.bool)
        attention_mask = torch.zeros((batch_size, max_length), dtype=torch.long)
        codec_0_labels = torch.full((batch_size, max_length), -100, dtype=torch.long)

        header = 9
        speaker_position = 7
        for index, row in enumerate(batch):
            text_ids = row["text_ids"]
            audio_codes = row["audio_codes"]
            text_length = text_ids.shape[1]
            codec_length = audio_codes.shape[0]
            audio_codec_0 = audio_codes[:, 0]

            input_ids[index, :3, 0] = text_ids[0, :3]
            input_ids[index, 3:8, 0] = self.config.tts_pad_token_id
            input_ids[index, 8, 0] = self.config.tts_bos_token_id
            input_ids[index, header : header + text_length - 3, 0] = text_ids[0, 3:]
            input_ids[index, header + text_length - 3, 0] = self.config.tts_eos_token_id
            input_ids[index, header + text_length - 2 : header + text_length + codec_length, 0] = (
                self.config.tts_pad_token_id
            )
            text_embedding_mask[index, : header + text_length + codec_length] = True

            input_ids[index, 3:9, 1] = torch.tensor(
                [
                    self.config.talker_config.codec_think_id,
                    self.config.talker_config.codec_think_bos_id,
                    self.russian_language_id,
                    self.config.talker_config.codec_think_eos_id,
                    0,
                    self.config.talker_config.codec_pad_id,
                ]
            )
            codec_bos_position = header + text_length - 2
            codec_start = header + text_length - 1
            codec_end = codec_start + codec_length
            input_ids[index, header : header + text_length - 3, 1] = self.config.talker_config.codec_pad_id
            input_ids[index, header + text_length - 3, 1] = self.config.talker_config.codec_pad_id
            input_ids[index, codec_bos_position, 1] = self.config.talker_config.codec_bos_id
            input_ids[index, codec_start:codec_end, 1] = audio_codec_0
            input_ids[index, codec_end, 1] = self.config.talker_config.codec_eos_token_id
            codec_0_labels[index, codec_start:codec_end] = audio_codec_0
            codec_0_labels[index, codec_end] = self.config.talker_config.codec_eos_token_id
            codec_ids[index, codec_start:codec_end, :] = audio_codes
            codec_embedding_mask[index, 3 : header + text_length + codec_length] = True
            codec_embedding_mask[index, speaker_position] = False
            codec_mask[index, codec_start:codec_end] = True
            attention_mask[index, : header + text_length + codec_length] = True

        return {
            "input_ids": input_ids,
            "ref_mels": torch.cat([row["ref_mel"] for row in batch], dim=0),
            "attention_mask": attention_mask,
            "text_embedding_mask": text_embedding_mask.unsqueeze(-1),
            "codec_embedding_mask": codec_embedding_mask.unsqueeze(-1),
            "codec_0_labels": codec_0_labels,
            "codec_ids": codec_ids,
            "codec_mask": codec_mask,
        }
