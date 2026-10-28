from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tickets import get_ticket_registration_service
from app.application.tickets.registration_service import (
    EventNotFound,
    ProviderRegistrationRejected,
    ProviderResultUnknown,
    RegistrationClosed,
    RegistrationStateError,
    RegistrationUnavailable,
    SeatLookupUnavailable,
    SeatUnavailable,
)
from app.application.tickets.schemas import TicketCreate
from app.main import create_app


def ticket_body() -> dict[str, str]:
    return {
        "event_id": str(uuid4()),
        "first_name": "Anna",
        "last_name": "Test",
        "email": "anna@example.com",
        "seat": "A15",
    }


def make_application(service: SimpleNamespace) -> FastAPI:
    application = create_app(enable_sync=False)

    application.dependency_overrides[get_ticket_registration_service] = lambda: service

    return application


def test_ticket_created() -> None:
    internal_ticket_id = uuid4()

    service = SimpleNamespace(register=AsyncMock(return_value=internal_ticket_id))

    application = make_application(service)

    with TestClient(application) as client:
        response = client.post(
            "/api/tickets",
            json=ticket_body(),
        )

    assert response.status_code == 201
    assert response.json() == {"ticket_id": str(internal_ticket_id)}

    service.register.assert_awaited_once()

    request = service.register.await_args.args[0]

    assert isinstance(request, TicketCreate)
    assert request.seat == "A15"
    assert request.email == "anna@example.com"


def test_ticket_api_unavailable_without_runtime_resources() -> None:
    application = create_app(enable_sync=False)

    with TestClient(application) as client:
        response = client.post(
            "/api/tickets",
            json=ticket_body(),
        )

    assert response.status_code == 503

    assert response.json() == {"detail": "Registration service is unavailable"}


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (EventNotFound("Event not found"), 404),
        (RegistrationUnavailable("Not published"), 409),
        (RegistrationClosed("Registration closed"), 409),
        (SeatUnavailable("Seat unavailable"), 409),
        (ProviderRegistrationRejected("Rejected"), 409),
        (SeatLookupUnavailable("Provider unavailable"), 503),
        (ProviderResultUnknown("Unknown result"), 503),
        (RegistrationStateError("Database state error"), 503),
    ],
)
def test_registration_errors_have_expected_http_status(
    error: Exception,
    expected_status: int,
) -> None:
    service = SimpleNamespace(register=AsyncMock(side_effect=error))

    application = make_application(service)

    with TestClient(application) as client:
        response = client.post(
            "/api/tickets",
            json=ticket_body(),
        )

    assert response.status_code == expected_status

    service.register.assert_awaited_once()


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("seat", "A0"),
        ("email", "not-an-email"),
        ("first_name", "   "),
        ("event_id", "invalid-uuid"),
    ],
)
def test_invalid_ticket_data_is_rejected(
    field: str,
    invalid_value: str,
) -> None:
    service = SimpleNamespace(register=AsyncMock(return_value=uuid4()))

    application = make_application(service)

    body = ticket_body()
    body[field] = invalid_value

    with TestClient(application) as client:
        response = client.post(
            "/api/tickets",
            json=body,
        )

    assert response.status_code == 400

    service.register.assert_not_awaited()


def test_invalid_registration_matches_lms_contract() -> None:
    service = SimpleNamespace(register=AsyncMock(return_value=uuid4()))

    application = make_application(service)

    body = ticket_body()
    body["event_id"] = "not-a-uuid"
    body["email"] = "x"

    with TestClient(application) as client:
        response = client.post(
            "/api/tickets",
            json=body,
        )

    assert response.status_code == 400

    invalid_fields = {error["loc"][-1] for error in response.json()["detail"]}

    assert {"event_id", "email"} <= invalid_fields

    service.register.assert_not_awaited()
