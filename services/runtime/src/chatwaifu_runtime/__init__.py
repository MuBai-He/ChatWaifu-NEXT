"""ChatWaifu NEXT local application runtime."""

from typing import TYPE_CHECKING, Any

__version__ = "0.1.0"

if TYPE_CHECKING:
    from chatwaifu_runtime.main import create_app


def __getattr__(name: str) -> Any:
    """Keep the public factory lazy so frozen bootstraps can set resource paths first."""

    if name == "create_app":
        from chatwaifu_runtime.main import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["create_app"]
