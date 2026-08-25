# Local Qwen3-TTS character fine-tuning

This workflow prepares a compact, reproducible Colab bundle without adding model
or dataset dependencies to Runtime. It is a development tool for evaluating a
private character voice; it does not change the versioned TTS provider contract.

## Scope

The current bundle targets the adult Ayachi Nene speaker code `nen` from
`KitsuneX07/Datasets_for_Sabbat_of_the_Witch`. It deliberately excludes `kne`
(child Nene), all other speakers, unpaired files, failed decodes, explicit adult
terms, ambiguous vocalizations, music-marked lines, borderline audio, and exact
duplicate audio.

The source repository states that the samples were extracted from the commercial
game _Sabbat of the Witch_. Hugging Face declares CC-BY-NC-ND-4.0, but that does
not establish that the uploader can relicense the underlying game audio. Dataset,
bundle, checkpoints, and generated voice remain local, uncommitted, private, and
non-commercial pending a separate rights review.

## Build the compact upload bundle

Requirements:

- the source ZIP under an ignored local directory;
- an `ffmpeg` binary capable of decoding Opus;
- Python 3.12 through the workspace `uv` environment.

```bash
uv run python -m tools.qwen3_tts_training.prepare_dataset \
  --archive .local/training/qwen3-nene/source/datasets_for_Sabbat_of_the_Witch.zip \
  --output .local/training/qwen3-nene/nene-qwen3-training-v1 \
  --ffmpeg /absolute/path/to/ffmpeg \
  --jobs 4
```

The command never extracts other speakers into the output bundle. It decodes
each candidate to 24 kHz PCM16 in memory for quality checks, but stores selected
source clips as Opus so the Colab upload remains compact. The generated ZIP uses
stable ordering and timestamps; an adjacent `.sha256` file verifies transport.

The output directory contains:

- `bundle_manifest.json`: source hash, pinned Qwen commit, filter counts, split
  counts, duration, and the fixed reference clip;
- `metadata/selected_records.jsonl`: samples used by training;
- `metadata/review_records.jsonl`: ambiguous samples excluded by default;
- `metadata/rejected_records.jsonl`: hard policy or audio failures;
- `reports/review.csv`: sortable audit report;
- `source_audio/`: only conservatively selected `nen` Opus clips;
- `train_qwen3_tts_colab.ipynb`: upload, materialization, training, evaluation,
  and private Drive export.

The train/validation/test assignment hashes the source scene key such as
`nen001`, so adjacent dialogue does not leak across splits. Every JSONL row uses
the same fixed clean reference clip (`nen104_084` for the audited source archive,
with deterministic quality-ranking fallback). The selection is recorded in the
manifest and may be overridden with `--reference-stem` after listening review.

## Run in Colab

1. Upload `train_qwen3_tts_colab.ipynb` as a Colab notebook.
2. Select a high-memory NVIDIA GPU runtime.
3. Run the notebook and upload the generated compact ZIP when prompted.
4. Read the rights notice and GPU preflight result.
5. Materialize WAVs and audio codes.
6. Run the default two-epoch full SFT, or set `MAX_STEPS=100` for a pilot.
7. Listen to every fixed Japanese and Chinese evaluation output.
8. Copy the chosen checkpoint to private Google Drive.

The notebook pins Qwen3-TTS commit
`022e286b98fbec7e1e916cb940cdf532cd9f488e` and downloads the base checkpoint to
a real local snapshot before training. This avoids the upstream saver treating a
Hugging Face model ID as a filesystem directory. The included SFT driver applies
the talker text projection before adding codec embeddings, keeps the corrected
one-token label shift, freezes the reference speaker encoder, enables gradient
checkpointing, and records the full run configuration.

## Model choice

The default is `Qwen/Qwen3-TTS-12Hz-1.7B-Base`. Upstream documentation names both
1.7B and 0.6B, but current 0.6B reports show dimension and output-quality failures
in the public fine-tuning path. The bundle therefore does not silently switch to
0.6B on a small Colab GPU. The notebook rejects insufficient VRAM before model
download or training instead of risking a late out-of-memory failure.

The existing ChatWaifu Demo runs a third-party MLX 8-bit 0.6B inference adapter.
A successful PyTorch 1.7B checkpoint is not immediately a drop-in replacement.
After listening evaluation, it still needs a separately validated Hugging Face to
MLX conversion/quantization step and a local voice profile update. Keep this
deployment work separate from training so an unverified checkpoint cannot become
the default voice.

## Evaluation gate

Do not choose a checkpoint only from training loss. Compare each candidate on:

- Japanese speaker similarity, pronunciation, naturalness, and emotional fit;
- Chinese pronunciation, intelligibility, and Japanese-accent leakage;
- speaking-rate drift across epochs;
- repeated or missing words on longer sentences;
- silence, clipping, broadband noise, and unstable output length;
- behavior on the same fixed prompt set in `eval/prompts.json`.

Only the selected checkpoint should proceed to conversion. Keep the base model,
per-epoch checkpoints, logs, dataset, and evaluation WAV files outside Git.
