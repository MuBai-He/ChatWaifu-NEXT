"""Generate isolated, tested candidate MCP packages. Installation is operator-only."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import signal
import sys
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from chatwaifu_protocol.agent import AgentDevelopmentPolicy, CandidateCreate, CandidateFeature
from chatwaifu_protocol.base import JsonObject, JsonValue
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field

from chatwaifu_runtime.agent.artifacts import ArtifactService
from chatwaifu_runtime.agent.development_ports import DevelopmentRepository
from chatwaifu_runtime.agent.structured import structured_result
from chatwaifu_runtime.providers.contracts import LlmProvider, LlmToolCallProtocolError
from chatwaifu_runtime.runtime_skills.registry import SkillRegistry, load_plugin_manifest
from chatwaifu_runtime.runtime_skills.service import RuntimeSkillService
from chatwaifu_runtime.runtime_skills.transports import PreparedStdioCommand, SandboxLauncher


class CandidateTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: JsonObject
    expected: JsonObject


class CandidateImplementation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    input_schema: JsonObject
    output_schema: JsonObject
    code: str = Field(min_length=1, max_length=32000)
    tests: list[CandidateTest] = Field(min_length=3, max_length=12)


_RUNNER = """
import importlib.util,json,sys
spec=importlib.util.spec_from_file_location("candidate","implementation.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
arguments=json.loads(sys.stdin.buffer.read())
result=json.dumps([module.run(item) for item in arguments],ensure_ascii=False)
sys.stdout.buffer.write(result.encode("utf-8"))
"""
_SERVER = """
from typing import Any
import importlib.util
from pathlib import Path
from mcp.server.mcpserver import MCPServer
spec=importlib.util.spec_from_file_location("candidate",Path(__file__).with_name("implementation.py"))
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
run=module.run
mcp=MCPServer("Candidate")
@mcp.tool()
def execute(arguments: dict[str,Any]) -> dict[str,Any]:
    return run(arguments)
if __name__ == "__main__":
    mcp.run()
"""


class CandidateDevelopmentService:
    def __init__(
        self,
        repository: DevelopmentRepository,
        artifacts: ArtifactService,
        skills: RuntimeSkillService,
        root: Path,
        launcher: SandboxLauncher,
        model_factory: Callable[[], LlmProvider],
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts
        self.skills = skills
        self.root = root
        self.launcher = launcher
        self.model_factory = model_factory
        self._workers: dict[UUID, asyncio.Task[None]] = {}
        self._closing = False
        self._approval_lock = asyncio.Lock()

    async def start(self) -> None:
        self._closing = False
        for candidate in await self.repository.list():
            if candidate.state in {"queued", "developing"}:
                await self._save(candidate, state="failed", test_summary="development_interrupted")

    async def stop(self) -> None:
        self._closing = True
        workers = tuple(self._workers.values())
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    async def configure(self, policy: AgentDevelopmentPolicy) -> AgentDevelopmentPolicy:
        if not await self.repository.configure(
            policy.model_copy(update={"revision": policy.revision + 1}), policy.revision
        ):
            raise ValueError("development policy revision conflict")
        if not policy.enabled:
            await self.stop()
        else:
            self._closing = False
        return await self.repository.policy()

    async def create(self, request: CandidateCreate) -> CandidateFeature:
        if await self.artifacts.scope(request.session_id) != "local":
            raise PermissionError("candidate development is owner-only")
        if self._closing or not (await self.repository.policy()).enabled:
            raise PermissionError("candidate development is disabled")
        now = datetime.now(UTC)
        candidate = CandidateFeature(
            candidate_id=uuid4(),
            session_id=request.session_id,
            goal=request.goal,
            source_ref=request.source_ref,
            state="queued",
            created_at=now,
            updated_at=now,
        )
        if not await self.repository.reserve(
            candidate, now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        ):
            raise PermissionError("daily candidate budget exhausted or development disabled")
        self._workers[candidate.candidate_id] = asyncio.create_task(self._develop(candidate))
        return candidate

    async def approve(
        self, candidate_id: UUID, expected_revision: int, sha256: str
    ) -> CandidateFeature:
        async with self._approval_lock:
            candidate = await self.repository.get(candidate_id)
            if (
                candidate is None
                or candidate.revision != expected_revision
                or candidate.state != "tested"
                or candidate.package_sha256 != sha256
            ):
                raise ValueError("candidate approval or package identity changed")
            directory = self.root / candidate_id.hex
            if _package_hash(directory) != sha256:
                raise ValueError("candidate package changed after testing")
            installed = next(
                (p for p in await self.skills.list_plugins() if p.plugin_id == candidate.plugin_id),
                None,
            )
            if installed is None:
                await self.skills.install_plugin(directory)
            elif _package_hash(Path(installed.install_path)) != sha256:
                raise ValueError("installed candidate identity differs from approved package")
            return await self._save(candidate, state="approved")

    async def create_skill(self, session_id: str, arguments: JsonObject) -> JsonObject:
        candidate = await self.create(
            CandidateCreate(
                session_id=UUID(session_id),
                goal=str(arguments["goal"]),
                source_ref="owner-confirmed-development:" + session_id,
            )
        )
        return {"candidate": candidate.model_dump(mode="json"), "enabled": False}

    async def _save(self, candidate: CandidateFeature, **changes: object) -> CandidateFeature:
        changed = candidate.model_copy(
            update={
                **changes,
                "revision": candidate.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        if not await self.repository.save(changed, candidate.revision):
            raise ValueError("candidate revision conflict")
        return changed

    async def _develop(self, candidate: CandidateFeature) -> None:
        try:
            candidate = await self._save(candidate, state="developing")
            directory = self.root / candidate.candidate_id.hex
            directory.mkdir(parents=True, exist_ok=False)
            (directory / "test_runner.py").write_text(_RUNNER, encoding="utf-8")
            # Preflight the actual enforcing sandbox before asking the model to write code.
            command = self.launcher.prepare(
                PreparedStdioCommand(
                    command=sys.executable,
                    args=("-I", "-B", "test_runner.py"),
                    cwd=directory,
                    env={"PATH": os.environ.get("PATH", "")},
                    read_only_roots=await asyncio.to_thread(_python_roots),
                    sandbox_subject_id="candidate:" + candidate.candidate_id.hex,
                ),
                trust_level="untrusted",
                sandbox_mode="required",
                network_policy="deny",
            )
            if command.sandbox_backend in {None, "none"}:
                raise PermissionError("development requires an enforcing sandbox")
            feedback = ""
            implementation: CandidateImplementation | None = None
            for attempt in range(3):
                try:
                    async with asyncio.timeout(90):
                        implementation = await structured_result(
                            self.model_factory(),
                            CandidateImplementation,
                            "implement_candidate",
                            "Develop a small stateless candidate for the owner's stated gap. "
                            "Python standard library only. Implement run(arguments: dict)->dict. "
                            "No network, shell, file access, application imports or credentials. "
                            "Return strict object input/output schemas and three meaningful tests "
                            "using tests[].arguments and tests[].expected "
                            "(objects, never JSON strings), "
                            "including an edge case. The candidate is untrusted "
                            "and cannot install itself. For goals needing production integration, "
                            "implement "
                            "only the standalone transform and describe its limits.",
                            json.dumps(
                                {"goal": candidate.goal, "previous_test_failure": feedback},
                                ensure_ascii=False,
                            ),
                        )
                    for schema in (implementation.input_schema, implementation.output_schema):
                        Draft202012Validator.check_schema(schema)
                        if (
                            schema.get("type") != "object"
                            or len(json.dumps(schema).encode()) > 8000
                        ):
                            raise ValueError("candidate requires bounded object schemas")
                        if "$ref" in json.dumps(schema):
                            raise ValueError(
                                "candidate schemas cannot reference external resources"
                            )
                    (directory / "implementation.py").write_text(
                        implementation.code, encoding="utf-8"
                    )
                    digest = _package_hash(directory)
                    result = await _sandbox_results(
                        command, [t.arguments for t in implementation.tests]
                    )
                    if digest != _package_hash(directory):
                        raise PermissionError("candidate modified its source during tests")
                    if result != [t.expected for t in implementation.tests]:
                        raise ValueError("candidate test outputs did not match expected results")
                    for test, actual in zip(implementation.tests, result, strict=True):
                        _validate_candidate_schema(implementation.input_schema, test.arguments)
                        _validate_candidate_schema(implementation.output_schema, actual)
                    break
                except (ValueError, SchemaError, LlmToolCallProtocolError) as error:
                    feedback = str(error)[:1000]
                    if attempt == 2:
                        raise
            if implementation is None:
                raise ValueError("candidate implementation missing")
            plugin_id = "candidate." + candidate.candidate_id.hex
            _write_package(directory, plugin_id, implementation)
            manifest = load_plugin_manifest(directory)
            registry = SkillRegistry(directory / "empty-builtin")
            registry.reload([(manifest, directory, False)])
            (directory / "test_runner.py").unlink()
            digest = _package_hash(directory)
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(directory.iterdir()):
                    archive.write(path, path.name)
            artifact = await self.artifacts.save(
                candidate.session_id, plugin_id + ".zip", "application/zip", output.getvalue()
            )
            await self._save(
                candidate,
                state="tested",
                plugin_id=plugin_id,
                artifact=artifact,
                package_sha256=digest,
                test_summary=f"{len(implementation.tests)} sandboxed smoke tests passed; "
                f"backend={command.sandbox_backend}; network=deny. Owner review required.",
            )
        except asyncio.CancelledError:
            await self._save(candidate, state="failed", test_summary="development_cancelled")
            raise
        except Exception as error:
            await self._save(candidate, state="blocked", test_summary=type(error).__name__)
        finally:
            self._workers.pop(candidate.candidate_id, None)


def _validate_candidate_schema(schema: JsonObject, value: JsonObject | JsonValue) -> None:
    # jsonschema's stubs leave validate partially unknown, as in the skill gateway.
    Draft202012Validator(schema).validate(value)  # pyright: ignore[reportUnknownMemberType]


def _python_roots() -> tuple[Path, ...]:
    return Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()


def _package_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise ValueError("candidate package must contain only regular files")
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_package(
    directory: Path, plugin_id: str, implementation: CandidateImplementation
) -> None:
    manifest = {
        "schema_version": "1.0",
        "plugin_id": plugin_id,
        "version": "1.0.0",
        "name": implementation.name,
        "description": implementation.description,
        "transport": {
            "kind": "stdio",
            "command": ["python", "server.py"],
            "trust_level": "untrusted",
            "sandbox_mode": "required",
            "network_policy": "deny",
        },
        "skills": ["chatwaifu.yaml"],
    }
    skill: JsonObject = {
        "schema_version": "1.0",
        "instructions": "SKILL.md",
        "definition": {
            "skill_id": plugin_id,
            "version": "1.0.0",
            "name": implementation.name,
            "description": implementation.description,
            "interruptible": True,
            "background_allowed": True,
            "capabilities": [
                {
                    "name": "execute",
                    "adapter_tool": "execute",
                    "description": implementation.description,
                    "side_effect": "read",
                    "required_permissions": [plugin_id + ".execute"],
                    "confirmation_required": True,
                    "timeout_seconds": 10,
                    "input_schema": {
                        "type": "object",
                        "properties": {"arguments": implementation.input_schema},
                        "required": ["arguments"],
                        "additionalProperties": False,
                    },
                    "output_schema": implementation.output_schema,
                }
            ],
        },
        "adapter": {"kind": "mcp", "tool": "execute"},
    }
    (directory / "plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (directory / "chatwaifu.yaml").write_text(
        json.dumps(skill, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (directory / "server.py").write_text(_SERVER, encoding="utf-8")
    (directory / "SKILL.md").write_text(
        f"---\nname: Candidate capability\ndescription: Owner reviewed candidate\n"
        f"id: {plugin_id}\nversion: 1.0.0\n---\n\n"
        "Stateless standalone transform only. Runtime permissions and sandbox always apply.\n",
        encoding="utf-8",
    )


async def _sandbox_results(
    command: PreparedStdioCommand, inputs: list[JsonObject]
) -> list[JsonValue]:
    process = await asyncio.create_subprocess_exec(
        command.command,
        *command.args,
        cwd=command.cwd,
        env=command.env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=os.name != "nt",
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(inputs, ensure_ascii=False).encode())
        await process.stdin.drain()
        process.stdin.close()
        async with asyncio.timeout(10):
            output = bytearray()
            while chunk := await process.stdout.read(8192):
                output.extend(chunk)
                if len(output) > 64_000:
                    raise ValueError("candidate output exceeds bound")
            await process.wait()
        if process.returncode != 0:
            raise ValueError("candidate sandbox test failed")
        result: object = json.loads(output)
        if not isinstance(result, list):
            raise ValueError("candidate returned invalid test results")
        results = cast(list[JsonValue], result)
        if len(results) != len(inputs):
            raise ValueError("candidate returned invalid test results")
        return results
    finally:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.returncode is None:
            process.kill()
        await process.wait()
