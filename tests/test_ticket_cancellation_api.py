from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.tickets import get_ticket_cancellation_service
from app.application.tickets.cancellation_service import (
    CancellationClosed,
    CancellationConflict,
    CancellationProviderRejected,
    CancellationResultUnknown,
    CancellationStateError,
    CancellationUnavailable,
    TicketNotFound,
)
from app.main import create_app


def make_application(service: SimpleNamespace):
    application = create_app(enable_sync=False)

    application.dependency_overrides[get_ticket_cancellation_service] = lambda: service

    return application


def test_successful_cancellation_returns_200() -> None:
    ticket_id = uuid4()

    service = SimpleNamespace(
        cancel=AsyncMock(return_value=None),
    )

    application = make_application(service)

    with TestClient(application) as client:
        response = client.delete(f"/api/tickets/{ticket_id}")

    assert response.status_code == 200
    assert response.json() == {"success": True}

    service.cancel.assert_awaited_once_with(ticket_id)


def test_cancellation_without_runtime_resources_returns_503() -> None:
    application = create_app(enable_sync=False)

    with TestClient(application) as client:
        response = client.delete(f"/api/tickets/{uuid4()}")

    assert response.status_code == 503
    assert response.json() == {"detail": "Cancellation service is unavailable"}


def test_invalid_ticket_id_returns_422() -> None:
    service = SimpleNamespace(
        cancel=AsyncMock(),
    )

    application = make_application(service)

    with TestClient(application) as client:
        response = client.delete("/api/tickets/not-a-uuid")

    assert response.status_code == 422

    service.cancel.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (TicketNotFound("Ticket not found"), 404),
        (CancellationUnavailable("Unavailable"), 409),
        (CancellationClosed("Event already started"), 409),
        (CancellationConflict("Already in progress"), 409),
        (CancellationProviderRejected("Rejected"), 409),
        (CancellationResultUnknown("Unknown result"), 503),
        (CancellationStateError("Database state error"), 503),
    ],
)
def test_cancellation_errors_have_expected_http_status(
    error: Exception,
    expected_status: int,
) -> None:
    service = SimpleNamespace(
        cancel=AsyncMock(side_effect=error),
    )

    application = make_application(service)

    with TestClient(application) as client:
        response = client.delete(f"/api/tickets/{uuid4()}")

    assert response.status_code == expected_status

    service.cancel.assert_awaited_once()
