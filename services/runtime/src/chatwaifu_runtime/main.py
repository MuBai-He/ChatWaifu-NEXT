"""FastAPI application factory and development entrypoint."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chatwaifu_runtime.api.routes import router
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings, load_settings
from chatwaifu_runtime.mcp_server import RuntimeMcpServer
from chatwaifu_runtime.observability.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or load_settings()
    container = RuntimeContainer(resolved_settings)
    mcp_server = RuntimeMcpServer(container)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.container = container
        app.state.mcp_server = mcp_server
        await container.start()
        try:
            await mcp_server.start()
            try:
                yield
            finally:
                await mcp_server.stop()
        finally:
            await container.stop()

    app = FastAPI(title="ChatWaifu NEXT Runtime", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[resolved_settings.runtime.web_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "Last-Event-ID",
            "MCP-Protocol-Version",
            "MCP-Session-Id",
        ],
        expose_headers=["MCP-Protocol-Version", "MCP-Session-Id"],
    )
    app.include_router(router)
    # Keep this catch-all mount last so existing /v1 and documentation routes
    # retain precedence while the official MCP app owns the exact /mcp path.
    app.mount("/", mcp_server.app, name="mcp")
    return app


def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=settings.runtime.host,
        port=settings.runtime.port,
        log_config=None,
    )


if __name__ == "__main__":
    run()
