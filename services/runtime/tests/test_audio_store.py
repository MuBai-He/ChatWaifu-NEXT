"""Failure handling for reset-time audio quarantine cleanup."""

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_runtime.audio.store import AudioAssetStore, AudioRemovalCommit
from fastapi.testclient import TestClient


def test_commit_counts_only_purged_assets_and_startup_retries_unlink_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = AudioAssetStore(tmp_path)
    store.start()
    deleted_asset_id = uuid4()
    pending_asset_id = uuid4()
    (tmp_path / f"{deleted_asset_id}.wav").write_bytes(b"deleted")
    (tmp_path / f"{pending_asset_id}.wav").write_bytes(b"pending")
    staged = store.stage_remove((deleted_asset_id, pending_asset_id))
    assert staged.directory is not None

    original_unlink = Path.unlink

    def fail_one_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.parent == staged.directory and path.stem == str(pending_asset_id):
            raise PermissionError("injected unlink failure")
        original_unlink(path, missing_ok=missing_ok)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_one_unlink)
        with caplog.at_level(logging.WARNING, logger="chatwaifu_runtime.audio.store"):
            result = staged.commit()

    assert result.deleted_count == 1
    assert result.pending_count == 1
    assert result.cleanup_complete is False
    assert staged.finished is False
    assert not (tmp_path / f"{deleted_asset_id}.wav").exists()
    assert not (tmp_path / f"{pending_asset_id}.wav").exists()
    assert not (staged.directory / f"{deleted_asset_id}.wav").exists()
    assert (staged.directory / f"{pending_asset_id}.wav").is_file()
    assert any(
        record.message == "audio asset purge failed; retaining quarantine entry for retry"
        and getattr(record, "audio_asset_id", None) == str(pending_asset_id)
        for record in caplog.records
    )

    restarted = AudioAssetStore(tmp_path)
    restarted.start()
    restarted.recover_staged_removals(())
    assert not (tmp_path / "reset-quarantine").exists()


def test_commit_reports_and_leaves_batch_when_directory_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = AudioAssetStore(tmp_path)
    store.start()
    asset_id = uuid4()
    (tmp_path / f"{asset_id}.wav").write_bytes(b"audio")
    staged = store.stage_remove((asset_id,))
    assert staged.directory is not None

    original_rmdir = Path.rmdir

    def fail_batch_rmdir(path: Path) -> None:
        if path == staged.directory:
            raise PermissionError("injected rmdir failure")
        original_rmdir(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rmdir", fail_batch_rmdir)
        with caplog.at_level(logging.WARNING, logger="chatwaifu_runtime.audio.store"):
            result = staged.commit()

    assert result.deleted_count == 1
    assert result.pending_count == 0
    assert result.cleanup_complete is False
    assert staged.finished is False
    assert staged.directory.is_dir()
    assert list(staged.directory.iterdir()) == []
    assert any(
        record.message == "audio quarantine batch cleanup failed; retaining it for retry"
        and getattr(record, "quarantine_batch", None) == staged.directory.name
        for record in caplog.records
    )

    restarted = AudioAssetStore(tmp_path)
    restarted.start()
    restarted.recover_staged_removals(())
    assert not (tmp_path / "reset-quarantine").exists()


def test_reset_reports_deferred_audio_cleanup_instead_of_claiming_success(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    container = cast(Any, client.app).state.container
    session = client.post("/v1/sessions", json={}).json()

    class DeferredRemoval:
        def commit(self) -> AudioRemovalCommit:
            return AudioRemovalCommit(
                deleted_count=0,
                pending_count=1,
                cleanup_complete=False,
            )

        def rollback(self) -> None:
            raise AssertionError("a committed reset must not roll back deferred cleanup")

    def defer_cleanup(_asset_ids: Iterable[UUID]) -> DeferredRemoval:
        return DeferredRemoval()

    monkeypatch.setattr(container.audio_assets, "stage_remove", defer_cleanup)
    with caplog.at_level(logging.WARNING, logger="chatwaifu_runtime.conversation.service"):
        response = client.post(
            f"/v1/sessions/{session['session_id']}/reset",
            json={"confirm": True},
        )

    assert response.status_code == 200
    assert response.json()["audio_assets_deleted"] == 0
    assert response.json()["audio_assets_pending_cleanup"] == 1
    assert response.json()["audio_cleanup_complete"] is False
    assert any(
        record.message == "experience reset committed with deferred audio quarantine cleanup"
        and getattr(record, "session_id", None) == session["session_id"]
        and getattr(record, "audio_assets_deleted", None) == 0
        and getattr(record, "audio_assets_pending", None) == 1
        for record in caplog.records
    )
