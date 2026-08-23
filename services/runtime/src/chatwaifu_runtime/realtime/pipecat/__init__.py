"""Pipecat-specific transport adapter; no Pipecat types escape this package."""

from chatwaifu_runtime.realtime.pipecat.session import PipecatMediaAdapter

__all__ = ["PipecatMediaAdapter"]
