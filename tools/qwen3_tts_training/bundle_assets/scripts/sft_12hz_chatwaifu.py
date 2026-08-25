# Copyright 2026 The Alibaba Qwen team and ChatWaifu contributors.
# SPDX-License-Identifier: Apache-2.0

"""ChatWaifu Qwen3-TTS single-speaker SFT driver.

Based on QwenLM/Qwen3-TTS finetuning/sft_12hz.py at commit
022e286b98fbec7e1e916cb940cdf532cd9f488e. The upstream source is Apache-2.0.
This driver makes the text projection explicit, supports configurable precision
and attention, freezes the reference speaker encoder, and records run metadata.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import torch
from accelerate import Accelerator
from dataset import TTSDataset
from qwen_tts.inference.qwen3_tts_model import Qwen3TTSModel
from safetensors.torch import save_file
from torch.optim import AdamW
from torch.utils.data import DataLoader
from transformers import AutoConfig

QWEN_UPSTREAM_COMMIT = "022e286b98fbec7e1e916cb940cdf532cd9f488e"


def train() -> None:
    args = _parse_args()
    model_path = Path(args.init_model_path).resolve()
    output_path = Path(args.output_model_path).resolve()
    if not (model_path / "config.json").is_file():
        raise FileNotFoundError(
            "--init_model_path must be a downloaded local Hugging Face snapshot"
        )
    if not torch.cuda.is_available():
        raise RuntimeError("This training driver requires an NVIDIA CUDA runtime")

    precision = _resolve_precision(args.mixed_precision)
    torch_dtype = torch.bfloat16 if precision == "bf16" else torch.float16
    accelerator = Accelerator(
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        mixed_precision=precision,
        log_with="tensorboard",
        project_dir=str(output_path / "logs"),
    )
    qwen3tts = Qwen3TTSModel.from_pretrained(
        str(model_path),
        dtype=torch_dtype,
        attn_implementation=args.attention_implementation,
    )
    config = AutoConfig.from_pretrained(str(model_path))
    qwen3tts.model.speaker_encoder.requires_grad_(False)
    qwen3tts.model.speaker_encoder.eval()
    if args.gradient_checkpointing:
        qwen3tts.model.gradient_checkpointing_enable()

    with Path(args.train_jsonl).open(encoding="utf-8") as source:
        train_data = [json.loads(line) for line in source if line.strip()]
    if not train_data:
        raise ValueError("Training JSONL is empty")
    dataset = TTSDataset(train_data, qwen3tts.processor, config)
    train_dataloader = DataLoader(
        dataset,
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=dataset.collate_fn,
        num_workers=args.data_workers,
        pin_memory=True,
    )
    trainable = [parameter for parameter in qwen3tts.model.parameters() if parameter.requires_grad]
    optimizer = AdamW(trainable, lr=args.lr, weight_decay=args.weight_decay)
    model, optimizer, train_dataloader = accelerator.prepare(
        qwen3tts.model,
        optimizer,
        train_dataloader,
    )
    model.train()
    model.speaker_encoder.eval()
    target_speaker_embedding: torch.Tensor | None = None
    global_step = 0
    output_path.mkdir(parents=True, exist_ok=True)
    _write_run_metadata(output_path, args, precision, len(train_data))

    for epoch in range(args.num_epochs):
        for step, batch in enumerate(train_dataloader):
            with accelerator.accumulate(model):
                input_ids = batch["input_ids"]
                codec_ids = batch["codec_ids"]
                ref_mels = batch["ref_mels"]
                text_embedding_mask = batch["text_embedding_mask"]
                codec_embedding_mask = batch["codec_embedding_mask"]
                attention_mask = batch["attention_mask"]
                codec_0_labels = batch["codec_0_labels"]
                codec_mask = batch["codec_mask"]

                with torch.no_grad():
                    speaker_embedding = model.speaker_encoder(
                        ref_mels.to(model.device).to(model.dtype)
                    )
                if target_speaker_embedding is None:
                    target_speaker_embedding = speaker_embedding[0].detach().clone()

                input_text_ids = input_ids[:, :, 0]
                input_codec_ids = input_ids[:, :, 1]
                input_text_embedding = (
                    model.talker.text_projection(model.talker.model.text_embedding(input_text_ids))
                    * text_embedding_mask
                )
                input_codec_embedding = (
                    model.talker.model.codec_embedding(input_codec_ids) * codec_embedding_mask
                )
                input_codec_embedding[:, 6, :] = speaker_embedding
                input_embeddings = input_text_embedding + input_codec_embedding

                for index in range(1, 16):
                    codec_embedding = model.talker.code_predictor.get_input_embeddings()[index - 1](
                        codec_ids[:, :, index]
                    )
                    input_embeddings = input_embeddings + codec_embedding * codec_mask.unsqueeze(-1)

                outputs = model.talker(
                    inputs_embeds=input_embeddings[:, :-1, :],
                    attention_mask=attention_mask[:, :-1],
                    labels=codec_0_labels[:, 1:],
                    output_hidden_states=True,
                )
                hidden_states = outputs.hidden_states[0][-1]
                talker_hidden_states = hidden_states[codec_mask[:, :-1]]
                talker_codec_ids = codec_ids[codec_mask]
                _, sub_talker_loss = model.talker.forward_sub_talker_finetune(
                    talker_codec_ids,
                    talker_hidden_states,
                )
                loss = outputs.loss + args.sub_talker_weight * sub_talker_loss
                accelerator.backward(loss)
                if accelerator.sync_gradients:
                    accelerator.clip_grad_norm_(trainable, args.max_grad_norm)
                optimizer.step()
                optimizer.zero_grad()

            global_step += 1
            if step % args.log_every == 0:
                accelerator.print(
                    f"epoch={epoch} step={step} global_step={global_step} loss={loss.item():.5f}"
                )
            if args.max_steps > 0 and global_step >= args.max_steps:
                break

        if accelerator.is_main_process:
            if target_speaker_embedding is None:
                raise RuntimeError("No speaker embedding was produced")
            _save_checkpoint(
                model=model,
                accelerator=accelerator,
                base_model=model_path,
                destination=output_path / f"checkpoint-epoch-{epoch}",
                speaker_name=args.speaker_name,
                speaker_embedding=target_speaker_embedding,
            )
        accelerator.wait_for_everyone()
        if args.max_steps > 0 and global_step >= args.max_steps:
            break


def _save_checkpoint(
    *,
    model: torch.nn.Module,
    accelerator: Accelerator,
    base_model: Path,
    destination: Path,
    speaker_name: str,
    speaker_embedding: torch.Tensor,
) -> None:
    shutil.copytree(base_model, destination, dirs_exist_ok=True)
    config_path = destination / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["tts_model_type"] = "custom_voice"
    talker_config = config.setdefault("talker_config", {})
    talker_config["spk_id"] = {speaker_name: 3000}
    talker_config["spk_is_dialect"] = {speaker_name: False}
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    unwrapped = accelerator.unwrap_model(model)
    state_dict = {
        key: value.detach().to("cpu")
        for key, value in unwrapped.state_dict().items()
        if not key.startswith("speaker_encoder")
    }
    weight = state_dict["talker.model.codec_embedding.weight"]
    weight[3000] = speaker_embedding.detach().to(weight.device).to(weight.dtype)
    save_file(state_dict, destination / "model.safetensors")


def _write_run_metadata(
    output_path: Path,
    args: argparse.Namespace,
    precision: str,
    sample_count: int,
) -> None:
    payload = {
        "schema_version": "1.0",
        "qwen_upstream_commit": QWEN_UPSTREAM_COMMIT,
        "base_model_path": str(Path(args.init_model_path).resolve()),
        "speaker_name": args.speaker_name,
        "sample_count": sample_count,
        "batch_size": args.batch_size,
        "gradient_accumulation_steps": args.gradient_accumulation_steps,
        "learning_rate": args.lr,
        "epochs": args.num_epochs,
        "max_steps": args.max_steps,
        "mixed_precision": precision,
        "attention_implementation": args.attention_implementation,
        "gradient_checkpointing": args.gradient_checkpointing,
    }
    (output_path / "training_run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _resolve_precision(requested: str) -> str:
    if requested != "auto":
        return requested
    return "bf16" if torch.cuda.is_bf16_supported() else "fp16"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init_model_path", required=True)
    parser.add_argument("--output_model_path", required=True)
    parser.add_argument("--train_jsonl", required=True)
    parser.add_argument("--speaker_name", default="ayachi_nene_local")
    parser.add_argument("--batch_size", type=int, default=1)
    parser.add_argument("--gradient_accumulation_steps", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-6)
    parser.add_argument("--weight_decay", type=float, default=0.01)
    parser.add_argument("--num_epochs", type=int, default=2)
    parser.add_argument("--max_steps", type=int, default=0)
    parser.add_argument("--max_grad_norm", type=float, default=1.0)
    parser.add_argument("--sub_talker_weight", type=float, default=0.3)
    parser.add_argument("--mixed_precision", choices=("auto", "bf16", "fp16"), default="auto")
    parser.add_argument(
        "--attention_implementation",
        choices=("flash_attention_2", "sdpa"),
        default="flash_attention_2",
    )
    parser.add_argument("--data_workers", type=int, default=2)
    parser.add_argument("--log_every", type=int, default=10)
    parser.add_argument(
        "--gradient_checkpointing", action=argparse.BooleanOptionalAction, default=True
    )
    return parser.parse_args()


if __name__ == "__main__":
    train()
