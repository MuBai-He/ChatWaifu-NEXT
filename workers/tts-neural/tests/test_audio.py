"""PCM encoding regressions shared by the neural TTS engines."""

# NumPy's buffer overload depends on the untyped stdlib wave reader.
# pyright: reportUnknownMemberType=false

import io
import wave

import numpy as np

from chatwaifu_tts_neural_worker.audio import wave_bytes


def _decode_pcm16(audio: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(audio), "rb") as source:
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2
        sample_rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    return np.frombuffer(frames, dtype=np.int16), sample_rate


def test_wave_bytes_preserves_existing_pcm16_without_rescaling() -> None:
    original = np.asarray([-32768, -2048, -1, 0, 1, 2048, 32767], dtype=np.int16)

    encoded, duration_ms = wave_bytes(original, 1_000)
    decoded, sample_rate = _decode_pcm16(encoded)

    assert sample_rate == 1_000
    assert duration_ms == 7
    np.testing.assert_array_equal(decoded, original)


def test_wave_bytes_converts_normalized_floats_and_sanitizes_nonfinite_values() -> None:
    normalized = np.asarray([-2.0, -0.5, 0.0, 0.5, 2.0, np.nan], dtype=np.float32)

    encoded, _ = wave_bytes(normalized, 24_000)
    decoded, _ = _decode_pcm16(encoded)

    np.testing.assert_array_equal(
        decoded,
        np.asarray([-32767, -16383, 0, 16383, 32767, 0], dtype=np.int16),
    )
