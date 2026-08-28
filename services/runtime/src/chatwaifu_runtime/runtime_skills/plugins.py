"""Local plugin installation, lifecycle, and recoverable removal."""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path

from chatwaifu_protocol.skills import PluginManifest, PluginSnapshot

from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.registry import load_plugin_manifest

MAX_PLUGIN_FILES = 256
MAX_PLUGIN_BYTES = 16 * 1024 * 1024


class PluginManager:
    def __init__(self, database: Database, install_root: Path, trash_root: Path) -> None:
        self._database = database
        self._install_root = install_root
        self._trash_root = trash_root

    async def start(self) -> None:
        await asyncio.to_thread(self._install_root.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self._trash_root.mkdir, parents=True, exist_ok=True)
        rows = await self._database.fetchall("SELECT plugin_id, install_path FROM skill_plugins")
        install_paths = [(str(row["plugin_id"]), Path(str(row["install_path"]))) for row in rows]
        missing = await asyncio.to_thread(_missing_install_paths, install_paths)
        if missing:
            raise SkillExecutionError(
                "plugin_install_missing",
                f"Installed plugin directory is missing: {', '.join(missing)}",
            )

    async def list(self) -> list[PluginSnapshot]:
        rows = await self._database.fetchall(
            "SELECT * FROM skill_plugins ORDER BY name COLLATE NOCASE, plugin_id"
        )
        return [
            PluginSnapshot.model_validate(
                {
                    "plugin_id": row["plugin_id"],
                    "version": row["version"],
                    "name": row["name"],
                    "description": row["description"],
                    "enabled": bool(row["enabled"]),
                    "trust_level": row["trust_level"],
                    "sandbox_mode": row["sandbox_mode"],
                    "network_policy": row["network_policy"],
                    "sandbox_backend": row["sandbox_backend"],
                    "install_path": row["install_path"],
                    "installed_at": row["installed_at"],
                    "updated_at": row["updated_at"],
                }
            )
            for row in rows
        ]

    async def registry_sources(self) -> list[tuple[PluginManifest, Path, bool]]:
        rows = await self._database.fetchall(
            "SELECT manifest_json, install_path, enabled FROM skill_plugins ORDER BY plugin_id"
        )
        return [
            (
                PluginManifest.model_validate_json(str(row["manifest_json"])),
                Path(str(row["install_path"])),
                bool(row["enabled"]),
            )
            for row in rows
        ]

    async def install(self, source: Path) -> PluginSnapshot:
        source = await asyncio.to_thread(_resolved_plugin_source, source)
        manifest = load_plugin_manifest(source)
        existing = await self._database.fetchone(
            "SELECT 1 FROM skill_plugins WHERE plugin_id = ?", (manifest.plugin_id,)
        )
        if existing is not None:
            raise ValueError(f"plugin is already installed: {manifest.plugin_id}")
        destination = self._install_root / manifest.plugin_id
        staging = self._install_root / f".{manifest.plugin_id}.installing"
        destination_exists, staging_exists = await asyncio.gather(
            asyncio.to_thread(destination.exists), asyncio.to_thread(staging.exists)
        )
        if destination_exists or staging_exists:
            raise ValueError("plugin destination already exists")
        await asyncio.to_thread(shutil.copytree, source, staging, symlinks=False)
        try:
            copied_manifest = load_plugin_manifest(staging)
            if copied_manifest != manifest:
                raise SkillExecutionError("plugin_copy_mismatch", "Copied plugin manifest changed")
            await asyncio.to_thread(staging.rename, destination)
            now = datetime.now(UTC).isoformat()
            async with self._database.transaction() as connection:
                await connection.execute(
                    """
                    INSERT INTO skill_plugins(
                        plugin_id, version, name, description, install_path,
                        manifest_json, enabled, trust_level, sandbox_mode,
                        network_policy, installed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        manifest.plugin_id,
                        manifest.version,
                        manifest.name,
                        manifest.description,
                        str(destination),
                        manifest.model_dump_json(),
                        manifest.transport.trust_level,
                        manifest.transport.sandbox_mode,
                        manifest.transport.network_policy,
                        now,
                        now,
                    ),
                )
        except BaseException:
            if await asyncio.to_thread(staging.exists):
                await asyncio.to_thread(shutil.rmtree, staging)
            if await asyncio.to_thread(destination.exists):
                await asyncio.to_thread(shutil.rmtree, destination)
            raise
        return await self.get(manifest.plugin_id)

    async def set_enabled(self, plugin_id: str, enabled: bool) -> PluginSnapshot:
        now = datetime.now(UTC).isoformat()
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "UPDATE skill_plugins SET enabled = ?, updated_at = ? WHERE plugin_id = ?",
                (int(enabled), now, plugin_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(plugin_id)
        return await self.get(plugin_id)

    async def uninstall(self, plugin_id: str) -> Path:
        row = await self._database.fetchone(
            "SELECT install_path FROM skill_plugins WHERE plugin_id = ?", (plugin_id,)
        )
        if row is None:
            raise KeyError(plugin_id)
        source, install_root = await asyncio.to_thread(
            _resolved_install_paths, Path(str(row["install_path"])), self._install_root
        )
        if not source.is_relative_to(install_root):
            raise SkillExecutionError("unsafe_plugin_path", "Installed plugin path is outside root")
        target = self._trash_root / f"{plugin_id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
        await asyncio.to_thread(source.rename, target)
        try:
            async with self._database.transaction() as connection:
                await connection.execute(
                    "DELETE FROM skill_plugins WHERE plugin_id = ?", (plugin_id,)
                )
        except BaseException:
            await asyncio.to_thread(target.rename, source)
            raise
        return target

    async def get(self, plugin_id: str) -> PluginSnapshot:
        row = await self._database.fetchone(
            "SELECT * FROM skill_plugins WHERE plugin_id = ?", (plugin_id,)
        )
        if row is None:
            raise KeyError(plugin_id)
        return PluginSnapshot.model_validate(
            {
                "plugin_id": row["plugin_id"],
                "version": row["version"],
                "name": row["name"],
                "description": row["description"],
                "enabled": bool(row["enabled"]),
                "trust_level": row["trust_level"],
                "sandbox_mode": row["sandbox_mode"],
                "network_policy": row["network_policy"],
                "sandbox_backend": row["sandbox_backend"],
                "install_path": row["install_path"],
                "installed_at": row["installed_at"],
                "updated_at": row["updated_at"],
            }
        )


def _validate_tree(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        raise SkillExecutionError("invalid_plugin_source", "Plugin source must be a directory")
    file_count = 0
    total_bytes = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise SkillExecutionError(
                "plugin_symlink_forbidden", f"Plugin contains symlink: {path}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise SkillExecutionError("plugin_special_file_forbidden", f"Unsupported file: {path}")
        file_count += 1
        total_bytes += path.stat().st_size
        if file_count > MAX_PLUGIN_FILES or total_bytes > MAX_PLUGIN_BYTES:
            raise SkillExecutionError(
                "plugin_too_large", "Plugin exceeds local installation limits"
            )
    if file_count == 0:
        raise SkillExecutionError("empty_plugin", "Plugin source is empty")


def _resolved_plugin_source(source: Path) -> Path:
    resolved = source.expanduser().resolve()
    _validate_tree(resolved)
    return resolved


def _resolved_install_paths(source: Path, install_root: Path) -> tuple[Path, Path]:
    return source.resolve(), install_root.resolve()


def _missing_install_paths(items: list[tuple[str, Path]]) -> list[str]:
    return [plugin_id for plugin_id, path in items if not path.is_dir()]
