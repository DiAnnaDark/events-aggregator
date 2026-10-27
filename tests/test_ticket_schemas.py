from uuid import UUID

import pytest
from pydantic import ValidationError

from app.application.tickets.schemas import (
    TicketCancelled,
    TicketCreate,
    TicketCreated,
)

EVENT_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
TICKET_ID = UUID("650e8400-e29b-41d4-a716-446655440001")


def valid_payload() -> dict[str, object]:
    return {
        "event_id": str(EVENT_ID),
        "first_name": "Анна",
        "last_name": "Иванова",
        "email": "anna@example.com",
        "seat": "A15",
    }


def test_valid_ticket_request() -> None:
    ticket = TicketCreate.model_validate(valid_payload())

    assert ticket.event_id == EVENT_ID
    assert ticket.first_name == "Анна"
    assert ticket.last_name == "Иванова"
    assert ticket.email == "anna@example.com"
    assert ticket.seat == "A15"


def test_names_are_stripped() -> None:
    data = valid_payload()

    data["first_name"] = "  Анна  "
    data["last_name"] = "  Иванова  "

    ticket = TicketCreate.model_validate(data)

    assert ticket.first_name == "Анна"
    assert ticket.last_name == "Иванова"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("event_id", "invalid-uuid"),
        ("first_name", "   "),
        ("last_name", ""),
        ("email", "not-an-email"),
        ("seat", "A0"),
        ("seat", "AA1"),
        ("seat", "A-1"),
        ("seat", "123"),
    ],
)
def test_invalid_ticket_request(
    field: str,
    bad_value: object,
) -> None:
    data = valid_payload()
    data[field] = bad_value

    with pytest.raises(ValidationError):
        TicketCreate.model_validate(data)


@pytest.mark.parametrize(
    "missing_field",
    [
        "event_id",
        "first_name",
        "last_name",
        "email",
        "seat",
    ],
)
def test_required_fields(
    missing_field: str,
) -> None:
    data = valid_payload()
    data.pop(missing_field)

    with pytest.raises(ValidationError):
        TicketCreate.model_validate(data)


def test_ticket_created_response() -> None:
    response = TicketCreated(ticket_id=TICKET_ID)

    assert response.model_dump(mode="json") == {
        "ticket_id": str(TICKET_ID),
    }


def test_ticket_cancelled_response() -> None:
    response = TicketCancelled(success=True)

    assert response.model_dump() == {
        "success": True,
    }
