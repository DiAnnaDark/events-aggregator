import logging
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID, uuid4

import httpx

from app.application.tickets.state import TicketCancellationSnapshot

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


class CancellationStore(Protocol):
    async def get_ticket_for_cancellation(
        self,
        *,
        ticket_id: UUID,
    ) -> TicketCancellationSnapshot | None: ...

    async def prepare_cancellation(
        self,
        *,
        ticket_id: UUID,
        operation_id: UUID,
        request_fingerprint: str,
        now: datetime,
    ) -> bool: ...

    async def mark_cancellation_in_flight(
        self,
        *,
        ticket_id: UUID,
        operation_id: UUID,
        now: datetime,
    ) -> bool: ...

    async def abort_prepared_cancellation(
        self,
        *,
        ticket_id: UUID,
        operation_id: UUID,
        now: datetime,
    ) -> bool: ...

    async def finish_cancellation_success(
        self,
        *,
        ticket_id: UUID,
        operation_id: UUID,
        now: datetime,
    ) -> bool: ...

    async def record_cancellation_error(
        self,
        *,
        ticket_id: UUID,
        operation_id: UUID,
        outcome: Literal["failed", "unknown"],
        provider_http_status: int | None,
        error_code: str,
        now: datetime,
    ) -> bool: ...


class CancellationProvider(Protocol):
    async def unregister(
        self,
        *,
        event_id: UUID,
        ticket_id: UUID,
    ) -> None: ...


class CancellationSeatCache(Protocol):
    async def invalidate(
        self,
        event_id: UUID,
    ) -> None: ...


class TicketCancellationError(Exception):
    pass


class TicketNotFound(TicketCancellationError):
    pass


class CancellationUnavailable(TicketCancellationError):
    pass


class CancellationClosed(TicketCancellationError):
    pass


class CancellationConflict(TicketCancellationError):
    pass


class CancellationProviderRejected(TicketCancellationError):
    pass


class CancellationResultUnknown(TicketCancellationError):
    pass


class CancellationStateError(TicketCancellationError):
    pass


class TicketCancellationService:
    def __init__(
        self,
        *,
        store: CancellationStore,
        provider: CancellationProvider,
        seats: CancellationSeatCache,
        clock: Callable[[], datetime] = utc_now,
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self._store = store
        self._provider = provider
        self._seats = seats
        self._clock = clock
        self._new_id = new_id

    async def cancel(self, ticket_id: UUID) -> None:
        ticket = await self._store.get_ticket_for_cancellation(
            ticket_id=ticket_id,
        )

        if ticket is None:
            raise TicketNotFound("Ticket not found")

        if ticket.status == "cancelled":
            return

        if ticket.status != "active":
            raise CancellationUnavailable("Ticket is not available for cancellation")

        if ticket.provider_ticket_id is None:
            raise CancellationStateError("Active ticket has no provider identifier")

        now = self._clock()

        if now >= ticket.event_time:
            raise CancellationClosed("Event has already started")

        operation_id = self._new_id()

        fingerprint = sha256(f"cancel:{ticket_id}".encode()).hexdigest()

        prepared = await self._store.prepare_cancellation(
            ticket_id=ticket_id,
            operation_id=operation_id,
            request_fingerprint=fingerprint,
            now=now,
        )

        if not prepared:
            raise CancellationConflict(
                "Cancellation is unavailable or already in progress"
            )

        started = await self._store.mark_cancellation_in_flight(
            ticket_id=ticket_id,
            operation_id=operation_id,
            now=self._clock(),
        )

        if not started:
            aborted = await self._store.abort_prepared_cancellation(
                ticket_id=ticket_id,
                operation_id=operation_id,
                now=self._clock(),
            )

            if not aborted:
                raise CancellationStateError(
                    "Cancellation operation state could not be resolved"
                )

            raise CancellationConflict("Cancellation could not be started")

        try:
            await self._provider.unregister(
                event_id=ticket.event_id,
                ticket_id=ticket.provider_ticket_id,
            )

        except httpx.HTTPStatusError as error:
            status = error.response.status_code

            definitely_rejected = 400 <= status < 500 and status not in (408, 425, 429)

            outcome: Literal["failed", "unknown"] = (
                "failed" if definitely_rejected else "unknown"
            )

            await self._record_provider_error(
                ticket_id=ticket_id,
                operation_id=operation_id,
                outcome=outcome,
                provider_http_status=status,
                error_code=(
                    "provider_rejected" if definitely_rejected else "provider_error"
                ),
            )

            if definitely_rejected:
                raise CancellationProviderRejected(
                    "Events Provider rejected cancellation"
                ) from error

            raise CancellationResultUnknown("Cancellation result is unknown") from error

        except (httpx.RequestError, ValueError, TypeError) as error:
            await self._record_provider_error(
                ticket_id=ticket_id,
                operation_id=operation_id,
                outcome="unknown",
                provider_http_status=None,
                error_code="provider_response_unknown",
            )

            raise CancellationResultUnknown("Cancellation result is unknown") from error

        try:
            confirmed = await self._store.finish_cancellation_success(
                ticket_id=ticket_id,
                operation_id=operation_id,
                now=self._clock(),
            )
        except Exception as error:
            logger.exception(
                "Failed to persist cancellation confirmation: ticket=%s",
                ticket_id,
            )
            raise CancellationStateError(
                "Cancellation was confirmed externally but not saved"
            ) from error

        if not confirmed:
            raise CancellationStateError("Cancellation confirmation could not be saved")

        try:
            await self._seats.invalidate(ticket.event_id)
        except Exception:
            logger.exception(
                "Failed to invalidate seats after cancellation: event=%s",
                ticket.event_id,
            )

    async def _record_provider_error(
        self,
        *,
        ticket_id: UUID,
        operation_id: UUID,
        outcome: Literal["failed", "unknown"],
        provider_http_status: int | None,
        error_code: str,
    ) -> None:
        try:
            recorded = await self._store.record_cancellation_error(
                ticket_id=ticket_id,
                operation_id=operation_id,
                outcome=outcome,
                provider_http_status=provider_http_status,
                error_code=error_code,
                now=self._clock(),
            )
        except Exception as error:
            logger.exception(
                "Failed to persist cancellation error: ticket=%s",
                ticket_id,
            )
            raise CancellationStateError(
                "Cancellation error could not be saved"
            ) from error

        if not recorded:
            raise CancellationStateError("Cancellation error could not be saved")
