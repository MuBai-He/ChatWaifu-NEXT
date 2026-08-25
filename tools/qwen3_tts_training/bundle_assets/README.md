# Ayachi Nene Qwen3-TTS local training bundle

This bundle is a local research artifact prepared for ChatWaifu NEXT. It contains
only the conservatively selected `nen` speaker clips, deterministic metadata, and
the scripts needed to materialize 24 kHz WAV files and fine-tune Qwen3-TTS in
Google Colab.

## Important rights boundary

The source repository says the audio and text were extracted from the commercial
game _Sabbat of the Witch_. The Hugging Face page declares CC-BY-NC-ND-4.0, but
that declaration does not prove that the uploader can relicense the underlying
game audio. Keep the source, converted data, checkpoints, and generated voice
local. Do not redistribute or use them commercially without permission from the
rightsholders.

## Colab flow

1. Upload the adjacent `.zip` bundle to a GPU-enabled Colab runtime.
2. Open `train_qwen3_tts_colab.ipynb` in Colab.
3. Run the cells from top to bottom.
4. Read the GPU preflight result before starting training.
5. Export the selected checkpoint to your private Google Drive.

The notebook defaults to `Qwen/Qwen3-TTS-12Hz-1.7B-Base` because the upstream
0.6B fine-tuning path still has unresolved compatibility reports. The notebook
uses a pinned upstream commit and a ChatWaifu-owned training driver with the text
projection and label alignment made explicit.

## Bundle layout

```text
source_audio/                 selected Opus clips
metadata/selected_records.jsonl
metadata/review_records.jsonl
metadata/rejected_records.jsonl
reports/review.csv
scripts/materialize_dataset.py
scripts/sft_12hz_chatwaifu.py
eval/prompts.json
bundle_manifest.json
train_qwen3_tts_colab.ipynb
```

`scripts/materialize_dataset.py` converts only selected clips to mono PCM16 WAV
at 24 kHz and writes the official `audio`, `text`, `ref_audio` JSONL format. The
same fixed reference clip is used for every record.
