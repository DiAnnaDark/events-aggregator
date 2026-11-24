from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.api.tickets import get_ticket_registration_service


def test_ticket_registration_dependency_passes_configured_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = object()

    state = SimpleNamespace(
        db_session_factory=session_factory,
        seats_service=object(),
        registration_provider=object(),
        ticket_idempotency_ttl_hours=48,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(state=state),
    )

    store_constructor = Mock(return_value=object())

    monkeypatch.setattr(
        "app.api.tickets.PostgresTicketStore",
        store_constructor,
    )

    get_ticket_registration_service(request)

    store_constructor.assert_called_once_with(
        session_factory,
        idempotency_ttl_hours=48,
    )
