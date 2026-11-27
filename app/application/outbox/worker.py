import asyncio
import logging
from collections.abc import Sequence
from contextlib import suppress
from typing import Protocol
from uuid import UUID

from app.core.enums import OutboxEventType

logger = logging.getLogger(__name__)


class OutboxEvent(Protocol):
    id: UUID
    event_type: OutboxEventType | str
    payload: dict[str, object]


class PendingOutboxStore(Protocol):
    async def get_pending(
        self,
        limit: int = 100,
    ) -> Sequence[OutboxEvent]: ...

    async def mark_sent(self, event_id: UUID) -> None: ...


class NotificationSender(Protocol):
    async def send(
        self,
        *,
        message: str,
        reference_id: str,
        idempotency_key: str,
    ) -> None: ...


class OutboxWorker:
    def __init__(
        self,
        *,
        store: PendingOutboxStore,
        sender: NotificationSender | None = None,
        poll_interval_seconds: float = 30.0,
        batch_size: int = 100,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("Poll interval must be positive")

        if batch_size <= 0:
            raise ValueError("Batch size must be positive")

        self._store = store
        self._sender = sender
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = batch_size
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return

        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task = self._task

        if task is None:
            return

        task.cancel()

        with suppress(asyncio.CancelledError):
            await task

        self._task = None

    async def run_once(self) -> int:
        pending = await self._store.get_pending(
            limit=self._batch_size,
        )

        if self._sender is None:
            return len(pending)

        for event in pending:
            try:
                if event.event_type != OutboxEventType.TICKET_PURCHASED:
                    logger.error(
                        "Unsupported outbox event type: %s",
                        event.event_type,
                    )
                    continue

                message = event.payload.get("message")
                ticket_id = event.payload.get("ticket_id")

                if (
                    not isinstance(message, str)
                    or not message.strip()
                    or not isinstance(ticket_id, str)
                    or not ticket_id.strip()
                ):
                    logger.error(
                        "Invalid outbox payload for event %s",
                        event.id,
                    )
                    continue

                await self._sender.send(
                    message=message,
                    reference_id=ticket_id,
                    idempotency_key=str(event.id),
                )

                await self._store.mark_sent(event.id)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Outbox notification failed for event %s",
                    event.id,
                )

        return len(pending)

    async def _run(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Outbox worker poll failed")

            await asyncio.sleep(self._poll_interval_seconds)
