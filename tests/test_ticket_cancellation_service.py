import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.application.tickets.cancellation_service import (
    CancellationConflict,
    CancellationProviderRejected,
    CancellationResultUnknown,
    CancellationStateError,
    TicketCancellationService,
    TicketNotFound,
)
from app.application.tickets.state import TicketCancellationSnapshot

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def make_case():
    ticket_id = uuid4()
    event_id = uuid4()
    provider_ticket_id = uuid4()
    operation_id = uuid4()

    ticket = TicketCancellationSnapshot(
        ticket_id=ticket_id,
        event_id=event_id,
        provider_ticket_id=provider_ticket_id,
        status="active",
        event_time=NOW + timedelta(days=7),
    )

    store = SimpleNamespace(
        get_ticket_for_cancellation=AsyncMock(return_value=ticket),
        prepare_cancellation=AsyncMock(return_value=True),
        mark_cancellation_in_flight=AsyncMock(return_value=True),
        abort_prepared_cancellation=AsyncMock(return_value=True),
        finish_cancellation_success=AsyncMock(return_value=True),
        record_cancellation_error=AsyncMock(return_value=True),
    )

    provider = SimpleNamespace(unregister=AsyncMock())

    seats = SimpleNamespace(invalidate=AsyncMock())

    service = TicketCancellationService(
        store=store,
        provider=provider,
        seats=seats,
        clock=lambda: NOW,
        new_id=lambda: operation_id,
    )

    return SimpleNamespace(
        service=service,
        ticket=ticket,
        ticket_id=ticket_id,
        operation_id=operation_id,
        store=store,
        provider=provider,
        seats=seats,
    )


def provider_http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request(
        "DELETE",
        "https://provider.test/unregister/",
    )

    response = httpx.Response(
        status,
        request=request,
    )

    return httpx.HTTPStatusError(
        "Provider error",
        request=request,
        response=response,
    )


def test_successful_cancellation() -> None:
    case = make_case()

    asyncio.run(case.service.cancel(case.ticket_id))

    case.provider.unregister.assert_awaited_once_with(
        event_id=case.ticket.event_id,
        ticket_id=case.ticket.provider_ticket_id,
    )

    case.store.finish_cancellation_success.assert_awaited_once()
    case.seats.invalidate.assert_awaited_once_with(case.ticket.event_id)


def test_already_cancelled_ticket_is_idempotent() -> None:
    case = make_case()

    case.store.get_ticket_for_cancellation.return_value = TicketCancellationSnapshot(
        ticket_id=case.ticket_id,
        event_id=case.ticket.event_id,
        provider_ticket_id=case.ticket.provider_ticket_id,
        status="cancelled",
        event_time=case.ticket.event_time,
    )

    asyncio.run(case.service.cancel(case.ticket_id))

    case.provider.unregister.assert_not_awaited()
    case.store.prepare_cancellation.assert_not_awaited()


def test_missing_ticket() -> None:
    case = make_case()

    case.store.get_ticket_for_cancellation.return_value = None

    with pytest.raises(TicketNotFound):
        asyncio.run(case.service.cancel(case.ticket_id))

    case.provider.unregister.assert_not_awaited()


def test_existing_cancellation_blocks_duplicate() -> None:
    case = make_case()

    case.store.prepare_cancellation.return_value = False

    with pytest.raises(CancellationConflict):
        asyncio.run(case.service.cancel(case.ticket_id))

    case.provider.unregister.assert_not_awaited()


def test_cancellation_cannot_start_and_is_aborted() -> None:
    case = make_case()

    case.store.mark_cancellation_in_flight.return_value = False

    with pytest.raises(CancellationConflict):
        asyncio.run(case.service.cancel(case.ticket_id))

    case.store.abort_prepared_cancellation.assert_awaited_once()
    case.provider.unregister.assert_not_awaited()


def test_provider_rejection_is_recorded() -> None:
    case = make_case()

    case.provider.unregister.side_effect = provider_http_error(400)

    with pytest.raises(CancellationProviderRejected):
        asyncio.run(case.service.cancel(case.ticket_id))

    assert case.store.record_cancellation_error.await_args.kwargs["outcome"] == "failed"

    case.store.finish_cancellation_success.assert_not_awaited()


def test_provider_timeout_has_unknown_result() -> None:
    case = make_case()

    case.provider.unregister.side_effect = httpx.ReadTimeout("Read timed out")

    with pytest.raises(CancellationResultUnknown):
        asyncio.run(case.service.cancel(case.ticket_id))

    assert (
        case.store.record_cancellation_error.await_args.kwargs["outcome"] == "unknown"
    )

    case.seats.invalidate.assert_not_awaited()


def test_provider_http_500_has_unknown_result() -> None:
    case = make_case()

    case.provider.unregister.side_effect = provider_http_error(500)

    with pytest.raises(CancellationResultUnknown):
        asyncio.run(case.service.cancel(case.ticket_id))

    assert (
        case.store.record_cancellation_error.await_args.kwargs["outcome"] == "unknown"
    )


def test_confirmation_failure_is_reported() -> None:
    case = make_case()

    case.store.finish_cancellation_success.return_value = False

    with pytest.raises(CancellationStateError):
        asyncio.run(case.service.cancel(case.ticket_id))

    case.provider.unregister.assert_awaited_once()
    case.seats.invalidate.assert_not_awaited()
