"""Dependency-light PCM/WAV conversion helpers."""

# NumPy's shape-generic overloads are intentionally broader than this mono helper.
# pyright: reportUnknownMemberType=false

import io
import wave
from typing import Any

import numpy as np
from numpy.typing import NDArray


def wave_bytes(samples: NDArray[Any], sample_rate: int) -> tuple[bytes, int]:
    pcm = pcm16_bytes(samples)
    return wave_bytes_from_pcm(pcm, sample_rate, channels=1)


def pcm16_bytes(samples: NDArray[Any]) -> bytes:
    source = np.asarray(samples).reshape(-1)
    if source.dtype == np.dtype(np.int16):
        # GPT-SoVITS already returns PCM16. Treating these values as normalized
        # floats would clip nearly every non-zero sample into a full-scale square
        # wave and produce extremely loud distortion.
        pcm = np.ascontiguousarray(source, dtype=np.int16)
    elif np.issubdtype(source.dtype, np.signedinteger):
        info = np.iinfo(source.dtype)
        scale = float(max(abs(info.min), info.max))
        normalized = source.astype(np.float64) / scale
        pcm = _normalized_float_to_pcm16(normalized)
    elif np.issubdtype(source.dtype, np.floating):
        pcm = _normalized_float_to_pcm16(source.astype(np.float64, copy=False))
    else:
        raise TypeError(f"unsupported audio sample dtype: {source.dtype}")
    return pcm.tobytes()


def wave_bytes_from_pcm(pcm16: bytes, sample_rate: int, channels: int) -> tuple[bytes, int]:
    if channels not in (1, 2):
        raise ValueError("channels must be 1 or 2")
    if not pcm16 or len(pcm16) % (channels * 2):
        raise ValueError("PCM16 audio must contain aligned frames")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm16)
    duration_ms = round(len(pcm16) * 1000 / (sample_rate * channels * 2))
    return buffer.getvalue(), duration_ms


def _normalized_float_to_pcm16(samples: NDArray[np.float64]) -> NDArray[np.int16]:
    finite = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
    return (np.clip(finite, -1.0, 1.0) * 32767).astype(np.int16, copy=False)
