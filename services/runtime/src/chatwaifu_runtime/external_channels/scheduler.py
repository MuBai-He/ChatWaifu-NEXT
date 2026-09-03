"""Background delivery scheduler for multipart channel delivery plans."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from chatwaifu_protocol.channels import (
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
)
from chatwaifu_protocol.errors import StructuredError

from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.models import (
    ChannelDeliveryPartDeferRequest,
    ChannelDeliveryPartRecord,
    ChannelDeliveryPlanRecord,
)
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository

logger = logging.getLogger(__name__)


class DeliveryPartOutcome(StrEnum):
    DELIVERED = "delivered"
    RETRYABLE_ERROR = "retryable_error"
    FATAL_ERROR = "fatal_error"


@dataclass(frozen=True, slots=True)
class DeliveryPartExecutionResult:
    outcome: DeliveryPartOutcome
    provider_message_id: str | None = None
    error: StructuredError | None = None


class DeliveryPartExecutor(Protocol):
    async def execute_part(
        self,
        plan: ChannelDeliveryPlanRecord,
        part: ChannelDeliveryPartRecord,
    ) -> DeliveryPartExecutionResult: ...


class ChannelDeliveryScheduler:
    """Independent scheduler that scans nonterminal delivery plans and drives parts to delivery."""

    def __init__(
        self,
        repository: ExternalChannelRepository,
        executor: DeliveryPartExecutor,
        publisher: EventPublisher | None = None,
        *,
        event_hub: EventHub | None = None,
        connection_id: UUID | None = None,
        lease_seconds: int = 60,
        max_attempts: int = 3,
        initial_backoff_seconds: float = 1.0,
        poll_interval_seconds: float = 1.0,
        on_plan_terminal: Callable[[ChannelDeliveryPlanRecord], Awaitable[None]] | None = None,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._executor = executor
        self._event_hub = event_hub
        self._connection_id = connection_id
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._initial_backoff_seconds = initial_backoff_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._on_plan_terminal = on_plan_terminal

        self._wake_event = asyncio.Event()
        self._running_task: asyncio.Task[None] | None = None
        self._event_subscription_task: asyncio.Task[None] | None = None
        self._stopping = False

    def wake(self) -> None:
        """Wake up the scheduler loop immediately."""
        self._wake_event.set()

    async def start(self) -> None:
        """Start the background scheduler task and event listener."""
        if self._running_task is not None:
            return
        self._stopping = False
        self._wake_event.set()
        self._running_task = asyncio.create_task(
            self.run(),
            name=f"delivery-scheduler-{self._connection_id or 'all'}",
        )
        if self._event_hub is not None:
            self._event_subscription_task = asyncio.create_task(
                self._listen_for_plan_events(),
                name=f"delivery-scheduler-events-{self._connection_id or 'all'}",
            )

    async def stop(self) -> None:
        """Stop the scheduler and clean up tasks."""
        self._stopping = True
        self.wake()
        tasks_to_cancel: list[asyncio.Task[None]] = []
        if self._running_task is not None:
            tasks_to_cancel.append(self._running_task)
            self._running_task = None
        if self._event_subscription_task is not None:
            tasks_to_cancel.append(self._event_subscription_task)
            self._event_subscription_task = None

        for task in tasks_to_cancel:
            task.cancel()
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

    async def _listen_for_plan_events(self) -> None:
        if self._event_hub is None:
            return
        subscription = self._event_hub.subscribe(
            lambda event: str(event.get("event_type")) == "channel.delivery_plan_created"
        )
        try:
            while not self._stopping:
                event_payload = await subscription.receive()
                if self._stopping:
                    break
                payload = event_payload.get("payload", {})
                if self._connection_id is None or payload.get("connection_id") == str(
                    self._connection_id
                ):
                    self.wake()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("delivery scheduler event subscription failed")
        finally:
            self._event_hub.unsubscribe(subscription)

    async def run(self) -> None:
        """Main scheduler loop."""
        while not self._stopping:
            try:
                progress = await self.step()
                if progress:
                    # If work was done, check immediately if next part is ready
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("unexpected error in delivery scheduler step")

            now = datetime.now(UTC)
            next_wakeup = await self._repository.next_delivery_wakeup_at(self._connection_id)
            sleep_time = self._poll_interval_seconds
            if next_wakeup is not None:
                delta = (next_wakeup - now).total_seconds()
                if delta > 0:
                    sleep_time = min(sleep_time, delta)
                else:
                    sleep_time = 0.0

            if sleep_time > 0:
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=sleep_time)
                except TimeoutError:
                    pass
            self._wake_event.clear()

    async def step(self, now: datetime | None = None) -> bool:
        """Execute one evaluation cycle over all nonterminal delivery plans.

        Returns True if any part was claimed and processed.
        """
        current_time = now or datetime.now(UTC)
        await self._repository.recover_expired_delivery_part_leases(as_of=current_time)

        plans = await self._repository.list_nonterminal_delivery_plans(self._connection_id)
        any_progress = False

        for plan in plans:
            # Check if this plan currently has a child part actively SENDING
            # under an unexpired lease
            has_active_sending = False
            for part in plan.parts:
                if part.status is ChannelDeliveryPartStatus.SENDING:
                    if part.lease_expires_at is not None and part.lease_expires_at > current_time:
                        has_active_sending = True
                        break
            if has_active_sending:
                continue

            # Find the first non-completed part by ordinal
            target_part: ChannelDeliveryPartRecord | None = None
            for part in plan.parts:
                if part.status not in (
                    ChannelDeliveryPartStatus.DELIVERED,
                    ChannelDeliveryPartStatus.CANCELLED,
                ):
                    target_part = part
                    break

            if target_part is None:
                continue

            # Check deferral (not_before_at)
            if target_part.not_before_at is not None and target_part.not_before_at > current_time:
                continue

            # Claim next part
            lease_id = uuid4()
            claim = ChannelDeliveryPartClaimRequest(
                delivery_id=plan.delivery_id,
                part_id=target_part.part_id,
                lease_id=lease_id,
                lease_seconds=self._lease_seconds,
            )
            claim_result = await self._repository.claim_next_delivery_part(
                claim, claimed_at=current_time
            )
            if claim_result is None or claim_result.part is None:
                continue

            # Publish persisted events from claim
            if self._publisher is not None:
                for ev in claim_result.persisted_events:
                    await self._publisher.publish_persisted(ev)

            claimed_part = claim_result.part
            any_progress = True

            # Check kind: Phase 17.1A only supports TEXT
            if claimed_part.kind is not ChannelDeliveryPartKind.TEXT:
                err = StructuredError(
                    code="unsupported_delivery_part_kind",
                    message=f"Delivery part kind {claimed_part.kind} is not supported.",
                    retryable=False,
                    component="external_channels",
                )
                ack = ChannelDeliveryPartAcknowledgement(
                    delivery_id=plan.delivery_id,
                    part_id=claimed_part.part_id,
                    lease_id=lease_id,
                    status=ChannelDeliveryPartStatus.FAILED,
                    error=err,
                    acknowledged_at=datetime.now(UTC),
                )
                ack_res = await self._repository.acknowledge_delivery_part(
                    ack, updated_at=datetime.now(UTC)
                )
                if self._publisher is not None:
                    for ev in ack_res.persisted_events:
                        await self._publisher.publish_persisted(ev)
                if self._on_plan_terminal and ack_res.plan.status in (
                    ChannelDeliveryStatus.DELIVERED,
                    ChannelDeliveryStatus.FAILED,
                    ChannelDeliveryStatus.CANCELLED,
                ):
                    await self._on_plan_terminal(ack_res.plan)
                continue

            # Execute part
            try:
                exec_result = await self._executor.execute_part(plan, claimed_part)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                exec_result = DeliveryPartExecutionResult(
                    outcome=DeliveryPartOutcome.RETRYABLE_ERROR,
                    error=StructuredError(
                        code="delivery_execution_failed",
                        message=str(exc),
                        retryable=True,
                        component="external_channels",
                    ),
                )

            post_time = datetime.now(UTC)
            if exec_result.outcome is DeliveryPartOutcome.DELIVERED:
                ack = ChannelDeliveryPartAcknowledgement(
                    delivery_id=plan.delivery_id,
                    part_id=claimed_part.part_id,
                    lease_id=lease_id,
                    status=ChannelDeliveryPartStatus.DELIVERED,
                    provider_message_id=exec_result.provider_message_id,
                    acknowledged_at=post_time,
                )
                ack_res = await self._repository.acknowledge_delivery_part(
                    ack, updated_at=post_time
                )
                if self._publisher is not None:
                    for ev in ack_res.persisted_events:
                        await self._publisher.publish_persisted(ev)
                if self._on_plan_terminal and ack_res.plan.status in (
                    ChannelDeliveryStatus.DELIVERED,
                    ChannelDeliveryStatus.FAILED,
                    ChannelDeliveryStatus.CANCELLED,
                ):
                    await self._on_plan_terminal(ack_res.plan)

            elif exec_result.outcome is DeliveryPartOutcome.RETRYABLE_ERROR:
                if claimed_part.attempt < self._max_attempts:
                    backoff_delay = self._initial_backoff_seconds * (
                        2 ** (claimed_part.attempt - 1)
                    )
                    not_before = post_time + timedelta(seconds=backoff_delay)
                    defer_req = ChannelDeliveryPartDeferRequest(
                        delivery_id=plan.delivery_id,
                        part_id=claimed_part.part_id,
                        lease_id=lease_id,
                        not_before_at=not_before,
                        error=exec_result.error,
                    )
                    defer_res = await self._repository.defer_delivery_part(
                        defer_req, updated_at=post_time
                    )
                    if self._publisher is not None:
                        for ev in defer_res.persisted_events:
                            await self._publisher.publish_persisted(ev)
                else:
                    # Max attempts exceeded -> mark FAILED
                    ack = ChannelDeliveryPartAcknowledgement(
                        delivery_id=plan.delivery_id,
                        part_id=claimed_part.part_id,
                        lease_id=lease_id,
                        status=ChannelDeliveryPartStatus.FAILED,
                        error=exec_result.error
                        or StructuredError(
                            code="delivery_part_max_attempts_exceeded",
                            message=f"Part exceeded max attempts ({self._max_attempts})",
                            retryable=False,
                            component="external_channels",
                        ),
                        acknowledged_at=post_time,
                    )
                    ack_res = await self._repository.acknowledge_delivery_part(
                        ack, updated_at=post_time
                    )
                    if self._publisher is not None:
                        for ev in ack_res.persisted_events:
                            await self._publisher.publish_persisted(ev)
                    if self._on_plan_terminal and ack_res.plan.status in (
                        ChannelDeliveryStatus.DELIVERED,
                        ChannelDeliveryStatus.FAILED,
                        ChannelDeliveryStatus.CANCELLED,
                    ):
                        await self._on_plan_terminal(ack_res.plan)

            else:  # FATAL_ERROR
                ack = ChannelDeliveryPartAcknowledgement(
                    delivery_id=plan.delivery_id,
                    part_id=claimed_part.part_id,
                    lease_id=lease_id,
                    status=ChannelDeliveryPartStatus.FAILED,
                    error=exec_result.error
                    or StructuredError(
                        code="delivery_part_fatal_error",
                        message="Part delivery encountered fatal error",
                        retryable=False,
                        component="external_channels",
                    ),
                    acknowledged_at=post_time,
                )
                ack_res = await self._repository.acknowledge_delivery_part(
                    ack, updated_at=post_time
                )
                if self._publisher is not None:
                    for ev in ack_res.persisted_events:
                        await self._publisher.publish_persisted(ev)
                if self._on_plan_terminal and ack_res.plan.status in (
                    ChannelDeliveryStatus.DELIVERED,
                    ChannelDeliveryStatus.FAILED,
                    ChannelDeliveryStatus.CANCELLED,
                ):
                    await self._on_plan_terminal(ack_res.plan)

        return any_progress
