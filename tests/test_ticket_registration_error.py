import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.infrastructure.db.ticket_repository import (
    PostgresTicketRepository,
    TicketStateConflict,
)
from app.infrastructure.db.ticket_store import PostgresTicketStore


def make_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.parametrize(
    ("outcome", "ticket_status"),
    [
        ("failed", "failed"),
        ("unknown", "uncertain"),
    ],
)
def test_repository_records_registration_error(
    outcome: str,
    ticket_status: str,
) -> None:
    async def scenario() -> None:
        operation_id = uuid4()
        registration_id = uuid4()
        now = datetime.now(UTC)

        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                make_result(operation_id),
                make_result(registration_id),
            ]
        )

        repository = PostgresTicketRepository(session)

        updated = await repository.record_registration_error(
            registration_id=registration_id,
            operation_id=operation_id,
            outcome=outcome,
            provider_http_status=502,
            error_code="provider_error",
            now=now,
        )

        assert updated is True
        assert session.execute.await_count == 2

        statements = [call.args[0] for call in session.execute.await_args_list]

        operation_values = (
            statements[0]
            .compile(
                dialect=postgresql.dialect(),
            )
            .params
        )

        ticket_values = (
            statements[1]
            .compile(
                dialect=postgresql.dialect(),
            )
            .params
        )

        assert operation_values["status"] == outcome
        assert operation_values["provider_http_status"] == 502
        assert operation_values["error_code"] == "provider_error"

        expected_finished_at = now if outcome == "failed" else None

        assert operation_values["finished_at"] == expected_finished_at
        assert ticket_values["status"] == ticket_status

    asyncio.run(scenario())


def test_repository_rejects_stale_error_transition() -> None:
    async def scenario() -> None:
        session = MagicMock()
        session.execute = AsyncMock(return_value=make_result(None))

        repository = PostgresTicketRepository(session)

        updated = await repository.record_registration_error(
            registration_id=uuid4(),
            operation_id=uuid4(),
            outcome="unknown",
            provider_http_status=None,
            error_code="timeout",
            now=datetime.now(UTC),
        )

        assert updated is False
        session.execute.assert_awaited_once()

    asyncio.run(scenario())


def test_repository_detects_ticket_state_conflict() -> None:
    async def scenario() -> None:
        operation_id = uuid4()

        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                make_result(operation_id),
                make_result(None),
            ]
        )

        repository = PostgresTicketRepository(session)

        with pytest.raises(TicketStateConflict):
            await repository.record_registration_error(
                registration_id=uuid4(),
                operation_id=operation_id,
                outcome="unknown",
                provider_http_status=None,
                error_code="timeout",
                now=datetime.now(UTC),
            )

        assert session.execute.await_count == 2

    asyncio.run(scenario())


def test_repository_rejects_invalid_outcome() -> None:
    async def scenario() -> None:
        session = MagicMock()
        session.execute = AsyncMock()

        repository = PostgresTicketRepository(session)

        with pytest.raises(
            ValueError,
            match="Unsupported registration outcome",
        ):
            await repository.record_registration_error(
                registration_id=uuid4(),
                operation_id=uuid4(),
                outcome="invalid",
                provider_http_status=None,
                error_code="test",
                now=datetime.now(UTC),
            )

        session.execute.assert_not_awaited()

    asyncio.run(scenario())


def test_store_records_error_in_transaction() -> None:
    async def scenario() -> None:
        session = MagicMock()

        transaction = MagicMock()
        transaction.__aenter__ = AsyncMock(return_value=session)
        transaction.__aexit__ = AsyncMock(return_value=False)

        session_factory = MagicMock()
        session_factory.begin.return_value = transaction

        with patch(
            "app.infrastructure.db.ticket_store.PostgresTicketRepository"
        ) as repository_class:
            repository = repository_class.return_value
            repository.record_registration_error = AsyncMock(return_value=True)

            store = PostgresTicketStore(session_factory)

            updated = await store.record_registration_error(
                registration_id=uuid4(),
                operation_id=uuid4(),
                outcome="unknown",
                provider_http_status=None,
                error_code="timeout",
                now=datetime.now(UTC),
            )

            assert updated is True

            session_factory.begin.assert_called_once()
            repository_class.assert_called_once_with(session)

            repository.record_registration_error.assert_awaited_once()

            transaction.__aenter__.assert_awaited_once()
            transaction.__aexit__.assert_awaited_once_with(
                None,
                None,
                None,
            )

    asyncio.run(scenario())
