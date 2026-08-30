"""Command-line entrypoint for the distributable worker-pack manager."""

from chatwaifu_model_worker.pack_installer import main

if __name__ == "__main__":
    raise SystemExit(main())
