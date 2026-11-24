from uuid import uuid4

from app.application.tickets.schemas import TicketCreate


def test_ticket_create_accepts_idempotency_key() -> None:
    request = TicketCreate(
        event_id=uuid4(),
        first_name="Anna",
        last_name="Test",
        email="anna@example.com",
        seat="A15",
        idempotency_key="purchase-123",
    )

    assert request.idempotency_key == "purchase-123"


def test_ticket_create_without_idempotency_key() -> None:
    request = TicketCreate(
        event_id=uuid4(),
        first_name="Anna",
        last_name="Test",
        email="anna@example.com",
        seat="A15",
    )

    assert request.idempotency_key is None
