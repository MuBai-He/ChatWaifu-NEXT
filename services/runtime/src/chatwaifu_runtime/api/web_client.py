"""Optional same-origin product shell; Runtime APIs retain Bearer authentication."""

from fastapi import FastAPI
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from chatwaifu_runtime.config.settings import Settings

WEB_CLIENT_PREFIX = "/web-client"
WEB_CLIENT_PAGES = frozenset(
    {
        "/",
        "/knowledge",
        "/avatar-lab",
        "/settings",
        "/settings/channels",
        "/settings/agent",
        "/settings/models",
        "/settings/voice",
        "/settings/extensions",
        "/settings/memory",
        "/settings/knowledge",
        "/settings/personal-assistant",
        "/settings/connection",
    }
)


def is_web_client_request(settings: Settings, path: str, method: str) -> bool:
    return bool(
        settings.runtime.web_client_dir is not None
        and method in {"GET", "HEAD"}
        and (path in WEB_CLIENT_PAGES or path.startswith(f"{WEB_CLIENT_PREFIX}/"))
    )


def mount_web_client(app: FastAPI, settings: Settings) -> None:
    root = settings.runtime.web_client_dir
    if root is None:
        return
    index = root / "index.html"
    if not index.is_file():
        raise ValueError("runtime_web_client_build_missing")
    app.mount(WEB_CLIENT_PREFIX, StaticFiles(directory=root), name="runtime-web-client")

    def page() -> FileResponse:
        return FileResponse(
            index,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; "
                "script-src 'self' 'wasm-unsafe-eval'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                "font-src 'self' data:; media-src 'self' blob:; worker-src 'self' blob:; "
                "connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    for path in sorted(WEB_CLIENT_PAGES):
        app.add_api_route(path, page, methods=["GET", "HEAD"], include_in_schema=False)
