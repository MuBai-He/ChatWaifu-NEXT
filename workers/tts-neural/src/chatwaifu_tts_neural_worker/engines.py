"""Heavy engine SDK adapters; imports remain isolated inside this worker."""

# Third-party model projects intentionally do not expose stable typing metadata.
# pyright: reportMissingImports=false, reportMissingModuleSource=false
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

import gc
import os
import sys
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
from chatwaifu_model_worker import TtsSynthesisRequest
from numpy.typing import NDArray

from chatwaifu_tts_neural_worker.audio import wave_bytes
from chatwaifu_tts_neural_worker.config import WorkerSettings


class SynthesisCancelled(Exception):
    pass


class SynthesisEngine(Protocol):
    @property
    def device(self) -> str: ...

    def synthesize(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> tuple[bytes, int, int]: ...

    def cancel(self) -> None: ...

    def unload(self) -> None: ...


def build_engine(settings: WorkerSettings) -> SynthesisEngine:
    if settings.backend == "qwen3_tts_mlx":
        return QwenMlxEngine(settings)
    return GptSovitsEngine(settings)


class QwenMlxEngine:
    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        _prepend_import_path(settings.vendor_dir)
        from tqdm import tqdm

        tqdm.monitor_interval = 0
        from mlx_audio.tts.utils import load_model

        self._model: Any = load_model(Path(cast(Path, settings.model_dir)))

    @property
    def device(self) -> str:
        return "mlx"

    def synthesize(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> tuple[bytes, int, int]:
        language = {"zh": "Chinese", "ja": "Japanese", "en": "English"}.get(
            request.language, "Auto"
        )
        generator: Generator[Any, None, None] = self._model.generate(
            text=request.text,
            lang_code=language,
            ref_audio=str(self._settings.reference_audio),
            ref_text=self._settings.reference_text,
            # MLX-Audio accepts this argument, but the selected Qwen checkpoint
            # does not provide a stable duration-control contract.
            speed=1.0,
            temperature=self._settings.temperature,
            stream=True,
            streaming_interval=self._settings.streaming_interval,
            verbose=False,
        )
        chunks: list[NDArray[np.float32]] = []
        sample_rate = int(self._model.sample_rate)
        try:
            for result in generator:
                if cancel_event.is_set():
                    raise SynthesisCancelled
                sample_rate = int(result.sample_rate)
                chunks.append(np.asarray(result.audio, dtype=np.float32))
            if cancel_event.is_set():
                raise SynthesisCancelled
        finally:
            generator.close()
            self._reset_streaming_state()
        if not chunks:
            raise RuntimeError("Qwen3-TTS returned no audio")
        audio, duration_ms = wave_bytes(np.concatenate(chunks), sample_rate)
        return audio, sample_rate, duration_ms

    def cancel(self) -> None:
        return None

    def unload(self) -> None:
        self._reset_streaming_state()
        self._model = None
        gc.collect()
        try:
            import mlx.core as mx

            mx.clear_cache()
        except ImportError:
            pass

    def _reset_streaming_state(self) -> None:
        model = self._model
        if model is None:
            return
        decoder = getattr(getattr(model, "speech_tokenizer", None), "decoder", None)
        reset = getattr(decoder, "reset_streaming_state", None)
        if callable(reset):
            reset()


class GptSovitsEngine:
    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        _prepend_import_path(settings.vendor_dir)
        _prepend_import_path(settings.vendor_dir / "GPT_SoVITS")
        os.chdir(settings.vendor_dir)
        from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

        pretrained = settings.vendor_dir / "GPT_SoVITS" / "pretrained_models"
        config = TTS_Config(
            {
                "custom": {
                    "device": "cpu",
                    "is_half": False,
                    "version": "v2ProPlus",
                    "t2s_weights_path": str(settings.gpt_weights),
                    "vits_weights_path": str(settings.sovits_weights),
                    "cnhuhbert_base_path": str(pretrained / "chinese-hubert-base"),
                    "bert_base_path": str(pretrained / "chinese-roberta-wwm-ext-large"),
                }
            }
        )
        self._pipeline: Any = TTS(config)

    @property
    def device(self) -> str:
        return "cpu"

    def synthesize(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> tuple[bytes, int, int]:
        language = request.language if request.language in {"zh", "ja", "en"} else "auto"
        generator = self._pipeline.run(
            {
                "text": request.text,
                "text_lang": language,
                "ref_audio_path": str(self._settings.reference_audio),
                "aux_ref_audio_paths": [],
                "prompt_text": self._settings.reference_text,
                "prompt_lang": self._settings.reference_language,
                "top_k": 15,
                "top_p": 1.0,
                "temperature": self._settings.temperature,
                "text_split_method": "cut5",
                "batch_size": 1,
                "batch_threshold": 0.75,
                "split_bucket": True,
                # The CPUFast v2ProPlus branch returns a silent fallback for
                # non-1.0 speed because its latent lengths diverge. The worker
                # advertises speed as unsupported and preserves valid audio.
                "speed_factor": 1.0,
                "fragment_interval": 0.3,
                "seed": -1,
                "parallel_infer": True,
                "vits_parallel_infer": True,
                "repetition_penalty": 1.35,
                "return_fragment": False,
                "streaming_mode": False,
            }
        )
        try:
            sample_rate, samples = next(generator)
            if cancel_event.is_set():
                raise SynthesisCancelled
        finally:
            generator.close()
        samples_array = np.asarray(samples)
        if samples_array.size == 0 or float(np.max(np.abs(samples_array))) == 0:
            raise RuntimeError("GPT-SoVITS returned silent fallback audio")
        audio, duration_ms = wave_bytes(samples_array, int(sample_rate))
        return audio, int(sample_rate), duration_ms

    def cancel(self) -> None:
        pipeline = self._pipeline
        if pipeline is not None:
            pipeline.stop()

    def unload(self) -> None:
        self.cancel()
        self._pipeline = None
        gc.collect()
        try:
            import torch

            if hasattr(torch, "mps") and torch.backends.mps.is_available():
                torch.mps.empty_cache()
        except ImportError:
            pass


def _prepend_import_path(path: Path) -> None:
    resolved = str(path.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
