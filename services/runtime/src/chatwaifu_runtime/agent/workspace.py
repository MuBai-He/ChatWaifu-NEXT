"""Owner workspace and independent declarative document generation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from uuid import UUID
from xml.etree.ElementTree import Element, SubElement, tostring

from chatwaifu_protocol.base import JsonObject
from pypdf import PdfReader

from chatwaifu_runtime.agent.artifacts import ArtifactService
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError

_TEXT_LIMIT = 256_000


class WorkspaceSkills:
    def __init__(self, root: Path, artifacts: ArtifactService, skills_root: Path) -> None:
        self.root = root
        self.artifacts = artifacts
        self.skills_root = skills_root
        self._lock = asyncio.Lock()

    async def _path(self, session_id: str, value: str) -> Path:
        scope = await self.artifacts.scope(UUID(session_id))
        if scope != "local" and not scope.startswith("scene:"):
            raise PermissionError("workspace requires an owner or authorized scene")
        root = (
            self.root
            if scope == "local"
            else (self.root / "scenes" / hashlib.sha256(scope.encode()).hexdigest())
        )
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in value
            or (not path.parts and value != ".")
        ):
            raise ValueError("workspace requires a relative path")
        if len(value) > 240 or any(ord(c) < 32 for c in value):
            raise ValueError("invalid workspace path")
        root.mkdir(parents=True, exist_ok=True)
        result = root / value
        current = result
        while current != root:
            if current.is_symlink():
                raise ValueError("workspace symlinks are not allowed")
            current = current.parent
        if not result.resolve().is_relative_to(root.resolve()):
            raise ValueError("workspace path escaped root")
        return result

    async def list(self, session_id: str, arguments: JsonObject) -> JsonObject:
        path = await self._path(session_id, str(arguments.get("path", ".")))
        if not path.is_dir():
            raise ValueError("workspace directory unavailable")
        items = sorted(path.iterdir(), key=lambda p: p.name)
        return {
            "items": [
                {"name": p.name, "directory": p.is_dir()} for p in items[:100] if not p.is_symlink()
            ],
            "truncated": len(items) > 100,
        }

    async def read(self, session_id: str, arguments: JsonObject) -> JsonObject:
        path = await self._path(session_id, str(arguments["path"]))
        if path.stat().st_size > _TEXT_LIMIT:
            raise ValueError("workspace file exceeds text limit")
        content = await asyncio.to_thread(path.read_bytes)
        return {
            "path": str(arguments["path"]),
            "text": content.decode("utf-8"),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    async def write(self, session_id: str, arguments: JsonObject) -> JsonObject:
        async with self._lock:
            try:
                path = await self._path(session_id, str(arguments["path"]))
                content = str(arguments["text"]).encode()
                if len(content) > _TEXT_LIMIT:
                    raise ValueError("workspace text exceeds limit")
                expected = arguments.get("expected_sha256")
                if path.exists():
                    actual = hashlib.sha256(path.read_bytes()).hexdigest()
                    if expected != actual:
                        raise ValueError("existing file requires its current checksum")
                elif expected is not None:
                    raise ValueError("file version no longer exists")
            except ValueError as error:
                # Validation happens before atomic replacement or artifact publication.
                raise SkillExecutionError("invalid_arguments", str(error)) from error
            path.parent.mkdir(parents=True, exist_ok=True)
            writer = asyncio.create_task(asyncio.to_thread(_atomic_write, path, content))
            try:
                await asyncio.shield(writer)
            except asyncio.CancelledError:
                # Atomic replacement may already have happened. Join the writer
                # before acknowledging cancellation; its journal remains uncertain.
                await writer
                raise
            artifact = await self.artifacts.save(
                UUID(session_id),
                path.name,
                "text/plain; charset=utf-8",
                content,
            )
            return {
                "path": str(arguments["path"]),
                "sha256": artifact.sha256,
                "artifact": artifact.model_dump(mode="json"),
                "verified": True,
            }

    async def inspect(self, session_id: str, arguments: JsonObject) -> JsonObject:
        artifact, path = await self.artifacts.resolve(
            UUID(session_id), UUID(str(arguments["artifact_id"]))
        )
        info: JsonObject = {
            "artifact": artifact.model_dump(mode="json"),
            "verified": artifact.validation_status == "rendered"
            if artifact.name.endswith((".docx", ".pptx"))
            else True,
        }
        if path.is_file() and artifact.name.endswith((".docx", ".pptx")):
            with zipfile.ZipFile(path) as archive:
                info["parts"] = len(archive.namelist())
        return info

    async def word(self, session_id: str, arguments: JsonObject) -> JsonObject:
        try:
            return await self._document(session_id, arguments, "docx")
        except ValueError as error:
            raise SkillExecutionError("invalid_document", str(error)) from error

    async def powerpoint(self, session_id: str, arguments: JsonObject) -> JsonObject:
        try:
            return await self._document(session_id, arguments, "pptx")
        except ValueError as error:
            raise SkillExecutionError("invalid_document", str(error)) from error

    async def _document(self, session_id: str, arguments: JsonObject, kind: str) -> JsonObject:
        await self._path(session_id, ".")
        title = str(arguments["title"])[:100]
        if not title or any(c in title for c in "/\\\x00\r\n"):
            raise ValueError("document title must be a valid filename")
        if shutil.which("soffice") is None:
            raise SkillExecutionError(
                "dependency_missing", "Document verification requires LibreOffice"
            )
        font = {"Darwin": "Arial Unicode MS", "Windows": "Microsoft YaHei"}.get(
            platform.system(), "Noto Sans CJK SC"
        )
        content = json.dumps({**arguments, "_font_face": font}, ensure_ascii=False).encode()
        if len(content) > 131072:
            raise ValueError("document input exceeds 128 KiB")
        if getattr(sys, "frozen", False):
            raise SkillExecutionError(
                "dependency_missing", "Document worker requires the Python runtime installation"
            )
        with tempfile.TemporaryDirectory(prefix="cw-document-") as temporary:
            directory = Path(temporary)
            destination = directory / f"document.{kind}"
            if kind == "docx":
                command = (
                    sys.executable,
                    "-m",
                    "chatwaifu_runtime.agent.document_worker",
                    str(destination),
                )
            else:
                node = shutil.which("node")
                if node is None:
                    raise SkillExecutionError("dependency_missing", "PPT worker requires Node.js")
                command = (
                    node,
                    str(self.skills_root / "builtin" / "documents" / "generate-ppt.mjs"),
                    str(destination),
                )
            await _process(command, content, 60)
            if not destination.is_file() or destination.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("document worker produced no bounded file")
            with zipfile.ZipFile(destination) as archive:
                if archive.testzip() is not None:
                    raise ValueError("document archive is corrupt")
                required = "word/document.xml" if kind == "docx" else "ppt/presentation.xml"
                if required not in archive.namelist():
                    raise ValueError("document structural validation failed")
            renderer = shutil.which("soffice")
            preview = None
            if renderer is not None:
                fonts = _font_config(directory)
                await _process(
                    (
                        renderer,
                        "-env:UserInstallation=" + (directory / "profile").as_uri(),
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        str(directory),
                        str(destination),
                    ),
                    b"",
                    60,
                    environment={"FONTCONFIG_FILE": str(fonts)},
                )
                pdf = directory / "document.pdf"
                if not pdf.is_file() or pdf.stat().st_size == 0:
                    raise ValueError("document renderer did not produce a preview")
                rendered_text = await asyncio.to_thread(_rendered_text, pdf)
                expected = [str(arguments["title"])]
                sections = arguments.get("sections") if kind == "docx" else arguments.get("slides")
                assert isinstance(sections, list)
                for section in sections:
                    assert isinstance(section, dict)
                    expected.append(str(section.get("heading" if kind == "docx" else "title", "")))
                    values = section.get("paragraphs" if kind == "docx" else "bullets")
                    assert isinstance(values, list)
                    for value in values:
                        text = str(value)
                        # PPT pagination inserts continuation headings between 320-character
                        # segments. Verify every segment in order, including duplicates.
                        expected.extend(
                            [text]
                            if kind == "docx"
                            else [
                                text[offset : offset + 320] for offset in range(0, len(text), 320)
                            ]
                        )
                normalized = "".join(rendered_text.split())
                cursor = 0
                for value in expected:
                    fragment = "".join(value.split())
                    if not fragment:
                        continue
                    position = normalized.find(fragment, cursor)
                    if position < 0:
                        raise ValueError("rendered document lost requested content")
                    cursor = position + len(fragment)
                preview = await self.artifacts.save(
                    UUID(session_id),
                    str(arguments["title"])[:100] + ".pdf",
                    "application/pdf",
                    pdf.read_bytes(),
                    validation_status="rendered",
                )
            media_type = "application/vnd.openxmlformats-officedocument." + (
                "wordprocessingml.document" if kind == "docx" else "presentationml.presentation"
            )
            artifact = await self.artifacts.save(
                UUID(session_id),
                str(arguments["title"])[:100] + "." + kind,
                media_type,
                destination.read_bytes(),
                validation_status="rendered" if preview is not None else "structural",
            )
            return {
                "artifact": artifact.model_dump(mode="json"),
                "preview": preview.model_dump(mode="json") if preview is not None else None,
                "verified": preview is not None,
                "rendered": preview is not None,
            }


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".cw-write-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _rendered_text(path: Path) -> str:
    reader = PdfReader(path)
    if not 0 < len(reader.pages) <= 200:
        raise ValueError("rendered document page count exceeds bound")
    return "\n".join(page.extract_text(extraction_mode="layout") for page in reader.pages)


def _font_config(directory: Path) -> Path:
    config = Element("fontconfig")
    # Headless renderers may ship a private fontconfig which omits OS fonts.
    # Register trusted host locations for this isolated render, without installing fonts.
    for path in (
        Path("/System/Library/Fonts"),
        Path("/System/Library/Fonts/Supplemental"),
        Path("/Library/Fonts"),
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "Fonts",
        Path.home() / "Library/Fonts",
        Path.home() / ".local/share/fonts",
    ):
        if path.is_dir():
            SubElement(config, "dir").text = str(path)
    SubElement(config, "cachedir").text = str(directory / "font-cache")
    target = directory / "fonts.conf"
    target.write_bytes(tostring(config, encoding="utf-8", xml_declaration=True))
    return target


async def _process(
    command: tuple[str, ...],
    content: bytes,
    deadline_seconds: float,
    *,
    environment: dict[str, str] | None = None,
) -> bytes:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            **{
                key: value
                for key, value in os.environ.items()
                if key
                in {
                    "PATH",
                    "HOME",
                    "TMPDIR",
                    "LANG",
                    "LC_ALL",
                    "SYSTEMROOT",
                    "PYTHONPATH",
                    "VIRTUAL_ENV",
                    "LD_LIBRARY_PATH",
                }
            },
            **(environment or {}),
        },
    )
    try:
        async with asyncio.timeout(deadline_seconds):
            stdout, stderr = await process.communicate(content)
        if process.returncode != 0 or len(stdout) + len(stderr) > 131072:
            raise SkillExecutionError("worker_failed", "Document worker failed")
        return stdout
    except BaseException:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                process.kill()
                await process.wait()
        raise
