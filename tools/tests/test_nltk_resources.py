# pyright: reportPrivateUsage=false

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any

import pytest

from tools import nltk_resources


def test_configure_nltk_data_prepends_local_root_and_preserves_existing(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    local = tmp_path / "local"
    environment = {"NLTK_DATA": str(existing)}

    resolved = nltk_resources.configure_nltk_data_environment(local, environment)

    assert resolved == local.resolve()
    assert environment["NLTK_DATA"].split(nltk_resources.os.pathsep) == [
        str(local.resolve()),
        str(existing),
    ]


def test_ensure_punkt_tab_installs_verified_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _punkt_archive()
    digest = hashlib.sha256(archive).hexdigest()
    monkeypatch.setattr(nltk_resources, "PUNKT_TAB_ARCHIVE_SHA256", digest)

    def open_archive(*_args: object, **_kwargs: object) -> io.BytesIO:
        return io.BytesIO(archive)

    monkeypatch.setattr(
        nltk_resources.urllib.request,
        "urlopen",
        open_archive,
    )

    installed = nltk_resources.ensure_punkt_tab(tmp_path / "nltk_data")

    assert (installed / "english" / "ortho_context.tab").read_text() == "ortho"
    assert (installed / nltk_resources.PUNKT_TAB_MARKER).read_text().strip() == digest


def test_ensure_punkt_tab_reuses_complete_local_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "nltk_data" / "tokenizers" / "punkt_tab"
    _write_ready_resource(target)

    def unexpected_download(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("complete resources must not trigger a download")

    monkeypatch.setattr(nltk_resources.urllib.request, "urlopen", unexpected_download)

    assert nltk_resources.ensure_punkt_tab(tmp_path / "nltk_data") == target


def test_ensure_punkt_tab_rejects_checksum_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def open_invalid_archive(*_args: object, **_kwargs: object) -> io.BytesIO:
        return io.BytesIO(b"not the pinned archive")

    monkeypatch.setattr(
        nltk_resources.urllib.request,
        "urlopen",
        open_invalid_archive,
    )

    with pytest.raises(nltk_resources.NltkResourceError, match="checksum mismatch"):
        nltk_resources.ensure_punkt_tab(tmp_path / "nltk_data")

    assert not (tmp_path / "nltk_data" / "tokenizers" / "punkt_tab").exists()


def test_archive_rejects_parent_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("punkt_tab/../escaped.txt", "unsafe")

    with pytest.raises(nltk_resources.NltkResourceError, match="Unsafe path"):
        nltk_resources._extract_verified_archive(archive_path, tmp_path / "output")


def _punkt_archive() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name in nltk_resources._REQUIRED_ENGLISH_FILES:
            archive.writestr(f"punkt_tab/english/{name}", "ortho")
    return output.getvalue()


def _write_ready_resource(target: Path) -> None:
    english = target / "english"
    english.mkdir(parents=True)
    for name in nltk_resources._REQUIRED_ENGLISH_FILES:
        (english / name).write_text("ready", encoding="utf-8")
    (target / nltk_resources.PUNKT_TAB_MARKER).write_text(
        nltk_resources.PUNKT_TAB_ARCHIVE_SHA256,
        encoding="utf-8",
    )
