# pyright: reportPrivateUsage=false
from __future__ import annotations

# Dedicated tests intentionally exercise the smoke tool's release-gate helpers.
import base64
import io
import json
import threading
import urllib.error
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from chatwaifu_model_worker import InstalledWorkerPack, WorkerPackManifest

from tools.windows import smoke_worker_pack as smoke


def _pcm_wave(samples: list[int], *, sample_rate: int = 8_000) -> bytes:
    payload = bytearray()
    for sample in samples:
        payload.extend(sample.to_bytes(2, byteorder="little", signed=True))
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(payload)
    return target.getvalue()


def _health(*, status: str, queue_depth: int, model_loaded: bool = True) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": status,
        "worker_id": "test-worker",
        "model_loaded": model_loaded,
        "model": "test-model",
        "queue_depth": queue_depth,
        "device": "cuda:0",
        "capabilities": ["tts.synthesize", "tts.cancel"],
    }


def _manifest() -> WorkerPackManifest:
    return WorkerPackManifest.model_validate(
        {
            "schema_version": "1.0",
            "pack_id": "test-tts-pack",
            "version": "1.2.3",
            "platform": {
                "os": "windows",
                "architecture": "x86_64",
                "accelerator": "cuda",
                "accelerator_version": "12.6",
                "python_abi": "cp312",
            },
            "worker": {
                "kind": "tts",
                "backend": "test-backend",
                "provider_id": "test-provider",
                "display_name": "Test provider",
                "model": "test-model",
                "entrypoint": {"executable": "payload/worker.exe"},
            },
            "files": [
                {
                    "path": "payload/worker.exe",
                    "size": 1,
                    "sha256": "0" * 64,
                    "role": "runtime",
                }
            ],
            "licenses": [{"name": "Test license"}],
        }
    )


def test_tts_smoke_records_latency_wave_metrics_and_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = _pcm_wave([1_000, -2_000, 32_767, -32_768] * 1_200)

    def synthesize(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        del url, headers, timeout
        return {
            "schema_version": "1.0",
            **{field: payload[field] for field in smoke._identity_fields()},
            "audio_base64": base64.b64encode(audio).decode("ascii"),
            "sample_rate": 8_000,
            "duration_ms": 600,
            "provider": "test-provider",
            "model": "test-model",
            "speaker_id": 0,
        }

    monkeypatch.setattr(smoke, "_post_json", synthesize)

    evidence = smoke._smoke_tts("http://worker", {}, tmp_path, timeout=5)

    assert [item["language"] for item in evidence] == ["zh", "ja"]
    for item in evidence:
        assert isinstance(item["latency_ms"], float)
        response = item["response"]
        assert isinstance(response, dict)
        assert "audio_base64" not in response
        metrics = cast(dict[str, object], item["audio"])
        assert metrics["duration_ms"] == 600
        assert metrics["peak_pcm16"] == 32_768
        assert metrics["clipped_sample_count"] == 2_400
        meaningful = sum(character.isalnum() for character in str(item["text"]))
        assert metrics["duration_acceptance_bounds_ms"] == {
            "minimum": max(250, meaningful * 40),
            "maximum": max(5_000, meaningful * 1_500),
        }
        assert metrics["file_bytes"] == len(audio)
        file_name = metrics["file_name"]
        assert isinstance(file_name, str)
        assert (tmp_path / file_name).read_bytes() == audio


def test_whisper_reasonableness_is_language_neutral_and_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wave_input = smoke.PcmWave(
        pcm16=b"\x01\x00" * 8_000,
        sample_rate=8_000,
        channels=1,
        frame_count=8_000,
    )

    def transcribe(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        del url, headers, timeout
        return {
            "schema_version": "1.0",
            **{field: payload[field] for field in smoke._identity_fields()},
            "text": "  Hello,   multilingual world!  ",
            "language": "en",
            "confidence": None,
            "duration_ms": 1_000,
            "provider": "faster-whisper",
        }

    monkeypatch.setattr(smoke, "_post_json", transcribe)

    evidence = smoke._smoke_stt(
        "http://worker",
        {},
        wave_input,
        "speech.wav",
        5,
        smoke.TranscriptExpectations(
            min_meaningful_characters=10,
            pattern=r"multilingual\s+world",
            language="en",
        ),
    )

    transcript = evidence["transcript"]
    assert isinstance(transcript, dict)
    assert transcript["text"] == "  Hello,   multilingual world!  "
    reasonableness = evidence["reasonableness"]
    assert isinstance(reasonableness, dict)
    assert reasonableness["meaningful_character_count"] == 22
    assert reasonableness["pattern_matched"] is True
    assert reasonableness["language_matched"] is True


@pytest.mark.parametrize(
    ("text", "language", "expectations", "message"),
    [
        (
            "!?",
            "ja",
            smoke.TranscriptExpectations(min_meaningful_characters=2),
            "not reasonable",
        ),
        (
            "hello world",
            "en",
            smoke.TranscriptExpectations(pattern="missing"),
            "did not match",
        ),
        (
            "hello world",
            "en",
            smoke.TranscriptExpectations(language="zh"),
            "unexpected language",
        ),
    ],
)
def test_whisper_reasonableness_rejects_configured_mismatch(
    text: str,
    language: str,
    expectations: smoke.TranscriptExpectations,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        smoke._assess_transcript(text, language, expectations)


def test_cancel_probe_waits_for_observable_busy_job_and_rejects_late_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_started = threading.Event()
    cancel_received = threading.Event()
    release_request = threading.Event()
    payload = smoke._tts_payload("zh", "取消我")

    def post_json(
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        del headers, timeout
        if url.endswith("/v1/synthesize"):
            request_started.set()
            if not release_request.wait(2):
                raise AssertionError("cancel endpoint did not release the in-flight request")
            raise urllib.error.URLError("generation_cancelled")
        assert url.endswith(f"/v1/jobs/{payload['generation_id']}/cancel")
        assert body == {}
        assert request_started.is_set()
        cancel_received.set()
        release_request.set()
        return {"generation_id": payload["generation_id"], "cancelled": True}

    def get_json(url: str, headers: dict[str, str], timeout: float) -> dict[str, object]:
        del url, headers, timeout
        assert request_started.wait(2)
        if not cancel_received.is_set():
            return _health(status="busy", queue_depth=1)
        return _health(status="ready", queue_depth=0)

    monkeypatch.setattr(smoke, "_post_json", post_json)
    monkeypatch.setattr(smoke, "_get_json", get_json)

    evidence = smoke._probe_in_flight_cancellation(
        "http://worker",
        "/v1/health",
        {},
        kind="tts",
        payload=payload,
        request_evidence={"kind": "tts", "text_character_count": 3},
        timeout=5,
    )

    assert evidence["observed_in_flight"] is True
    assert evidence["generation_id"] == payload["generation_id"]
    assert evidence["job_id"] == payload["job_id"]
    assert evidence["cancel_response"] == {
        "generation_id": payload["generation_id"],
        "cancelled": True,
    }
    timing = cast(dict[str, object], evidence["timing_ms"])
    assert all(isinstance(value, float) and value >= 0 for value in timing.values())
    outcome = evidence["request_outcome"]
    assert isinstance(outcome, dict)
    assert outcome["request_terminated_after_cancel"] is True
    assert outcome["successful_inference_returned"] is False
    assert outcome["transport"] == "connection_closed"
    semantics = cast(dict[str, object], evidence["semantics"])
    assert semantics == {
        "scope": "worker_request_boundary",
        "cancel_acknowledged": True,
        "successful_inference_result_suppressed": True,
        "native_execution_stop_observable": False,
    }


def test_cancel_probe_fails_if_cancelled_request_returns_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_started = threading.Event()
    release_request = threading.Event()
    payload = smoke._tts_payload("zh", "取消我")

    def post_json(
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        del headers, body, timeout
        if url.endswith("/v1/synthesize"):
            request_started.set()
            assert release_request.wait(2)
            return {"audio_base64": "late-audio"}
        release_request.set()
        return {"generation_id": payload["generation_id"], "cancelled": True}

    def get_json(url: str, headers: dict[str, str], timeout: float) -> dict[str, object]:
        del url, headers, timeout
        assert request_started.wait(2)
        return _health(status="busy", queue_depth=1)

    monkeypatch.setattr(smoke, "_post_json", post_json)
    monkeypatch.setattr(smoke, "_get_json", get_json)

    with pytest.raises(RuntimeError, match="successful inference result"):
        smoke._probe_in_flight_cancellation(
            "http://worker",
            "/v1/health",
            {},
            kind="tts",
            payload=payload,
            request_evidence={"kind": "tts"},
            timeout=5,
        )


def test_unload_requires_true_and_model_loaded_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def unload_false(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        del url, headers, payload, timeout
        return {"unloaded": False}

    def unload_true(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        del url, headers, payload, timeout
        return {"unloaded": True}

    def loaded_health(url: str, headers: dict[str, str], timeout: float) -> dict[str, object]:
        del url, headers, timeout
        return _health(status="ready", queue_depth=0, model_loaded=True)

    def unloaded_health(url: str, headers: dict[str, str], timeout: float) -> dict[str, object]:
        del url, headers, timeout
        return _health(status="ready", queue_depth=0, model_loaded=False)

    monkeypatch.setattr(smoke, "_post_json", unload_false)
    with pytest.raises(RuntimeError, match="did not return true"):
        smoke._unload_and_verify("http://worker", "/v1/health", {}, timeout=5)

    monkeypatch.setattr(smoke, "_post_json", unload_true)
    monkeypatch.setattr(smoke, "_get_json", loaded_health)
    with pytest.raises(RuntimeError, match="still reports a loaded model"):
        smoke._unload_and_verify("http://worker", "/v1/health", {}, timeout=5)

    monkeypatch.setattr(smoke, "_get_json", unloaded_health)
    evidence = smoke._unload_and_verify("http://worker", "/v1/health", {}, timeout=5)
    assert evidence["response"] == {"unloaded": True}
    health = evidence["health_after_unload"]
    assert isinstance(health, dict)
    assert health["model_loaded"] is False


def test_post_inference_health_must_report_model_loaded() -> None:
    with pytest.raises(RuntimeError, match="model_loaded=true after inference"):
        smoke._assert_model_loaded(
            _health(status="ready", queue_depth=0, model_loaded=False),
            stage="after inference",
        )


def test_inference_response_must_preserve_all_request_identities() -> None:
    request = smoke._tts_payload("ja", "こんにちは")
    response = {field: request[field] for field in smoke._identity_fields()}
    response["generation_id"] = "late-generation"

    with pytest.raises(RuntimeError, match="generation_id"):
        smoke._assert_response_identity(request, response, operation="TTS ja")


def test_smoke_pack_persists_structured_result_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest()
    archive = tmp_path / "candidate.cwpack"
    archive.write_bytes(b"test archive")
    installed = cast(
        InstalledWorkerPack,
        SimpleNamespace(
            manifest=manifest,
            root=tmp_path / "installed",
            receipt=SimpleNamespace(
                archive_sha256="a" * 64,
                manifest_sha256="b" * 64,
            ),
        ),
    )

    temporary_roots: list[Path] = []

    def install_archive(archive: Path, root: Path) -> InstalledWorkerPack:
        del archive
        temporary_roots.append(root.parent)
        return installed

    monkeypatch.setattr(smoke, "install_archive", install_archive)

    def smoke_extracted(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {
            "health": {"after_inference": _health(status="ready", queue_depth=0)},
            "capabilities": {"provider_id": "test-provider"},
            "device": {"reported": "cuda:0"},
            "inference": {"kind": "tts", "languages": []},
            "cancellation": {"observed_in_flight": True},
            "unload": {"response": {"unloaded": True}},
            "shutdown": {"listener_closed": True},
            "artifacts": {"worker_log": "test.log"},
        }

    monkeypatch.setattr(smoke, "_smoke_extracted", smoke_extracted)

    result = smoke.smoke_pack(
        archive,
        kind="tts",
        timeout=5,
        smoke_wav=None,
        output_directory=tmp_path / "smoke",
    )

    result_path = tmp_path / "smoke" / smoke.RESULT_FILE_NAME
    persisted: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
    assert persisted == result
    assert persisted["schema_version"] == "1.0"
    assert persisted["archive"] == {
        "file_name": "candidate.cwpack",
        "size_bytes": len(b"test archive"),
        "sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
    }
    pack = persisted["pack"]
    assert isinstance(pack, dict)
    assert pack["pack_id"] == "test-tts-pack"
    assert pack["version"] == "1.2.3"
    assert pack["kind"] == "tts"
    assert pack["backend"] == "test-backend"
    assert pack["provider_id"] == "test-provider"
    assert pack["model"] == "test-model"
    assert pack["platform"] == {
        "os": "windows",
        "architecture": "x86_64",
        "accelerator": "cuda",
        "accelerator_version": "12.6",
        "python_abi": "cp312",
    }
    assert pack["entrypoint_environment"] == {}
    assert pack["build_metadata"] is None
    assert pack["payload"] == {
        "file_count": 1,
        "expanded_size_bytes": 1,
        "model_file_count": 0,
        "model_size_bytes": 0,
    }
    assert persisted["artifacts"]["result_json"] == smoke.RESULT_FILE_NAME
    assert len(temporary_roots) == 1
    assert not temporary_roots[0].exists()


def test_smoke_pack_propagates_temporary_tree_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest()
    archive = tmp_path / "candidate.cwpack"
    archive.write_bytes(b"test archive")
    installed = cast(
        InstalledWorkerPack,
        SimpleNamespace(
            manifest=manifest,
            root=tmp_path / "installed",
            receipt=SimpleNamespace(
                archive_sha256="a" * 64,
                manifest_sha256="b" * 64,
            ),
        ),
    )
    temporary_roots: list[Path] = []

    def install_archive(_archive: Path, _root: Path) -> InstalledWorkerPack:
        temporary_roots.append(_root.parent)
        return installed

    def smoke_extracted(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {}

    monkeypatch.setattr(smoke, "install_archive", install_archive)
    monkeypatch.setattr(smoke, "_smoke_extracted", smoke_extracted)

    def fail_cleanup(_path: Path, *, missing_ok: bool = False) -> None:
        assert missing_ok is True
        raise RuntimeError("forced smoke cleanup failure")

    real_cleanup = smoke.remove_directory_tree
    monkeypatch.setattr(smoke, "remove_directory_tree", fail_cleanup)

    try:
        with pytest.raises(RuntimeError, match="forced smoke cleanup failure"):
            smoke.smoke_pack(
                archive,
                kind="tts",
                timeout=5,
                smoke_wav=None,
                output_directory=tmp_path / "smoke",
            )
    finally:
        for temporary_root in temporary_roots:
            real_cleanup(temporary_root, missing_ok=True)
