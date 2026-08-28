"""Prepare pinned NLTK resources required by Pipecat without runtime auto-downloads."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from collections.abc import MutableMapping
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NLTK_DATA_ROOT = ROOT / ".local" / "nltk_data"

# Pin the NLTK data repository revision as well as the archive digest. The standard
# NLTK downloader rejects proxy Fake-IP addresses in 198.18.0.0/15 before opening the
# trusted upstream URL, so ChatWaifu performs this narrowly scoped verified fetch at
# setup time and leaves NLTK's general SSRF protection enabled.
PUNKT_TAB_ARCHIVE_URL = (
    "https://raw.githubusercontent.com/nltk/nltk_data/"
    "550b6625bcef1f2abff2ff770a5a0d272c9c6b2a/"
    "packages/tokenizers/punkt_tab.zip"
)
PUNKT_TAB_ARCHIVE_SHA256 = "e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106"
PUNKT_TAB_MARKER = ".chatwaifu-source.sha256"
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_EXTRACTED_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 256
_REQUIRED_ENGLISH_FILES = (
    "abbrev_types.txt",
    "collocations.tab",
    "ortho_context.tab",
    "sent_starters.txt",
)


class NltkResourceError(RuntimeError):
    """Raised when a pinned NLTK resource cannot be verified or installed."""


def configure_nltk_data_environment(
    data_root: Path = DEFAULT_NLTK_DATA_ROOT,
    environment: MutableMapping[str, str] | None = None,
) -> Path:
    """Prepend ChatWaifu's local data root before NLTK is imported."""

    target = os.environ if environment is None else environment
    resolved_root = data_root.resolve()
    existing = [item for item in target.get("NLTK_DATA", "").split(os.pathsep) if item]
    ordered = [str(resolved_root), *existing]
    target["NLTK_DATA"] = os.pathsep.join(dict.fromkeys(ordered))
    return resolved_root


def ensure_punkt_tab(data_root: Path = DEFAULT_NLTK_DATA_ROOT) -> Path:
    """Install the pinned Punkt sentence tables when the local copy is absent."""

    resolved_root = data_root.resolve()
    target = resolved_root / "tokenizers" / "punkt_tab"
    if _punkt_tab_is_ready(target):
        return target

    tokenizers_root = target.parent
    tokenizers_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".punkt-tab-", dir=tokenizers_root) as temporary:
        temporary_root = Path(temporary)
        archive_path = temporary_root / "punkt_tab.zip"
        extracted_root = temporary_root / "extracted"
        _download_verified_archive(archive_path)
        _extract_verified_archive(archive_path, extracted_root)
        staged = extracted_root / "punkt_tab"
        (staged / PUNKT_TAB_MARKER).write_text(f"{PUNKT_TAB_ARCHIVE_SHA256}\n", encoding="utf-8")
        _validate_installed_resource(staged)
        _replace_resource_atomically(staged, target, temporary_root)

    return target


def _punkt_tab_is_ready(target: Path) -> bool:
    try:
        marker = (target / PUNKT_TAB_MARKER).read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError, UnicodeError):
        return False
    if marker != PUNKT_TAB_ARCHIVE_SHA256:
        return False
    english_root = target / "english"
    return all((english_root / name).is_file() for name in _REQUIRED_ENGLISH_FILES)


def _download_verified_archive(destination: Path) -> None:
    digest = hashlib.sha256()
    total = 0
    try:
        with (
            urllib.request.urlopen(PUNKT_TAB_ARCHIVE_URL, timeout=60) as response,
            destination.open("wb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise NltkResourceError("NLTK punkt_tab archive exceeded the size limit")
                digest.update(chunk)
                output.write(chunk)
    except NltkResourceError:
        raise
    except (OSError, TimeoutError) as error:
        raise NltkResourceError(
            "Could not download the pinned NLTK punkt_tab resource. "
            "Check the network or proxy and run `make setup-nltk-data` again."
        ) from error

    actual_digest = digest.hexdigest()
    if actual_digest != PUNKT_TAB_ARCHIVE_SHA256:
        raise NltkResourceError(
            "NLTK punkt_tab checksum mismatch: "
            f"expected {PUNKT_TAB_ARCHIVE_SHA256}, received {actual_digest}"
        )


def _extract_verified_archive(archive_path: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise NltkResourceError("NLTK punkt_tab archive contains too many entries")
            extracted_bytes = sum(member.file_size for member in members)
            if extracted_bytes > MAX_EXTRACTED_BYTES:
                raise NltkResourceError("NLTK punkt_tab archive expands beyond the size limit")
            for member in members:
                relative = PurePosixPath(member.filename)
                if (
                    relative.is_absolute()
                    or not relative.parts
                    or relative.parts[0] != "punkt_tab"
                    or ".." in relative.parts
                    or stat.S_ISLNK(member.external_attr >> 16)
                ):
                    raise NltkResourceError(
                        f"Unsafe path in NLTK punkt_tab archive: {member.filename!r}"
                    )
            archive.extractall(destination)
    except NltkResourceError:
        raise
    except (OSError, zipfile.BadZipFile) as error:
        raise NltkResourceError("NLTK punkt_tab archive is invalid") from error


def _validate_installed_resource(staged: Path) -> None:
    if not _punkt_tab_is_ready(staged):
        raise NltkResourceError("NLTK punkt_tab archive is missing required English tables")


def _replace_resource_atomically(staged: Path, target: Path, temporary_root: Path) -> None:
    if target.is_symlink():
        raise NltkResourceError(f"Refusing to replace symlinked NLTK resource: {target}")

    backup = temporary_root / "previous-punkt_tab"
    had_previous = target.exists()
    if had_previous:
        target.replace(backup)
    try:
        staged.replace(target)
    except OSError:
        if not had_previous and _punkt_tab_is_ready(target):
            return
        if had_previous and backup.exists() and not target.exists():
            backup.replace(target)
        raise
    except BaseException:
        if had_previous and backup.exists() and not target.exists():
            backup.replace(target)
        raise
    if backup.exists():
        shutil.rmtree(backup)
