from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID, uuid4

import httpx

from app.application.events.schemas import EventDetail
from app.application.tickets.schemas import TicketCreate


def utc_now() -> datetime:
    return datetime.now(UTC)


class EventReader(Protocol):
    async def get_event(
        self,
        event_id: UUID,
    ) -> EventDetail | None: ...


class SeatAvailability(Protocol):
    async def get_available_seats(
        self,
        event_id: UUID,
    ) -> list[str]: ...

    async def invalidate(
        self,
        event_id: UUID,
    ) -> None: ...


class RegistrationProvider(Protocol):
    async def register(
        self,
        *,
        event_id: UUID,
        first_name: str,
        last_name: str,
        email: str,
        seat: str,
    ) -> UUID: ...


class RegistrationStore(Protocol):
    async def reserve_registration(
        self,
        *,
        registration_id: UUID,
        operation_id: UUID,
        request: TicketCreate,
        request_fingerprint: str,
        now: datetime,
    ) -> bool: ...

    async def mark_registration_in_flight(
        self,
        *,
        registration_id: UUID,
        operation_id: UUID,
        now: datetime,
    ) -> bool: ...

    async def finish_registration_success(
        self,
        *,
        registration_id: UUID,
        operation_id: UUID,
        provider_ticket_id: UUID,
        now: datetime,
    ) -> bool: ...

    async def record_registration_error(
        self,
        *,
        registration_id: UUID,
        operation_id: UUID,
        outcome: Literal["failed", "unknown"],
        provider_http_status: int | None,
        error_code: str,
        now: datetime,
    ) -> bool: ...


class TicketRegistrationError(Exception):
    pass


class EventNotFound(TicketRegistrationError):
    pass


class RegistrationUnavailable(TicketRegistrationError):
    pass


class RegistrationClosed(TicketRegistrationError):
    pass


class SeatUnavailable(TicketRegistrationError):
    pass


class SeatLookupUnavailable(TicketRegistrationError):
    pass


class RegistrationStateError(TicketRegistrationError):
    pass


class ProviderRegistrationRejected(TicketRegistrationError):
    pass


class ProviderResultUnknown(TicketRegistrationError):
    pass


class TicketRegistrationService:
    def __init__(
        self,
        *,
        events: EventReader,
        seats: SeatAvailability,
        provider: RegistrationProvider,
        store: RegistrationStore,
        clock: Callable[[], datetime] = utc_now,
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self._events = events
        self._seats = seats
        self._provider = provider
        self._store = store
        self._clock = clock
        self._new_id = new_id

    async def register(self, request: TicketCreate) -> UUID:
        event = await self._events.get_event(request.event_id)

        if event is None:
            raise EventNotFound("Event not found")

        if event.status != "published":
            raise RegistrationUnavailable("Event is not available for registration")

        now = self._clock()

        if now >= event.registration_deadline or now >= event.event_time:
            raise RegistrationClosed("Registration is closed")

        try:
            available_seats = await self._seats.get_available_seats(request.event_id)
        except (httpx.HTTPError, ValueError) as error:
            raise SeatLookupUnavailable("Unable to check available seats") from error

        if request.seat not in available_seats:
            raise SeatUnavailable("Selected seat is unavailable")

        now = self._clock()

        if now >= event.registration_deadline or now >= event.event_time:
            raise RegistrationClosed("Registration is closed")

        registration_id = self._new_id()
        operation_id = self._new_id()

        fingerprint = sha256(request.model_dump_json().encode("utf-8")).hexdigest()

        reserved = await self._store.reserve_registration(
            registration_id=registration_id,
            operation_id=operation_id,
            request=request,
            request_fingerprint=fingerprint,
            now=now,
        )

        if not reserved:
            raise SeatUnavailable("Selected seat is already reserved")

        started = await self._store.mark_registration_in_flight(
            registration_id=registration_id,
            operation_id=operation_id,
            now=self._clock(),
        )

        if not started:
            raise RegistrationStateError("Registration operation could not be started")

        try:
            provider_ticket_id = await self._provider.register(
                event_id=request.event_id,
                first_name=request.first_name,
                last_name=request.last_name,
                email=str(request.email),
                seat=request.seat,
            )

        except httpx.HTTPStatusError as error:
            status = error.response.status_code

            definitely_rejected = 400 <= status < 500 and status not in (408, 425, 429)

            outcome: Literal["failed", "unknown"] = (
                "failed" if definitely_rejected else "unknown"
            )

            await self._record_provider_error(
                registration_id=registration_id,
                operation_id=operation_id,
                event_id=request.event_id,
                outcome=outcome,
                provider_http_status=status,
                error_code="provider_rejected"
                if definitely_rejected
                else "provider_error",
            )

            if definitely_rejected:
                raise ProviderRegistrationRejected(
                    "Events Provider rejected registration"
                ) from error

            raise ProviderResultUnknown("Registration result is unknown") from error

        except (httpx.RequestError, ValueError, TypeError) as error:
            await self._record_provider_error(
                registration_id=registration_id,
                operation_id=operation_id,
                event_id=request.event_id,
                outcome="unknown",
                provider_http_status=None,
                error_code="provider_response_unknown",
            )

            raise ProviderResultUnknown("Registration result is unknown") from error

        confirmed = await self._store.finish_registration_success(
            registration_id=registration_id,
            operation_id=operation_id,
            provider_ticket_id=provider_ticket_id,
            now=self._clock(),
        )

        if not confirmed:
            raise RegistrationStateError("Registration confirmation could not be saved")

        await self._seats.invalidate(request.event_id)

        return registration_id

    async def _record_provider_error(
        self,
        *,
        registration_id: UUID,
        operation_id: UUID,
        event_id: UUID,
        outcome: Literal["failed", "unknown"],
        provider_http_status: int | None,
        error_code: str,
    ) -> None:
        recorded = await self._store.record_registration_error(
            registration_id=registration_id,
            operation_id=operation_id,
            outcome=outcome,
            provider_http_status=provider_http_status,
            error_code=error_code,
            now=self._clock(),
        )

        if not recorded:
            raise RegistrationStateError("Registration error could not be saved")

        await self._seats.invalidate(event_id)
