"""PyInstaller entrypoint for the self-contained Desktop Runtime."""

from chatwaifu_runtime.desktop_sidecar import main

if __name__ == "__main__":
    raise SystemExit(main())
