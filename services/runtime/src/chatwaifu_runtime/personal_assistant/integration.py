"""Optional Runtime-owned personal assistant lifecycle."""

import asyncio
from contextlib import suppress
from typing import Literal

from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.persistence.async_secret_store import AsyncSecretStore
from chatwaifu_runtime.persistence.atomic_secret_store import AtomicSecretStore
from chatwaifu_runtime.personal_assistant.accounts import GoogleAccountService, GoogleClient
from chatwaifu_runtime.personal_assistant.google_calendar import GoogleCalendarAdapter
from chatwaifu_runtime.personal_assistant.oauth import GoogleOAuthCoordinator
from chatwaifu_runtime.personal_assistant.repository import AssistantRepository


class PersonalAssistantIntegration:
    def __init__(self, settings: Settings, repository: AssistantRepository) -> None:
        config = settings.personal_assistant
        self.state: Literal["disabled", "unconfigured", "ready", "cleanup_failed"] = "disabled"
        self.accounts: GoogleAccountService | None = None
        self.oauth: GoogleOAuthCoordinator | None = None
        self._adapter: GoogleCalendarAdapter | None = None
        self._maintenance: asyncio.Task[None] | None = None
        if not config.enabled:
            return
        if not config.google_client_id.strip():
            self.state = "unconfigured"
            return
        client = GoogleClient(
            config.google_client_id.strip(),
            config.google_client_secret.get_secret_value() if config.google_client_secret else None,
        )
        self._adapter = GoogleCalendarAdapter()
        self.accounts = GoogleAccountService(
            repository,
            AsyncSecretStore(
                AtomicSecretStore(
                    settings.config_dir / "personal-assistant-secrets.json",
                )
            ),
            self._adapter,
            client,
        )
        self.oauth = GoogleOAuthCoordinator(repository, self._adapter, self.accounts, client)
        self.state = "ready"

    async def start(self) -> None:
        if self.accounts is not None and self._maintenance is None:
            self._maintenance = asyncio.create_task(self._reconcile(), name="assistant-cleanup")

    async def _reconcile(self) -> None:
        assert self.accounts is not None
        try:
            await self.accounts.reconcile()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Secret store errors can contain paths; status stays bounded and safe.
            self.state = "cleanup_failed"

    async def close(self) -> None:
        if self.oauth is not None:
            await self.oauth.close()
        if self._maintenance is not None:
            self._maintenance.cancel()
            with suppress(asyncio.CancelledError):
                await self._maintenance
            self._maintenance = None
        if self._adapter is not None:
            await self._adapter.close()
