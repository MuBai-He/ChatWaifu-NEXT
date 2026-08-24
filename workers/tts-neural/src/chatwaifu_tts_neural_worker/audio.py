"""Dependency-light PCM/WAV conversion helpers."""

# NumPy's shape-generic overloads are intentionally broader than this mono helper.
# pyright: reportUnknownMemberType=false

import io
import wave
from typing import Any

import numpy as np
from numpy.typing import NDArray


def wave_bytes(samples: NDArray[Any], sample_rate: int) -> tuple[bytes, int]:
    flattened: NDArray[np.float64] = np.asarray(samples, dtype=np.float64).reshape(-1)
    pcm: NDArray[np.int16] = (np.clip(flattened, -1.0, 1.0) * 32767).astype(np.int16, copy=False)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())
    duration_ms = round(len(pcm) * 1000 / sample_rate)
    return buffer.getvalue(), duration_ms
