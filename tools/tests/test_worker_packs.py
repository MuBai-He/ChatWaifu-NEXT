from __future__ import annotations

import hashlib
import json
import stat
import struct
import zipfile
from pathlib import Path
from typing import Any

import pytest
from chatwaifu_model_worker import WorkerPackActivationConfig, WorkerPackManifest
from chatwaifu_model_worker import pack_installer as worker_packs


def _pe(payload: bytes = b"worker", *, machine: int = 0x8664) -> bytes:
    value = bytearray(128 + len(payload))
    value[:2] = b"MZ"
    struct.pack_into("<I", value, 0x3C, 64)
    value[64:68] = b"PE\0\0"
    struct.pack_into("<H", value, 68, machine)
    value[128:] = payload
    return bytes(value)


def _manifest(
    payloads: dict[str, bytes],
    *,
    pack_id: str = "faster-whisper-cpu",
    version: str = "1.0.0",
    kind: str = "stt",
) -> dict[str, Any]:
    prefix = "CHATWAIFU_STT_WORKER_" if kind == "stt" else "CHATWAIFU_NEURAL_TTS_WORKER_"
    executable = next(iter(payloads))
    return {
        "schema_version": "1.0",
        "pack_id": pack_id,
        "version": version,
        "platform": {
            "os": "windows",
            "architecture": "x86_64",
            "accelerator": "cpu" if kind == "stt" else "cuda",
            "accelerator_version": None if kind == "stt" else "12.6",
            "python_abi": "cp312",
        },
        "worker": {
            "kind": kind,
            "backend": "faster_whisper" if kind == "stt" else "qwen3_tts_torch",
            "provider_id": "faster-whisper" if kind == "stt" else "qwen3_tts_torch",
            "display_name": "Local worker",
            "model": "base",
            "entrypoint": {
                "executable": executable,
                "arguments": [],
                "working_directory": ".",
                "environment": {f"{prefix}MODEL_DIR": "${PACK_ROOT}/models/default"},
                "health_path": "/v1/health",
                "capabilities_path": "/v1/capabilities",
                "startup_timeout_seconds": 120,
                "shutdown_timeout_seconds": 10,
            },
        },
        "files": [
            {
                "path": path,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "role": "runtime" if path == executable else "model",
            }
            for path, payload in payloads.items()
        ],
        "licenses": [{"name": "test-only"}],
    }


def _write_archive(
    path: Path,
    payloads: dict[str, bytes],
    *,
    manifest: dict[str, Any] | None = None,
    extras: dict[str, bytes] | None = None,
) -> Path:
    value = manifest or _manifest(payloads)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(value))
        for name, payload in payloads.items():
            archive.writestr(name, payload)
        for name, payload in (extras or {}).items():
            archive.writestr(name, payload)
    return path


def _write_template(
    path: Path,
    payloads: dict[str, bytes],
    *,
    pack_id: str = "faster-whisper-cpu",
    version: str = "1.0.0",
    kind: str = "stt",
) -> Path:
    manifest = _manifest(
        payloads,
        pack_id=pack_id,
        version=version,
        kind=kind,
    )
    manifest.pop("files")
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_build_is_deterministic_and_infers_manifest_files(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    (staging / "bin").mkdir(parents=True)
    (staging / "models" / "default").mkdir(parents=True)
    worker = _pe()
    (staging / "bin" / "worker.exe").write_bytes(worker)
    (staging / "models" / "default" / "model.bin").write_bytes(b"weights")
    template = _write_template(
        tmp_path / "template.json",
        {"bin/worker.exe": worker, "models/default/model.bin": b"weights"},
    )

    first = worker_packs.build_archive(staging, template, tmp_path / "first.cwpack")
    second = worker_packs.build_archive(staging, template, tmp_path / "second.cwpack")

    assert first.archive_sha256 == second.archive_sha256
    assert (tmp_path / "first.cwpack").read_bytes() == (tmp_path / "second.cwpack").read_bytes()
    roles = {file.path: file.role for file in first.manifest.files}
    assert roles == {
        "bin/worker.exe": "runtime",
        "models/default/model.bin": "model",
    }
    with zipfile.ZipFile(first.archive_path) as archive:
        assert archive.namelist() == [
            "manifest.json",
            "bin/worker.exe",
            "models/default/model.bin",
        ]
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())
        assert archive.getinfo("bin/worker.exe").compress_type == zipfile.ZIP_DEFLATED
        assert archive.getinfo("models/default/model.bin").compress_type == zipfile.ZIP_STORED


@pytest.mark.parametrize("forbidden", [".env", ".idea/workspace.xml", "state/runtime.db"])
def test_build_rejects_secrets_databases_and_development_state(
    tmp_path: Path, forbidden: str
) -> None:
    staging = tmp_path / "staging"
    (staging / "bin").mkdir(parents=True)
    worker = _pe()
    (staging / "bin" / "worker.exe").write_bytes(worker)
    forbidden_path = staging.joinpath(*Path(forbidden).parts)
    forbidden_path.parent.mkdir(parents=True, exist_ok=True)
    forbidden_path.write_text("private", encoding="utf-8")
    template = _write_template(tmp_path / "template.json", {"bin/worker.exe": worker})

    with pytest.raises(worker_packs.WorkerPackError, match="forbidden"):
        worker_packs.build_archive(staging, template, tmp_path / "output.cwpack")

    assert not (tmp_path / "output.cwpack").exists()


def test_build_rejects_symlinks_and_prefilled_files(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    (staging / "bin").mkdir(parents=True)
    worker = _pe()
    executable = staging / "bin" / "worker.exe"
    executable.write_bytes(worker)
    (staging / "linked.exe").symlink_to(executable)
    template = _write_template(tmp_path / "template.json", {"bin/worker.exe": worker})

    with pytest.raises(worker_packs.WorkerPackError, match="symlinks"):
        worker_packs.build_archive(staging, template, tmp_path / "output.cwpack")

    (staging / "linked.exe").unlink()
    value = json.loads(template.read_text(encoding="utf-8"))
    value["files"] = []
    template.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(worker_packs.WorkerPackError, match="must not prefill files"):
        worker_packs.build_archive(staging, template, tmp_path / "output.cwpack")


def test_verify_install_list_and_reverify_payload(tmp_path: Path) -> None:
    worker = _pe()
    payloads = {"bin/worker.exe": worker, "models/default/model.bin": b"weights"}
    archive = _write_archive(tmp_path / "worker.zip", payloads)

    verified = worker_packs.verify_archive(archive)
    installed = worker_packs.install_archive(archive, tmp_path / "packs")
    discovered, errors = worker_packs.discover_installed_packs(tmp_path / "packs")
    reverified = worker_packs.load_installed_pack(installed.root, verify_payload=True)

    assert verified.manifest.pack_id == "faster-whisper-cpu"
    assert installed.root == tmp_path / "packs" / "faster-whisper-cpu" / "1.0.0"
    assert installed.receipt.archive_sha256 == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert installed.receipt.manifest_sha256 == verified.manifest_sha256
    assert (installed.root / "bin/worker.exe").read_bytes() == worker
    assert reverified.manifest == installed.manifest
    assert discovered == [reverified]
    assert errors == []
    assert not list((tmp_path / "packs" / "faster-whisper-cpu").glob(".install-*"))


def test_verify_rejects_checksum_mismatch_and_leaves_no_install(tmp_path: Path) -> None:
    payloads = {"bin/worker.exe": _pe(b"tampered")}
    manifest = _manifest({"bin/worker.exe": _pe(b"expected")})
    archive = _write_archive(tmp_path / "tampered.zip", payloads, manifest=manifest)

    with pytest.raises(worker_packs.WorkerPackError, match="checksum mismatch"):
        worker_packs.install_archive(archive, tmp_path / "packs")

    assert not (tmp_path / "packs").exists()


@pytest.mark.parametrize("unsafe_name", ["../escape.exe", "/absolute.exe", "dir\\file.exe"])
def test_verify_rejects_escaping_or_nonportable_zip_names(tmp_path: Path, unsafe_name: str) -> None:
    payloads = {"bin/worker.exe": _pe()}
    archive = _write_archive(
        tmp_path / "unsafe.zip",
        payloads,
        extras={unsafe_name: b"escape"},
    )

    with pytest.raises(worker_packs.WorkerPackError, match="archive path"):
        worker_packs.verify_archive(archive)

    assert not (tmp_path / "escape.exe").exists()


def test_verify_rejects_symlink_and_unexpected_payload(tmp_path: Path) -> None:
    archive_path = tmp_path / "symlink.zip"
    manifest = _manifest({"bin/worker.exe": _pe(b"target")})
    link = zipfile.ZipInfo("bin/worker.exe")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(link, "target")

    with pytest.raises(worker_packs.WorkerPackError, match="regular file"):
        worker_packs.verify_archive(archive_path)

    unexpected = _write_archive(
        tmp_path / "unexpected.zip",
        {"bin/worker.exe": _pe()},
        extras={"extra.txt": b"not declared"},
    )
    with pytest.raises(worker_packs.WorkerPackError, match="does not match manifest"):
        worker_packs.verify_archive(unexpected)


def test_verify_rejects_case_colliding_zip_members(tmp_path: Path) -> None:
    archive_path = tmp_path / "collision.zip"
    payloads = {"bin/worker.exe": _pe()}
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(_manifest(payloads)))
        archive.writestr("bin/worker.exe", _pe())
        archive.writestr("BIN/WORKER.EXE", _pe())

    with pytest.raises(worker_packs.WorkerPackError, match="case-insensitive"):
        worker_packs.verify_archive(archive_path)


@pytest.mark.parametrize(
    ("name", "payload", "message"),
    [
        ("bin/worker.exe", _pe(machine=0xAA64), "PE machine mismatch"),
        ("bin/native.dll", b"not-a-pe", "too small to be PE"),
        ("bin/native.pyd", _pe(machine=0x014C), "PE machine mismatch"),
    ],
)
def test_verify_enforces_windows_x64_machine_for_every_native_file(
    tmp_path: Path,
    name: str,
    payload: bytes,
    message: str,
) -> None:
    payloads = {"bin/worker.exe": _pe(), name: payload}
    if name == "bin/worker.exe":
        payloads = {name: payload}
    archive = _write_archive(tmp_path / f"wrong-{Path(name).suffix}.zip", payloads)

    with pytest.raises(worker_packs.WorkerPackError, match=message):
        worker_packs.verify_archive(archive)


def test_install_refuses_existing_version_without_overwriting(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path / "worker.zip", {"bin/worker.exe": _pe()})
    installed = worker_packs.install_archive(archive, tmp_path / "packs")
    marker = installed.root / "keep.txt"
    marker.write_text("user-owned", encoding="utf-8")

    with pytest.raises(worker_packs.WorkerPackError, match="already installed"):
        worker_packs.install_archive(archive, tmp_path / "packs")

    assert marker.read_text(encoding="utf-8") == "user-owned"


def test_reverify_detects_installed_payload_tampering(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path / "worker.zip", {"bin/worker.exe": _pe()})
    installed = worker_packs.install_archive(archive, tmp_path / "packs")
    (installed.root / "bin/worker.exe").write_bytes(b"changed")

    with pytest.raises(worker_packs.WorkerPackError, match="checksum mismatch"):
        worker_packs.load_installed_pack(installed.root, verify_payload=True)


def test_installed_pack_rejects_symlinked_metadata(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path / "worker.zip", {"bin/worker.exe": _pe()})
    installed = worker_packs.install_archive(archive, tmp_path / "packs")
    manifest = installed.root / worker_packs.MANIFEST_NAME
    outside = tmp_path / "outside-manifest.json"
    outside.write_bytes(manifest.read_bytes())
    manifest.unlink()
    manifest.symlink_to(outside)

    with pytest.raises(worker_packs.WorkerPackError, match="must not be symlinked"):
        worker_packs.load_installed_pack(installed.root)


def test_activate_selects_latest_semver_and_preserves_other_kind(tmp_path: Path) -> None:
    pack_root = tmp_path / "packs"
    config_root = tmp_path / "config"
    for version in ("1.0.0", "1.1.0-rc.1", "1.1.0"):
        payloads = {"bin/worker.exe": _pe(f"stt-{version}".encode())}
        manifest = _manifest(payloads, version=version)
        archive = _write_archive(tmp_path / f"stt-{version}.zip", payloads, manifest=manifest)
        worker_packs.install_archive(archive, pack_root)

    tts_payloads = {"bin/worker.exe": _pe(b"tts")}
    tts_manifest = _manifest(
        tts_payloads,
        pack_id="qwen3-tts-cuda",
        version="2.0.0",
        kind="tts",
    )
    tts_archive = _write_archive(tmp_path / "tts.zip", tts_payloads, manifest=tts_manifest)
    worker_packs.install_archive(tts_archive, pack_root)

    worker_packs.activate_pack("qwen3-tts-cuda", root=pack_root, config_root=config_root)
    selected, config_path = worker_packs.activate_pack(
        "faster-whisper-cpu", root=pack_root, config_root=config_root
    )
    config = WorkerPackActivationConfig.model_validate_json(config_path.read_bytes())

    assert selected.manifest.version == "1.1.0"
    assert config.active.stt is not None and config.active.stt.version == "1.1.0"
    assert config.active.tts is not None and config.active.tts.pack_id == "qwen3-tts-cuda"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_activate_rejects_invalid_existing_config_without_replacing_it(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path / "worker.zip", {"bin/worker.exe": _pe()})
    worker_packs.install_archive(archive, tmp_path / "packs")
    config_root = tmp_path / "config"
    config_root.mkdir()
    config_path = config_root / worker_packs.SELECTION_NAME
    config_path.write_text('{"schema_version":"999"}', encoding="utf-8")

    with pytest.raises(worker_packs.WorkerPackError, match="activation config is invalid"):
        worker_packs.activate_pack(
            "faster-whisper-cpu", root=tmp_path / "packs", config_root=config_root
        )

    assert config_path.read_text(encoding="utf-8") == '{"schema_version":"999"}'


def test_cli_verify_outputs_machine_readable_metadata(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _write_archive(tmp_path / "worker.zip", {"bin/worker.exe": _pe()})

    assert worker_packs.main(["verify", str(archive), "--json"]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["pack_id"] == "faster-whisper-cpu"
    assert output["kind"] == "stt"
    WorkerPackManifest.model_validate(_manifest({"bin/worker.exe": _pe()}))
