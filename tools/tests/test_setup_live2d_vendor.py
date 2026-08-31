# Dedicated regression coverage intentionally exercises the setup helper directly.
# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path

import pytest

from tools import setup_live2d_vendor


def test_sample_source_rewrite_does_not_depend_on_a_utf8_system_locale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_directory = tmp_path / "CubismSdkForWeb-5-r.5"
    source_root = sdk_directory / "Samples" / "TypeScript" / "Demo" / "src"
    source_root.mkdir(parents=True)
    define_path = source_root / "lappdefine.ts"
    original_shader_path = "export const ShaderPath = '../../Framework/Shaders/WebGL/';"
    define_path.write_bytes(f"// café\n{original_shader_path}\n".encode())

    destination = tmp_path / "vendor" / "CubismWebSamples" / "src"
    monkeypatch.setattr(setup_live2d_vendor, "SAMPLE_SOURCE_TARGET", destination)

    original_read_text = Path.read_text
    original_write_text = Path.write_text
    read_encodings: list[str | None] = []
    write_encodings: list[str | None] = []

    def locale_sensitive_read_text(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        read_encodings.append(encoding)
        return original_read_text(path, encoding=encoding or "ascii", errors=errors)

    def locale_sensitive_write_text(
        path: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        write_encodings.append(encoding)
        return original_write_text(
            path,
            data,
            encoding=encoding or "ascii",
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "read_text", locale_sensitive_read_text)
    monkeypatch.setattr(Path, "write_text", locale_sensitive_write_text)

    setup_live2d_vendor._copy_sample_sources(sdk_directory)

    rewritten = original_read_text(destination / "lappdefine.ts", encoding="utf-8")
    assert "// café" in rewritten
    assert "export const ShaderPath = '/vendor/live2d/framework/Shaders/WebGL/';" in rewritten
    assert read_encodings == ["utf-8"]
    assert write_encodings == ["utf-8"]
