from __future__ import annotations

import hashlib

import pytest
from chatwaifu_model_worker import (
    WORKER_PACK_MAX_FILE_BYTES,
    WorkerPackActivationConfig,
    WorkerPackActiveSelection,
    WorkerPackEntrypoint,
    WorkerPackFile,
    WorkerPackLicense,
    WorkerPackManifest,
    WorkerPackPlatform,
    WorkerPackSelection,
    WorkerPackWorker,
)
from pydantic import ValidationError

_PAYLOAD = b"worker"


def _file(path: str = "bin/worker.exe") -> WorkerPackFile:
    return WorkerPackFile(
        path=path,
        size=len(_PAYLOAD),
        sha256=hashlib.sha256(_PAYLOAD).hexdigest(),
        role="runtime",
    )


def _manifest(**overrides: object) -> WorkerPackManifest:
    values: dict[str, object] = {
        "pack_id": "faster-whisper-cpu",
        "version": "1.2.3",
        "platform": WorkerPackPlatform(
            os="windows", architecture="x86_64", accelerator="cpu", python_abi="cp312"
        ),
        "worker": WorkerPackWorker(
            kind="stt",
            backend="faster_whisper",
            provider_id="faster-whisper",
            display_name="faster-whisper · CPU",
            model="base",
            entrypoint=WorkerPackEntrypoint(
                executable="bin/worker.exe",
                environment={
                    "CHATWAIFU_STT_WORKER_MODEL_DIR": "${PACK_ROOT}/models/base",
                    "CHATWAIFU_STT_WORKER_DEVICE": "cpu",
                },
            ),
        ),
        "files": [_file()],
        "licenses": [WorkerPackLicense(name="MIT")],
    }
    values.update(overrides)
    return WorkerPackManifest.model_validate(values)


def test_worker_pack_manifest_round_trips_strict_contract() -> None:
    manifest = _manifest()

    decoded = WorkerPackManifest.model_validate_json(manifest.model_dump_json())

    assert decoded == manifest
    assert decoded.schema_version == "1.0"
    assert decoded.worker.entrypoint.environment["CHATWAIFU_STT_WORKER_DEVICE"] == "cpu"


@pytest.mark.parametrize(
    "path",
    ["../worker.exe", "/worker.exe", "bin\\worker.exe", "bin//worker.exe", "CON/file"],
)
def test_worker_pack_file_rejects_nonportable_or_escaping_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        _file(path)


def test_worker_pack_rejects_case_colliding_payload_paths() -> None:
    with pytest.raises(ValidationError, match="case-insensitive"):
        _manifest(files=[_file(), _file("BIN/worker.exe")])


def test_worker_pack_requires_entrypoint_and_license_files_in_payload() -> None:
    with pytest.raises(ValidationError, match="entrypoint executable"):
        _manifest(files=[_file("bin/another.exe")])

    with pytest.raises(ValidationError, match="license file"):
        _manifest(licenses=[WorkerPackLicense(name="MIT", file="licenses/MIT.txt")])


@pytest.mark.parametrize(
    "environment",
    [
        {"PATH": "${PACK_ROOT}/bin"},
        {"CHATWAIFU_STT_WORKER_TOKEN": "not-allowed"},
        {"CHATWAIFU_STT_WORKER_API_KEY": "not-allowed"},
        {"CHATWAIFU_STT_WORKER_CLIENT_SECRET": "not-allowed"},
        {"CHATWAIFU_STT_WORKER_PASSWORD": "not-allowed"},
        {"CHATWAIFU_STT_WORKER_MODEL_DIR": "${UNKNOWN}/model"},
    ],
)
def test_worker_pack_rejects_uncontrolled_environment(environment: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        _manifest(
            worker=WorkerPackWorker(
                kind="stt",
                backend="faster_whisper",
                provider_id="faster-whisper",
                display_name="faster-whisper",
                model="base",
                entrypoint=WorkerPackEntrypoint(
                    executable="bin/worker.exe",
                    environment=environment,
                ),
            )
        )


def test_worker_pack_allows_non_secret_environment_names_containing_key_letters() -> None:
    manifest = _manifest(
        worker=WorkerPackWorker(
            kind="stt",
            backend="faster_whisper",
            provider_id="faster-whisper",
            display_name="faster-whisper",
            model="base",
            entrypoint=WorkerPackEntrypoint(
                executable="bin/worker.exe",
                environment={"CHATWAIFU_STT_WORKER_MONKEY": "enabled"},
            ),
        )
    )

    assert manifest.worker.entrypoint.environment["CHATWAIFU_STT_WORKER_MONKEY"] == "enabled"


def test_windows_worker_pack_requires_exe_entrypoint() -> None:
    worker = WorkerPackWorker(
        kind="stt",
        backend="faster_whisper",
        provider_id="faster-whisper",
        display_name="faster-whisper",
        model="base",
        entrypoint=WorkerPackEntrypoint(executable="bin/worker"),
    )

    with pytest.raises(ValidationError, match=r"must be an \.exe"):
        _manifest(worker=worker, files=[_file("bin/worker")])


def test_worker_pack_rejects_oversized_total_expanded_payload() -> None:
    files = [
        WorkerPackFile(
            path="bin/worker.exe" if index == 0 else f"models/model-{index}.bin",
            size=WORKER_PACK_MAX_FILE_BYTES,
            sha256="0" * 64,
            role="runtime" if index == 0 else "model",
        )
        for index in range(5)
    ]

    with pytest.raises(ValidationError, match="expanded payload exceeds"):
        _manifest(files=files)


def test_worker_pack_rejects_unknown_fields_and_invalid_accelerator_combinations() -> None:
    payload = _manifest().model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        WorkerPackManifest.model_validate(payload)

    with pytest.raises(ValidationError, match="metal"):
        WorkerPackPlatform(os="windows", architecture="x86_64", accelerator="metal")


def test_worker_pack_activation_config_preserves_typed_worker_kinds() -> None:
    config = WorkerPackActivationConfig(
        active=WorkerPackActiveSelection(
            stt=WorkerPackSelection(pack_id="faster-whisper-cpu", version="1.2.3")
        )
    )

    assert config.schema_version == "1.0"
    assert config.active.stt is not None
    assert config.active.stt.pack_id == "faster-whisper-cpu"
    assert config.active.tts is None
