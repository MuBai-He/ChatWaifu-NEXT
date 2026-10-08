"""Bounded independent material extraction; attachments remain untrusted evidence."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path


async def extract_material(name: str, content: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in {".txt", ".md", ".csv", ".json", ".pdf", ".docx", ".pptx"}:
        raise ValueError("material type requires an additional extraction adapter")
    with tempfile.TemporaryDirectory(prefix="cw-material-") as temporary:
        path = Path(temporary) / ("input" + suffix)
        path.write_bytes(content)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "chatwaifu_runtime.agent.materials",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(25):
                output, _ = await process.communicate()
            if process.returncode or len(output) > 400_000:
                raise ValueError("material extraction failed or exceeded output limit")
            value = json.loads(output)
            if not isinstance(value, str):
                raise ValueError("invalid material extraction result")
            return value
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise


def extract(path: Path) -> str:
    import zipfile
    from xml.etree.ElementTree import fromstring

    if path.suffix in {".docx", ".pptx"}:
        with zipfile.ZipFile(path) as archive:
            if (
                sum(i.file_size for i in archive.infolist()) > 8_000_000
                or len(archive.infolist()) > 2000
            ):
                raise ValueError("archive expansion exceeded bound")
            names = (
                ["word/document.xml"]
                if path.suffix == ".docx"
                else sorted(
                    n
                    for n in archive.namelist()
                    if n.startswith("ppt/slides/slide") and n.endswith(".xml")
                )
            )
            text = "\n".join(
                " ".join(
                    e.text or "" for e in fromstring(archive.read(n)).iter() if e.tag.endswith("}t")
                )
                for n in names
            )
    elif path.suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        if len(reader.pages) > 200:
            raise ValueError("PDF exceeded page bound")
        text = "\n".join(p.extract_text() for p in reader.pages)
    else:
        text = path.read_text(encoding="utf-8")
    return text[:96000]


if __name__ == "__main__":
    sys.stdout.buffer.write(
        (json.dumps(extract(Path(sys.argv[1])), ensure_ascii=False) + "\n").encode("utf-8")
    )
