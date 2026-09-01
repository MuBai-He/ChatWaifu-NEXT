"""Heavy engine SDK adapters; imports remain isolated inside this worker."""

# Third-party model projects intentionally do not expose stable typing metadata.
# pyright: reportMissingImports=false, reportMissingModuleSource=false
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

import gc
import os
import sys
import threading
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import numpy as np
from chatwaifu_model_worker import TtsSynthesisRequest

from chatwaifu_tts_neural_worker.audio import pcm16_bytes, wave_bytes_from_pcm
from chatwaifu_tts_neural_worker.config import WorkerSettings


class SynthesisCancelled(Exception):
    pass


@dataclass(frozen=True, slots=True)
class EnginePcmChunk:
    pcm16: bytes
    sample_rate: int
    channels: Literal[1, 2] = 1


class SynthesisEngine(Protocol):
    @property
    def device(self) -> str: ...

    def synthesize(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> tuple[bytes, int, int]: ...

    def stream_pcm(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> Iterator[EnginePcmChunk]: ...

    def cancel(self) -> None: ...

    def unload(self) -> None: ...


def build_engine(settings: WorkerSettings) -> SynthesisEngine:
    if settings.backend == "qwen3_tts_mlx":
        return QwenMlxEngine(settings)
    if settings.backend == "qwen3_tts_torch":
        return QwenTorchEngine(settings)
    return GptSovitsEngine(settings)


def validate_runtime_accelerator(settings: WorkerSettings) -> None:
    """Fail startup before advertising a CUDA Worker that cannot execute.

    Model loading remains lazy so the desktop can appear without paying the full
    checkpoint load cost.  A real device allocation still proves that the
    packaged PyTorch runtime, driver, and selected CUDA device work together on
    the target machine.  This is intentionally stronger than checking
    ``torch.cuda.is_available()`` alone.
    """

    if settings.backend != "qwen3_tts_torch" or not settings.device.startswith("cuda"):
        return
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError(
            "Qwen3-TTS CUDA worker cannot start because PyTorch reports CUDA unavailable"
        )
    try:
        probe = torch.ones(1, device=settings.device)
        result = (probe + 1).item()
        torch.cuda.synchronize(settings.device)
        del probe
    except Exception as error:
        raise RuntimeError(
            f"Qwen3-TTS CUDA worker failed its {settings.device} execution probe: {error}"
        ) from error
    if result != 2:
        raise RuntimeError("Qwen3-TTS CUDA worker returned an invalid device probe result")


class QwenMlxEngine:
    def __init__(
        self,
        settings: WorkerSettings,
        model_loader: Callable[[Path], Any] | None = None,
    ) -> None:
        self._settings = settings
        if model_loader is None:
            _prepend_import_path(cast(Path, settings.vendor_dir))
            from tqdm import tqdm

            tqdm.monitor_interval = 0
            from mlx_audio.tts.utils import load_model

            model_loader = load_model
        assert model_loader is not None
        self._model: Any = model_loader(Path(cast(Path, settings.model_dir)))
        self._voice = _resolve_qwen_voice(self._model, settings.qwen_voice)

    @property
    def device(self) -> str:
        return "mlx"

    def synthesize(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> tuple[bytes, int, int]:
        chunks = list(self.stream_pcm(request, cancel_event))
        if not chunks:
            raise RuntimeError("Qwen3-TTS returned no audio")
        sample_rate = chunks[0].sample_rate
        if any(chunk.sample_rate != sample_rate or chunk.channels != 1 for chunk in chunks):
            raise RuntimeError("Qwen3-TTS changed PCM format during one synthesis")
        audio, duration_ms = wave_bytes_from_pcm(
            b"".join(chunk.pcm16 for chunk in chunks), sample_rate, channels=1
        )
        return audio, sample_rate, duration_ms

    def stream_pcm(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> Iterator[EnginePcmChunk]:
        language = {"zh": "Chinese", "ja": "Japanese", "en": "English"}.get(
            request.language, "Auto"
        )
        generation_options: dict[str, Any] = {
            "text": request.text,
            "lang_code": language,
            # MLX-Audio accepts this argument, but the selected Qwen checkpoint
            # does not provide a stable duration-control contract.
            "speed": 1.0,
            "temperature": self._settings.temperature,
            "stream": True,
            "streaming_interval": self._settings.streaming_interval,
            "verbose": False,
        }
        if self._voice is None:
            generation_options.update(
                {
                    "ref_audio": str(cast(Path, self._settings.reference_audio)),
                    "ref_text": cast(str, self._settings.reference_text),
                }
            )
        else:
            generation_options["voice"] = self._voice
        generator: Generator[Any, None, None] = self._model.generate(**generation_options)
        try:
            for result in generator:
                if cancel_event.is_set():
                    raise SynthesisCancelled
                pcm16 = pcm16_bytes(np.asarray(result.audio, dtype=np.float32))
                if pcm16:
                    yield EnginePcmChunk(pcm16=pcm16, sample_rate=int(result.sample_rate))
            if cancel_event.is_set():
                raise SynthesisCancelled
        finally:
            generator.close()
            self._reset_streaming_state()

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


class QwenTorchEngine:
    """Official Qwen3-TTS PyTorch adapter for Windows/Linux CUDA workers.

    The upstream wrapper currently returns a complete waveform.  ``stream_pcm``
    therefore emits one terminal PCM chunk and the capability endpoint advertises
    ``native_streaming=false``.  Cancellation still invalidates the generation
    immediately; a late native result is discarded before it can cross the Worker
    boundary.
    """

    def __init__(
        self,
        settings: WorkerSettings,
        model_loader: Callable[[Path], Any] | None = None,
    ) -> None:
        self._settings = settings
        model_dir = Path(cast(Path, settings.model_dir))
        if model_loader is None:
            import torch
            from qwen_tts import Qwen3TTSModel

            dtype = _resolve_torch_dtype(torch, settings.device, settings.qwen_dtype)

            def load(path: Path) -> Any:
                return Qwen3TTSModel.from_pretrained(
                    str(path),
                    device_map=settings.device,
                    dtype=dtype,
                    attn_implementation=settings.qwen_attn_implementation,
                )

            model_loader = load
        self._model: Any = model_loader(model_dir)
        self._model_device, self._model_parameter_devices = _validate_torch_model_device(
            self._model, settings.device
        )
        self._voice = _resolve_torch_qwen_voice(self._model, settings.qwen_voice)

    @property
    def device(self) -> str:
        return self._settings.device

    @property
    def model_device(self) -> str:
        return self._model_device

    @property
    def model_parameter_devices(self) -> tuple[str, ...]:
        return self._model_parameter_devices

    def synthesize(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> tuple[bytes, int, int]:
        chunk = self._generate_pcm(request, cancel_event)
        audio, duration_ms = wave_bytes_from_pcm(
            chunk.pcm16, chunk.sample_rate, channels=chunk.channels
        )
        return audio, chunk.sample_rate, duration_ms

    def stream_pcm(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> Iterator[EnginePcmChunk]:
        yield self._generate_pcm(request, cancel_event)

    def cancel(self) -> None:
        # The official wrapper does not expose a generation stopping criterion.
        # SynthesisService invalidates the generation and serializes the native
        # call, so this no-op cannot leak late audio or overlap another request.
        return None

    def unload(self) -> None:
        self._model = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def _generate_pcm(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> EnginePcmChunk:
        if cancel_event.is_set():
            raise SynthesisCancelled
        language = {"zh": "Chinese", "ja": "Japanese", "en": "English"}.get(
            request.language, "Auto"
        )
        generation_options: dict[str, Any] = {
            "text": request.text,
            "language": language,
            "non_streaming_mode": True,
            "do_sample": True,
            "temperature": self._settings.temperature,
        }
        if self._voice is not None:
            generation_options["speaker"] = self._voice
            wavs, sample_rate = self._model.generate_custom_voice(**generation_options)
        else:
            generation_options.update(
                {
                    "ref_audio": str(cast(Path, self._settings.reference_audio)),
                    "ref_text": cast(str, self._settings.reference_text),
                }
            )
            wavs, sample_rate = self._model.generate_voice_clone(**generation_options)
        if cancel_event.is_set():
            raise SynthesisCancelled
        if not wavs:
            raise RuntimeError("Qwen3-TTS Torch returned no audio")
        samples = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
        if samples.size == 0 or not np.isfinite(samples).all():
            raise RuntimeError("Qwen3-TTS Torch returned invalid audio")
        pcm16 = pcm16_bytes(samples)
        if not pcm16:
            raise RuntimeError("Qwen3-TTS Torch returned empty PCM")
        return EnginePcmChunk(pcm16=pcm16, sample_rate=int(sample_rate))


class GptSovitsEngine:
    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        vendor_dir = cast(Path, settings.vendor_dir)
        _prepend_import_path(vendor_dir)
        _prepend_import_path(vendor_dir / "GPT_SoVITS")
        os.chdir(vendor_dir)
        from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

        pretrained = vendor_dir / "GPT_SoVITS" / "pretrained_models"
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
        chunks = list(self.stream_pcm(request, cancel_event))
        if not chunks:
            raise RuntimeError("GPT-SoVITS returned no audio")
        sample_rate = chunks[0].sample_rate
        if any(chunk.sample_rate != sample_rate or chunk.channels != 1 for chunk in chunks):
            raise RuntimeError("GPT-SoVITS changed PCM format during one synthesis")
        audio, duration_ms = wave_bytes_from_pcm(
            b"".join(chunk.pcm16 for chunk in chunks), sample_rate, channels=1
        )
        return audio, sample_rate, duration_ms

    def stream_pcm(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> Iterator[EnginePcmChunk]:
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
                "return_fragment": True,
                "streaming_mode": True,
            }
        )
        emitted = False
        try:
            for sample_rate, samples in generator:
                if cancel_event.is_set():
                    raise SynthesisCancelled
                samples_array = np.asarray(samples)
                if samples_array.size == 0:
                    continue
                if float(np.max(np.abs(samples_array))) == 0:
                    continue
                pcm16 = pcm16_bytes(samples_array)
                if pcm16:
                    emitted = True
                    yield EnginePcmChunk(pcm16=pcm16, sample_rate=int(sample_rate))
            if cancel_event.is_set():
                raise SynthesisCancelled
        finally:
            generator.close()
        if not emitted:
            raise RuntimeError("GPT-SoVITS returned silent fallback audio")

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


def _resolve_qwen_voice(model: Any, configured_voice: str | None) -> str | None:
    model_type = str(getattr(getattr(model, "config", None), "tts_model_type", "base"))
    if model_type != "custom_voice":
        return None
    if configured_voice is None:
        raise RuntimeError("Qwen CustomVoice model requires qwen_voice in the local profile")
    supported = [str(voice) for voice in model.get_supported_speakers()]
    if supported and configured_voice not in supported:
        raise RuntimeError(
            f"Qwen voice {configured_voice!r} is not in the checkpoint speakers: {supported}"
        )
    return configured_voice


def _resolve_torch_qwen_voice(model: Any, configured_voice: str | None) -> str | None:
    if configured_voice is None:
        return None
    supported = [str(voice) for voice in model.get_supported_speakers()]
    if supported and configured_voice.casefold() not in {voice.casefold() for voice in supported}:
        raise RuntimeError(
            f"Qwen voice {configured_voice!r} is not in the checkpoint speakers: {supported}"
        )
    return configured_voice


def _resolve_torch_dtype(torch: Any, device: str, configured: str) -> Any:
    if configured != "auto":
        return getattr(torch, configured)
    if not device.startswith("cuda"):
        return torch.float32
    if torch.cuda.is_bf16_supported():
        return torch.bfloat16
    return torch.float16


def _validate_torch_model_device(model: Any, expected_device: str) -> tuple[str, tuple[str, ...]]:
    """Prove the loaded wrapper and every model parameter use the requested device."""

    wrapped_model = getattr(model, "model", None)
    parameters = getattr(wrapped_model, "parameters", None)
    if not callable(parameters):
        raise RuntimeError("Qwen3-TTS Torch model does not expose parameters for device validation")
    parameter_iterator = cast(Callable[[], Iterator[Any]], parameters)
    parameter_devices = tuple(
        sorted({str(getattr(parameter, "device", "unknown")) for parameter in parameter_iterator()})
    )
    if not parameter_devices:
        raise RuntimeError("Qwen3-TTS Torch model exposes no parameters for device validation")
    if parameter_devices != (expected_device,):
        raise RuntimeError(
            "Qwen3-TTS Torch model parameters are not entirely on "
            f"{expected_device}: {list(parameter_devices)}"
        )
    model_device = str(getattr(model, "device", parameter_devices[0]))
    if model_device != expected_device:
        raise RuntimeError(
            f"Qwen3-TTS Torch wrapper reports {model_device}, expected {expected_device}"
        )
    return model_device, parameter_devices


def collect_torch_runtime_diagnostics(
    settings: WorkerSettings, engine: SynthesisEngine | None
) -> dict[str, object] | None:
    """Capture current CUDA and model-placement evidence for Qwen Torch health."""

    if settings.backend != "qwen3_tts_torch":
        return None
    try:
        import torch
    except ImportError:
        return None

    cuda_available = bool(torch.cuda.is_available())
    diagnostics: dict[str, object] = {
        "torch_version": str(torch.__version__),
        "torch_cuda_version": (str(torch.version.cuda) if torch.version.cuda is not None else None),
        "cuda_available": cuda_available,
    }
    if cuda_available and settings.device.startswith("cuda"):
        device = torch.device(settings.device)
        device_index = device.index
        if device_index is None:
            device_index = int(torch.cuda.current_device())
        major, minor = torch.cuda.get_device_capability(device_index)
        free_memory, total_memory = torch.cuda.mem_get_info(device_index)
        diagnostics.update(
            {
                "cuda_device_index": device_index,
                "cuda_device_name": str(torch.cuda.get_device_name(device_index)),
                "cuda_compute_capability": f"{major}.{minor}",
                "cuda_total_memory_bytes": int(total_memory),
                "cuda_free_memory_bytes": int(free_memory),
                "cuda_memory_allocated_bytes": int(torch.cuda.memory_allocated(device_index)),
                "cuda_memory_reserved_bytes": int(torch.cuda.memory_reserved(device_index)),
            }
        )
    if engine is not None:
        model_device = getattr(engine, "model_device", None)
        parameter_devices = getattr(engine, "model_parameter_devices", ())
        if model_device is not None:
            diagnostics["model_device"] = str(model_device)
        diagnostics["model_parameter_devices"] = [str(item) for item in parameter_devices]
    return diagnostics
