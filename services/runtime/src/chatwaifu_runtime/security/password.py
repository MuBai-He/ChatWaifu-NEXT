"""Password hashes on disk; bounded, expiring random client credentials in memory."""

import hashlib
import hmac
import logging
import math
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from chatwaifu_runtime.persistence.atomic_secret_store import AtomicSecretStore, SecretStoreError

SESSION_TTL_SECONDS = 24 * 60 * 60
logger = logging.getLogger(__name__)


class PasswordStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    configured: bool
    revision: int


class PasswordLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr = Field(min_length=1, max_length=128)


class PasswordUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_password: SecretStr = Field(min_length=12, max_length=128)
    current_password: SecretStr | None = Field(default=None, max_length=128)
    expected_revision: int = Field(ge=0)


class PasswordSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    token: str = Field(repr=False)
    expires_in: int = SESSION_TTL_SECONDS


class PasswordAuthError(Exception):
    def __init__(self, status: int, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class _PasswordRecord:
    revision: int
    salt: bytes = field(repr=False)
    digest: bytes = field(repr=False)


class RuntimePasswordAuth:
    """Blocking hashing/storage runs only in FastAPI's worker pool.

    Guard reads are constant-time dictionary lookups and never wait on the hashing
    lock. Credential mutations publish a new immutable revision after persistence.
    Existing operator/bootstrap tokens remain available for compatibility.
    """

    def __init__(self, config_dir: Path) -> None:
        self._store = AtomicSecretStore(config_dir / "runtime-password.json")
        self._lock = threading.Lock()
        self._attempts: deque[float] = deque()
        self._sessions: dict[str, tuple[int, float]] = {}
        self._record: _PasswordRecord | None = None
        raw = self._store.get("password")
        if raw:
            try:
                version, revision, salt, digest = raw.split(":")
                record = _PasswordRecord(int(revision), bytes.fromhex(salt), bytes.fromhex(digest))
                if (
                    version != "scrypt-v1"
                    or record.revision < 1
                    or len(record.salt) != 16
                    or len(record.digest) != 32
                ):
                    raise ValueError("invalid password record")
                self._record = record
            except ValueError as error:
                raise SecretStoreError(
                    "Runtime password storage is corrupt; it will not be overwritten"
                ) from error

    def status(self) -> PasswordStatus:
        record = self._record
        return PasswordStatus(
            configured=record is not None, revision=record.revision if record else 0
        )

    @staticmethod
    def _derive(password: str, salt: bytes) -> bytes:
        # OWASP's 32 MiB scrypt profile; fixed parameters cannot be supplied by clients.
        try:
            encoded = password.encode("utf-8")
        except UnicodeEncodeError as error:
            raise PasswordAuthError(422, "密码包含无效字符。") from error
        return hashlib.scrypt(
            encoded,
            salt=salt,
            n=2**15,
            r=8,
            p=3,
            maxmem=64 * 1024 * 1024,
            dklen=32,
        )

    def _admit_attempt(self) -> None:
        now = time.monotonic()
        while self._attempts and self._attempts[0] <= now - 60:
            self._attempts.popleft()
        # Global single-owner limit also works behind a proxy and cannot be evaded
        # by spoofing forwarded addresses. Successful attempts count as well.
        if len(self._attempts) >= 5:
            raise PasswordAuthError(
                429, "尝试次数过多，请稍后重试。", max(1, math.ceil(self._attempts[0] + 60 - now))
            )
        self._attempts.append(now)

    def _verify(self, password: str) -> None:
        self._admit_attempt()
        record = self._record
        if record is None or not hmac.compare_digest(
            self._derive(password, record.salt), record.digest
        ):
            raise PasswordAuthError(401, "密码不正确或服务器尚未设置密码。")

    def _issue_session(self) -> PasswordSession:
        now = time.monotonic()
        self._sessions = {key: value for key, value in self._sessions.items() if value[1] > now}
        if len(self._sessions) >= 128:
            raise PasswordAuthError(429, "登录会话过多，请在已有设备上使用连接或稍后重试。")
        token = secrets.token_urlsafe(32)
        record = self._record
        assert record is not None
        self._sessions[hashlib.sha256(token.encode()).hexdigest()] = (
            record.revision,
            now + SESSION_TTL_SECONDS,
        )
        return PasswordSession(token=token)

    def is_token_valid(self, token: str) -> bool:
        claims = self._sessions.get(hashlib.sha256(token.encode("utf-8")).hexdigest())
        record = self._record
        return bool(
            claims and record and claims[0] == record.revision and claims[1] > time.monotonic()
        )

    def login(self, password: str) -> PasswordSession:
        with self._lock:
            self._verify(password)
            session = self._issue_session()
            logger.info("runtime_password_login")
            return session

    def update(self, update: PasswordUpdate) -> PasswordSession:
        with self._lock:
            if update.expected_revision != self.status().revision:
                raise PasswordAuthError(409, "密码设置已变化，请重新读取后再修改。")
            if self._record:
                self._verify(
                    update.current_password.get_secret_value() if update.current_password else ""
                )
            salt = secrets.token_bytes(16)
            digest = self._derive(update.new_password.get_secret_value(), salt)
            revision = update.expected_revision + 1
            self._store.set("password", f"scrypt-v1:{revision}:{salt.hex()}:{digest.hex()}")
            self._record = _PasswordRecord(revision, salt, digest)
            self._sessions.clear()
            logger.info("runtime_password_changed", extra={"revision": revision})
            return self._issue_session()
