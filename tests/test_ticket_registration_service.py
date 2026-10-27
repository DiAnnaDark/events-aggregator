import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

from app.application.tickets.registration_service import (
    EventNotFound,
    ProviderRegistrationRejected,
    ProviderResultUnknown,
    RegistrationClosed,
    RegistrationStateError,
    RegistrationUnavailable,
    SeatLookupUnavailable,
    SeatUnavailable,
    TicketRegistrationService,
)
from app.application.tickets.schemas import TicketCreate

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@dataclass
class Scenario:
    service: TicketRegistrationService
    request: TicketCreate
    event: SimpleNamespace
    events: SimpleNamespace
    seats: SimpleNamespace
    provider: SimpleNamespace
    store: SimpleNamespace
    registration_id: UUID
    operation_id: UUID
    provider_ticket_id: UUID


def make_scenario() -> Scenario:
    request = TicketCreate(
        event_id=uuid4(),
        first_name="Anna",
        last_name="Test",
        email="anna@example.com",
        seat="A15",
    )

    event = SimpleNamespace(
        status="published",
        registration_deadline=NOW + timedelta(days=1),
        event_time=NOW + timedelta(days=2),
    )

    events = SimpleNamespace(
        get_event=AsyncMock(return_value=event),
    )

    seats = SimpleNamespace(
        get_available_seats=AsyncMock(return_value=["A15", "A16"]),
        invalidate=AsyncMock(),
    )

    provider_ticket_id = uuid4()

    provider = SimpleNamespace(
        register=AsyncMock(return_value=provider_ticket_id),
    )

    store = SimpleNamespace(
        reserve_registration=AsyncMock(return_value=True),
        mark_registration_in_flight=AsyncMock(return_value=True),
        finish_registration_success=AsyncMock(return_value=True),
        record_registration_error=AsyncMock(return_value=True),
    )

    registration_id = uuid4()
    operation_id = uuid4()

    identifiers = iter([registration_id, operation_id])

    service = TicketRegistrationService(
        events=events,
        seats=seats,
        provider=provider,
        store=store,
        clock=lambda: NOW,
        new_id=lambda: next(identifiers),
    )

    return Scenario(
        service=service,
        request=request,
        event=event,
        events=events,
        seats=seats,
        provider=provider,
        store=store,
        registration_id=registration_id,
        operation_id=operation_id,
        provider_ticket_id=provider_ticket_id,
    )


def test_successful_registration() -> None:
    scenario = make_scenario()

    ticket_id = asyncio.run(scenario.service.register(scenario.request))

    assert ticket_id == scenario.registration_id

    scenario.events.get_event.assert_awaited_once_with(scenario.request.event_id)

    scenario.seats.get_available_seats.assert_awaited_once_with(
        scenario.request.event_id
    )

    scenario.store.reserve_registration.assert_awaited_once()
    scenario.store.mark_registration_in_flight.assert_awaited_once()

    scenario.provider.register.assert_awaited_once()

    scenario.store.finish_registration_success.assert_awaited_once()

    confirmation = scenario.store.finish_registration_success.await_args.kwargs

    assert confirmation["registration_id"] == scenario.registration_id
    assert confirmation["operation_id"] == scenario.operation_id
    assert confirmation["provider_ticket_id"] == scenario.provider_ticket_id

    reservation = scenario.store.reserve_registration.await_args.kwargs

    assert reservation["request"] == scenario.request
    assert len(reservation["request_fingerprint"]) == 64

    scenario.store.record_registration_error.assert_not_awaited()

    scenario.seats.invalidate.assert_awaited_once_with(scenario.request.event_id)


@pytest.mark.parametrize(
    ("failure", "expected_exception"),
    [
        ("missing", EventNotFound),
        ("unpublished", RegistrationUnavailable),
        ("deadline", RegistrationClosed),
        ("event_passed", RegistrationClosed),
    ],
)
def test_registration_validation(
    failure: str,
    expected_exception: type[Exception],
) -> None:
    scenario = make_scenario()

    if failure == "missing":
        scenario.events.get_event.return_value = None

    elif failure == "unpublished":
        scenario.event.status = "new"

    elif failure == "deadline":
        scenario.event.registration_deadline = NOW

    elif failure == "event_passed":
        scenario.event.event_time = NOW

    with pytest.raises(expected_exception):
        asyncio.run(scenario.service.register(scenario.request))

    scenario.seats.get_available_seats.assert_not_awaited()
    scenario.store.reserve_registration.assert_not_awaited()
    scenario.provider.register.assert_not_awaited()


def test_unavailable_seat_is_rejected() -> None:
    scenario = make_scenario()

    scenario.seats.get_available_seats.return_value = ["A1", "A2"]

    with pytest.raises(SeatUnavailable):
        asyncio.run(scenario.service.register(scenario.request))

    scenario.store.reserve_registration.assert_not_awaited()
    scenario.provider.register.assert_not_awaited()


def test_seat_lookup_failure_does_not_reserve_ticket() -> None:
    scenario = make_scenario()

    request = httpx.Request(
        "GET",
        "http://provider.invalid/seats/",
    )

    scenario.seats.get_available_seats.side_effect = httpx.ReadTimeout(
        "Test timeout",
        request=request,
    )

    with pytest.raises(SeatLookupUnavailable):
        asyncio.run(scenario.service.register(scenario.request))

    scenario.store.reserve_registration.assert_not_awaited()
    scenario.provider.register.assert_not_awaited()


def test_local_seat_conflict_prevents_provider_call() -> None:
    scenario = make_scenario()

    scenario.store.reserve_registration.return_value = False

    with pytest.raises(SeatUnavailable):
        asyncio.run(scenario.service.register(scenario.request))

    scenario.store.mark_registration_in_flight.assert_not_awaited()
    scenario.provider.register.assert_not_awaited()


def test_registration_must_enter_in_flight_state() -> None:
    scenario = make_scenario()

    scenario.store.mark_registration_in_flight.return_value = False

    with pytest.raises(RegistrationStateError):
        asyncio.run(scenario.service.register(scenario.request))

    scenario.store.reserve_registration.assert_awaited_once()
    scenario.provider.register.assert_not_awaited()


@pytest.mark.parametrize(
    ("status_code", "expected_outcome", "expected_exception"),
    [
        (400, "failed", ProviderRegistrationRejected),
        (408, "unknown", ProviderResultUnknown),
        (500, "unknown", ProviderResultUnknown),
    ],
)
def test_provider_http_errors(
    status_code: int,
    expected_outcome: str,
    expected_exception: type[Exception],
) -> None:
    scenario = make_scenario()

    request = httpx.Request(
        "POST",
        "http://provider.invalid/register/",
    )

    response = httpx.Response(
        status_code,
        request=request,
    )

    scenario.provider.register.side_effect = httpx.HTTPStatusError(
        "Test provider error",
        request=request,
        response=response,
    )

    with pytest.raises(expected_exception):
        asyncio.run(scenario.service.register(scenario.request))

    scenario.store.record_registration_error.assert_awaited_once()

    error_record = scenario.store.record_registration_error.await_args.kwargs

    assert error_record["outcome"] == expected_outcome
    assert error_record["provider_http_status"] == status_code

    scenario.store.finish_registration_success.assert_not_awaited()

    scenario.seats.invalidate.assert_awaited_once_with(scenario.request.event_id)


def test_provider_timeout_keeps_reservation_uncertain() -> None:
    scenario = make_scenario()

    request = httpx.Request(
        "POST",
        "http://provider.invalid/register/",
    )

    scenario.provider.register.side_effect = httpx.ReadTimeout(
        "Test timeout",
        request=request,
    )

    with pytest.raises(ProviderResultUnknown):
        asyncio.run(scenario.service.register(scenario.request))

    error_record = scenario.store.record_registration_error.await_args.kwargs

    assert error_record["outcome"] == "unknown"
    assert error_record["provider_http_status"] is None

    scenario.store.finish_registration_success.assert_not_awaited()


def test_invalid_provider_response_is_uncertain() -> None:
    scenario = make_scenario()

    scenario.provider.register.side_effect = ValueError("Invalid registration response")

    with pytest.raises(ProviderResultUnknown):
        asyncio.run(scenario.service.register(scenario.request))

    error_record = scenario.store.record_registration_error.await_args.kwargs

    assert error_record["outcome"] == "unknown"


def test_failed_confirmation_does_not_return_ticket() -> None:
    scenario = make_scenario()

    scenario.store.finish_registration_success.return_value = False

    with pytest.raises(RegistrationStateError):
        asyncio.run(scenario.service.register(scenario.request))

    scenario.store.finish_registration_success.assert_awaited_once()

    scenario.seats.invalidate.assert_not_awaited()
