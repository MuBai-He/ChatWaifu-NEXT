"""Recover agenda outcomes from provider facts; never replay an uncertain write."""

import base64
import hashlib
import json
from dataclasses import asdict
from datetime import date
from typing import cast
from uuid import UUID

from chatwaifu_protocol.agent import AgentTask
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.skills import SkillInvocation

from chatwaifu_runtime.personal_assistant.google_calendar import GoogleCalendarError
from chatwaifu_runtime.personal_assistant.google_tasks import GoogleTask
from chatwaifu_runtime.personal_assistant.integration import PersonalAssistantIntegration
from chatwaifu_runtime.personal_assistant.verification import verify_calendar


def _hash(value: str | None) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()


def _positions(identifier: str, size: int) -> list[int]:
    digest = hashlib.sha256(identifier.encode()).digest()
    return [
        int.from_bytes(digest[index : index + 4], "big") % (size * 8) for index in range(0, 24, 4)
    ]


def _baseline(items: tuple[GoogleTask, ...]) -> str:
    bits = bytearray(min(8192, max(1024, len(items) * 2)))
    for item in items:
        for position in _positions(item.id, len(bits)):
            bits[position // 8] |= 1 << (position % 8)
    return base64.b64encode(bits).decode()


def _contained(identifier: str, baseline: str) -> bool:
    bits = base64.b64decode(baseline, validate=True)
    return bool(bits) and all(
        bits[position // 8] & (1 << (position % 8))
        for position in _positions(identifier, len(bits))
    )


class AgendaActionRecovery:
    def __init__(self, integration: PersonalAssistantIntegration) -> None:
        self.integration = integration

    async def prepare(
        self, task: AgentTask, invocation: SkillInvocation, action_id: UUID
    ) -> JsonObject:
        if invocation.skill_id != "agenda.manage" or self.integration.agenda is None:
            return {}
        args = invocation.arguments
        if args.get("action") == "create" and not (
            args.get("account_id") and args.get("collection_id")
        ):
            destinations = await self.integration.agenda.destinations(str(task.session_id))
            destination = next((d for d in destinations if d.kind == args.get("kind")), None)
            if destination is None or destination.provider != "google":
                return {}
            account, collection = str(destination.account_id), destination.collection_id
        else:
            account, collection = (
                str(args.get("account_id", "")),
                str(args.get("collection_id", "")),
            )
        scope = (
            task.authorization.calendar_ids
            if args.get("kind") == "calendar"
            else task.authorization.tasklist_ids
        )
        if scope is not None and collection not in scope and f"{account}/{collection}" not in scope:
            return {}
        metadata: JsonObject = {
            "provider": "google",
            "kind": args.get("kind"),
            "action": args.get("action"),
            "account_id": account,
            "collection_id": collection,
            "item_id": action_id.hex
            if args.get("action") == "create" and args.get("kind") == "calendar"
            else args.get("item_id"),
            "title_hash": _hash(str(args["title"])) if "title" in args else None,
            "notes_hash": _hash(cast(str | None, args.get("notes")))
            if "notes" in args or args.get("action") == "create"
            else None,
            "due_requested": "due_date" in args
            or args.get("clear_due") is True
            or args.get("action") == "create",
            "due_date": args.get("due_date"),
            "start_date": args.get("start_date"),
            "end_date": args.get("end_date"),
            "start": args.get("start"),
            "end": args.get("end"),
        }
        if (
            args.get("kind") == "reminder"
            and args.get("action") == "create"
            and self.integration.accounts
        ):
            metadata["baseline"] = _baseline(
                await self.integration.accounts.tasks(str(task.session_id), account, collection)
            )
        return metadata

    async def recover(self, task: AgentTask, step: JsonObject) -> JsonObject | None:
        snapshot = step.get("snapshot")
        error = snapshot.get("error") if isinstance(snapshot, dict) else None
        if (
            step.get("skill_id") == "agenda.manage"
            and isinstance(error, dict)
            and error.get("code") in {"agenda_access_denied", "task_scope_denied"}
            and error.get("message")
            in {"calendar_outside_task_grant", "calendar_version_changed", "task_version_changed"}
        ):
            return {
                "execution_state": "not_started",
                "verified": False,
                "verification_source": "native_optimistic_concurrency_preflight"
                if error.get("message") in {"calendar_version_changed", "task_version_changed"}
                else "native_authorization_preflight",
            }
        raw = step.get("verification")
        if not isinstance(raw, dict) or self.integration.accounts is None:
            return None
        data = cast(JsonObject, raw)
        if data.get("provider") != "google":
            return None
        account, collection = str(data["account_id"]), str(data["collection_id"])
        if data.get("kind") == "reminder":
            return await self._task(task, data, account, collection)
        if data.get("kind") != "calendar" or not data.get("item_id"):
            return None
        if (
            collection not in task.authorization.calendar_ids
            and f"{account}/{collection}" not in task.authorization.calendar_ids
        ):
            return None
        identifier = str(data["item_id"])
        try:
            event = await self.integration.accounts.inspect_event(
                str(task.session_id), account, collection, identifier
            )
        except GoogleCalendarError as error:
            if error.code == "item_not_found" and data.get("action") == "delete":
                return {
                    "verified": True,
                    "provider": "google",
                    "state": "deleted",
                    "item_id": identifier,
                    "account_id": account,
                    "collection_id": collection,
                    "verification_source": "provider_readback",
                }
            return None
        if data.get("action") == "delete":
            return (
                {
                    "verified": True,
                    "provider": "google",
                    "state": "deleted",
                    "item_id": identifier,
                    "account_id": account,
                    "collection_id": collection,
                    "verification_source": "provider_readback",
                }
                if event.status == "cancelled"
                else None
            )
        changes: dict[str, object] = {}
        if isinstance(data.get("start"), str):
            changes["start"] = {"dateTime": data["start"]}
        if isinstance(data.get("end"), str):
            changes["end"] = {"dateTime": data["end"]}
        for key in ("start", "end"):
            if isinstance(data.get(key + "_date"), str):
                changes[key] = {"date": data[key + "_date"]}
        try:
            verify_calendar(event, identifier, changes)
        except GoogleCalendarError:
            return None
        if data.get("title_hash") and _hash(event.title) != data["title_hash"]:
            return None
        if data.get("notes_hash") and _hash(event.description) != data["notes_hash"]:
            return None
        return cast(
            JsonObject,
            json.loads(
                json.dumps(
                    {
                        "verified": True,
                        "provider": "google",
                        "state": "saved",
                        "account_id": account,
                        "collection_id": collection,
                        "item": asdict(event),
                        "verification_source": "provider_readback",
                    },
                    default=str,
                )
            ),
        )

    async def _task(
        self, task: AgentTask, data: JsonObject, account: str, collection: str
    ) -> JsonObject | None:
        service = self.integration.accounts
        assert service is not None
        allowed = task.authorization.tasklist_ids
        if (
            allowed is not None
            and collection not in allowed
            and f"{account}/{collection}" not in allowed
        ):
            return None
        if data.get("action") == "create":
            baseline = data.get("baseline")
            if not isinstance(baseline, str):
                return None
            candidates = [
                item
                for item in await service.tasks(str(task.session_id), account, collection)
                if not _contained(item.id, baseline) and self._matches(item, data)
            ]
            if len(candidates) != 1:
                return None
            actual = candidates[0]
        else:
            identifier = data.get("item_id")
            if not isinstance(identifier, str):
                return None
            try:
                actual = await service.inspect_task(
                    str(task.session_id), account, collection, identifier
                )
            except GoogleCalendarError as error:
                if error.code == "item_not_found" and data.get("action") == "delete":
                    return {
                        "verified": True,
                        "provider": "google",
                        "state": "deleted",
                        "account_id": account,
                        "collection_id": collection,
                        "item_id": identifier,
                        "verification_source": "provider_readback",
                    }
                return None
            if data.get("action") == "delete" or not self._matches(actual, data):
                return None
        return cast(
            JsonObject,
            json.loads(
                json.dumps(
                    {
                        "verified": True,
                        "provider": "google",
                        "state": "saved",
                        "account_id": account,
                        "collection_id": collection,
                        "item": asdict(actual),
                        "verification_source": "provider_readback",
                        "write_acknowledgement_recovered": False,
                    },
                    default=str,
                )
            ),
        )

    def _matches(self, item: GoogleTask, data: JsonObject) -> bool:
        if data.get("title_hash") and _hash(item.title) != data["title_hash"]:
            return False
        if data.get("notes_hash") and _hash(item.notes) != data["notes_hash"]:
            return False
        if data.get("action") == "complete" and item.status != "completed":
            return False
        if data.get("due_requested") is True:
            raw = data.get("due_date")
            due = date.fromisoformat(raw) if isinstance(raw, str) else None
            if item.due != due:
                return False
        return True
