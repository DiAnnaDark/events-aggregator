import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.infrastructure.db.ticket_repository import (
    PostgresTicketRepository,
    TicketStateConflict,
)
from app.infrastructure.db.ticket_store import PostgresTicketStore

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
STALE_BEFORE = NOW - timedelta(minutes=30)


def make_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def test_expire_prepared_registration() -> None:
    async def scenario() -> None:
        registration_id = uuid4()

        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                make_result(registration_id),
                make_result(registration_id),
            ]
        )

        repository = PostgresTicketRepository(session)

        expired = await repository.expire_prepared_registration(
            operation_id=uuid4(),
            stale_before=STALE_BEFORE,
            now=NOW,
        )

        assert expired is True
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

        assert operation_values["status"] == "failed"
        assert operation_values["error_code"] == "stale_prepared"
        assert operation_values["finished_at"] == NOW

        assert "prepared" in operation_values.values()
        assert STALE_BEFORE in operation_values.values()

        assert ticket_values["status"] == "failed"

    asyncio.run(scenario())


def test_ineligible_operation_is_not_expired() -> None:
    async def scenario() -> None:
        session = MagicMock()
        session.execute = AsyncMock(return_value=make_result(None))

        repository = PostgresTicketRepository(session)

        expired = await repository.expire_prepared_registration(
            operation_id=uuid4(),
            stale_before=STALE_BEFORE,
            now=NOW,
        )

        assert expired is False

        session.execute.assert_awaited_once()

    asyncio.run(scenario())


def test_recovery_detects_ticket_state_conflict() -> None:
    async def scenario() -> None:
        registration_id = uuid4()

        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                make_result(registration_id),
                make_result(None),
            ]
        )

        repository = PostgresTicketRepository(session)

        with pytest.raises(TicketStateConflict):
            await repository.expire_prepared_registration(
                operation_id=uuid4(),
                stale_before=STALE_BEFORE,
                now=NOW,
            )

        assert session.execute.await_count == 2

    asyncio.run(scenario())


def test_store_expires_prepared_in_transaction() -> None:
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
            repository.expire_prepared_registration = AsyncMock(return_value=True)

            store = PostgresTicketStore(session_factory)

            expired = await store.expire_prepared_registration(
                operation_id=uuid4(),
                stale_before=STALE_BEFORE,
                now=NOW,
            )

            assert expired is True

            repository.expire_prepared_registration.assert_awaited_once()

            transaction.__aenter__.assert_awaited_once()
            transaction.__aexit__.assert_awaited_once_with(
                None,
                None,
                None,
            )

    asyncio.run(scenario())
