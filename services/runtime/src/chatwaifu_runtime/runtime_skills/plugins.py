"""Local plugin installation, lifecycle, and recoverable removal."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from chatwaifu_protocol.skills import PluginManifest, PluginSnapshot

from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.registry import load_plugin_manifest
from chatwaifu_runtime.runtime_skills.repository import PluginRepository

MAX_PLUGIN_FILES = 256
MAX_PLUGIN_BYTES = 16 * 1024 * 1024

type PluginMutationHook = Callable[[], Awaitable[None]]


class PluginManager:
    def __init__(
        self,
        repository: PluginRepository,
        install_root: Path,
        data_root: Path,
        trash_root: Path,
    ) -> None:
        self._repository = repository
        self._install_root = install_root
        self._data_root = data_root
        self._trash_root = trash_root
        self._operation_locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def operation_lease(self, plugin_id: str) -> AsyncGenerator[None]:
        """Keep one plugin package identity stable for a complete operation.

        Package execution and lifecycle mutation share this lease. Metadata-only
        updates, such as recording the sandbox backend used by an invocation, do
        not acquire it so execution can update diagnostics without self-deadlock.
        """

        lock = self._operation_locks.setdefault(plugin_id, asyncio.Lock())
        async with lock:
            yield

    async def start(self) -> None:
        await asyncio.to_thread(self._install_root.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self._data_root.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self._trash_root.mkdir, parents=True, exist_ok=True)
        rows = await self._repository.list_plugin_records()
        install_paths = [(str(row["plugin_id"]), Path(str(row["install_path"]))) for row in rows]
        missing = await asyncio.to_thread(_missing_install_paths, install_paths)
        if missing:
            raise SkillExecutionError(
                "plugin_install_missing",
                f"Installed plugin directory is missing: {', '.join(missing)}",
            )
        for plugin_id, install_path in install_paths:
            await asyncio.to_thread(
                _prepare_installed_package,
                plugin_id,
                install_path,
                self._install_root,
            )
            await asyncio.to_thread(self.data_root(plugin_id).mkdir, parents=True, exist_ok=True)

    async def list(self) -> list[PluginSnapshot]:
        rows = await self._repository.list_plugin_records()
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
                    "sandbox_limits_enforced": json.loads(str(row["sandbox_limits_json"])),
                    "install_path": row["install_path"],
                    "installed_at": row["installed_at"],
                    "updated_at": row["updated_at"],
                }
            )
            for row in rows
        ]

    async def registry_sources(self) -> list[tuple[PluginManifest, Path, bool]]:
        rows = await self._repository.list_plugin_records()
        return [
            (
                PluginManifest.model_validate_json(str(row["manifest_json"])),
                Path(str(row["install_path"])),
                bool(row["enabled"]),
            )
            for row in rows
        ]

    async def install(
        self,
        source: Path,
        *,
        after_change: PluginMutationHook | None = None,
    ) -> PluginSnapshot:
        source = await asyncio.to_thread(_resolved_plugin_source, source)
        manifest = load_plugin_manifest(source)
        async with self.operation_lease(manifest.plugin_id):
            existing = await self._repository.plugin_record(manifest.plugin_id)
            if existing is not None:
                raise ValueError(f"plugin is already installed: {manifest.plugin_id}")
            destination = self._install_root / manifest.plugin_id
            staging = self._install_root / f".{manifest.plugin_id}.installing"
            data_destination = self.data_root(manifest.plugin_id)
            destination_exists, staging_exists, data_exists = await asyncio.gather(
                asyncio.to_thread(destination.exists),
                asyncio.to_thread(staging.exists),
                asyncio.to_thread(data_destination.exists),
            )
            if destination_exists or staging_exists or data_exists:
                raise ValueError("plugin destination already exists")
            await asyncio.to_thread(shutil.copytree, source, staging, symlinks=False)
            try:
                copied_manifest = load_plugin_manifest(staging)
                if copied_manifest != manifest:
                    raise SkillExecutionError(
                        "plugin_copy_mismatch", "Copied plugin manifest changed"
                    )
                await asyncio.to_thread(staging.rename, destination)
                await asyncio.to_thread(_make_package_read_only, destination)
                await asyncio.to_thread(data_destination.mkdir, parents=True, exist_ok=False)
                now = datetime.now(UTC).isoformat()
                await self._repository.insert_plugin(
                    {
                        "plugin_id": manifest.plugin_id,
                        "version": manifest.version,
                        "name": manifest.name,
                        "description": manifest.description,
                        "install_path": str(destination),
                        "manifest_json": manifest.model_dump_json(),
                        "trust_level": manifest.transport.trust_level,
                        "sandbox_mode": manifest.transport.sandbox_mode,
                        "network_policy": manifest.transport.network_policy,
                        "installed_at": now,
                        "updated_at": now,
                    }
                )
            except BaseException:
                if await asyncio.to_thread(staging.exists):
                    await asyncio.to_thread(shutil.rmtree, staging)
                if await asyncio.to_thread(destination.exists):
                    await asyncio.to_thread(_make_package_writable, destination)
                    await asyncio.to_thread(shutil.rmtree, destination)
                if await asyncio.to_thread(data_destination.exists):
                    await asyncio.to_thread(shutil.rmtree, data_destination)
                raise
            if after_change is not None:
                await after_change()
            return await self.get(manifest.plugin_id)

    async def set_enabled(
        self,
        plugin_id: str,
        enabled: bool,
        *,
        after_change: PluginMutationHook | None = None,
    ) -> PluginSnapshot:
        async with self.operation_lease(plugin_id):
            now = datetime.now(UTC).isoformat()
            if not await self._repository.set_plugin_enabled(plugin_id, enabled, now):
                raise KeyError(plugin_id)
            if after_change is not None:
                await after_change()
            return await self.get(plugin_id)

    async def set_sandbox_backend(
        self, plugin_id: str, backend: str | None, limits: tuple[str, ...] = ()
    ) -> PluginSnapshot:
        updated = await self._repository.set_plugin_sandbox_backend(
            plugin_id,
            backend,
            json.dumps(limits),
            datetime.now(UTC).isoformat(),
        )
        if not updated:
            raise KeyError(plugin_id)
        return await self.get(plugin_id)

    async def uninstall(
        self,
        plugin_id: str,
        *,
        before_change: PluginMutationHook | None = None,
        after_change: PluginMutationHook | None = None,
    ) -> Path:
        async with self.operation_lease(plugin_id):
            if before_change is not None:
                await before_change()
            row = await self._repository.plugin_record(plugin_id)
            if row is None:
                raise KeyError(plugin_id)
            source, install_root = await asyncio.to_thread(
                _resolved_install_paths, Path(str(row["install_path"])), self._install_root
            )
            if not source.is_relative_to(install_root):
                raise SkillExecutionError(
                    "unsafe_plugin_path", "Installed plugin path is outside root"
                )
            target = self._trash_root / (
                f"{plugin_id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
            )
            await asyncio.to_thread(_make_package_writable, source)
            await asyncio.to_thread(source.rename, target)
            data_source = self.data_root(plugin_id)
            data_target = target.with_name(f"{target.name}-data")
            data_moved = False
            try:
                if await asyncio.to_thread(data_source.exists):
                    await asyncio.to_thread(data_source.rename, data_target)
                    data_moved = True
                if not await self._repository.delete_plugin(plugin_id):
                    raise KeyError(plugin_id)
            except BaseException:
                await asyncio.to_thread(target.rename, source)
                await asyncio.to_thread(_make_package_read_only, source)
                if data_moved and await asyncio.to_thread(data_target.exists):
                    await asyncio.to_thread(data_target.rename, data_source)
                raise
            if after_change is not None:
                await after_change()
            return target

    def data_root(self, plugin_id: str) -> Path:
        return self._data_root / plugin_id

    async def get(self, plugin_id: str) -> PluginSnapshot:
        row = await self._repository.plugin_record(plugin_id)
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
                "sandbox_limits_enforced": json.loads(str(row["sandbox_limits_json"])),
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


def _prepare_installed_package(plugin_id: str, path: Path, install_root: Path) -> None:
    resolved_root = install_root.resolve()
    if path.is_symlink():
        raise SkillExecutionError(
            "unsafe_plugin_path", f"Installed plugin path is unsafe: {plugin_id}"
        )
    resolved = path.resolve()
    if not resolved.is_relative_to(resolved_root) or resolved.name != plugin_id:
        raise SkillExecutionError(
            "unsafe_plugin_path", f"Installed plugin path is outside root: {plugin_id}"
        )
    _validate_tree(resolved)
    _make_package_read_only(resolved)


def _make_package_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _make_package_writable(root: Path) -> None:
    root.chmod(0o755)
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
