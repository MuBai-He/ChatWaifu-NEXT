"""Normalized Runtime Skill failures."""

from chatwaifu_protocol.errors import StructuredError


class SkillExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.structured = StructuredError.model_validate(
            {
                "code": code,
                "message": message,
                "retryable": retryable,
                "component": "runtime.skills",
                "details": details or {},
            }
        )
