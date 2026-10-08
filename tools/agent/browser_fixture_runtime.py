"""Disposable local API fixture for real-browser Agent settings acceptance."""

import argparse
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.main import create_app
from fastapi import FastAPI


def fixture_app(root: Path) -> FastAPI:
    settings = Settings.model_validate(
        {
            "config_dir": root / "config",
            "data_dir": root / "data",
            "storage": {"database_path": root / "runtime.db"},
            "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
            "tts": {"provider": "fake"},
            "security": {
                "admin_token": "disposable-agent-browser-fixture",
                "allowed_origins": [
                    "http://127.0.0.1:4173",
                    "http://127.0.0.1:4183",
                    "http://127.0.0.1:4184",
                    "http://127.0.0.1:4186",
                    "http://127.0.0.1:4187",
                ],
            },
        }
    )
    app = create_app(settings)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        async with original(application):
            container: RuntimeContainer = application.state.container
            await container.agent_tasks.stop()
            session = await container.sessions.create_session("default")
            await container.workspace_skills.word(
                str(session.session_id),
                {
                    "title": "browser-check",
                    "sections": [
                        {
                            "heading": "实际文件",
                            "paragraphs": ["中文内容、数字 42 与权限范围保持完整。"],
                        }
                    ],
                },
            )
            yield

    app.router.lifespan_context = lifespan
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8778)
    args = parser.parse_args()
    if not args.root.resolve().is_relative_to(Path("/tmp").resolve()) or args.port == 8765:
        raise ValueError("browser fixture must use a disposable temp directory and separate port")
    uvicorn.run(fixture_app(args.root), host="127.0.0.1", port=args.port)
