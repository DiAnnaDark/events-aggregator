import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.sync.state import SyncLease
from app.infrastructure.db.sync_state_repository import (
    PostgresSyncStateRepository,
)


def test_try_start_returns_lease() -> None:
    now = datetime(2026, 10, 27, tzinfo=UTC)

    result = Mock()
    result.one_or_none.return_value = (now, None)

    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result

    repository = PostgresSyncStateRepository(session)

    lease = asyncio.run(
        repository.try_start(
            now=now,
            stale_before=now - timedelta(minutes=5),
        )
    )

    assert lease == SyncLease(
        started_at=now,
        last_changed_at=None,
    )

    session.commit.assert_not_awaited()

    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "UPDATE sync_state SET" in sql
    assert "sync_state.updated_at <" in sql
    assert "RETURNING sync_state.started_at" in sql


def test_try_start_rejects_concurrent_run() -> None:
    now = datetime(2026, 10, 27, tzinfo=UTC)

    result = Mock()
    result.one_or_none.return_value = None

    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result

    repository = PostgresSyncStateRepository(session)

    lease = asyncio.run(
        repository.try_start(
            now=now,
            stale_before=now - timedelta(minutes=5),
        )
    )

    assert lease is None


def test_success_preserves_watermark_when_no_events() -> None:
    now = datetime(2026, 10, 27, tzinfo=UTC)
    lease = SyncLease(started_at=now, last_changed_at=None)

    result = Mock()
    result.scalar_one_or_none.return_value = 1

    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result

    repository = PostgresSyncStateRepository(session)

    success = asyncio.run(
        repository.finish_success(
            lease=lease,
            now=now + timedelta(minutes=1),
            newest_changed_at=None,
        )
    )

    assert success is True

    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))

    update_clause = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]

    assert "last_changed_at =" not in update_clause
    assert "sync_state.started_at =" in sql

    session.commit.assert_not_awaited()
