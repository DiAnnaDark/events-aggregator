import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.application.sync.schemas import EventSnapshot
from app.application.sync.state import SyncLease
from app.infrastructure.db.sync_store import PostgresSyncStore

NOW = datetime(2026, 10, 27, 16, 0, tzinfo=UTC)
LEASE = SyncLease(started_at=NOW, last_changed_at=None)


def make_factory() -> MagicMock:
    factory = MagicMock()
    factory.begin.return_value.__aenter__.return_value = MagicMock()
    return factory


def test_save_batch_success() -> None:
    factory = make_factory()
    store = PostgresSyncStore(factory)

    events = [
        EventSnapshot.model_construct(id=uuid4()),
        EventSnapshot.model_construct(id=uuid4()),
    ]

    state_repository = MagicMock()
    state_repository.heartbeat = AsyncMock(return_value=True)

    event_repository = MagicMock()
    event_repository.upsert = AsyncMock()

    with (
        patch(
            "app.infrastructure.db.sync_store.PostgresSyncStateRepository",
            return_value=state_repository,
        ),
        patch(
            "app.infrastructure.db.sync_store.PostgresEventRepository",
            return_value=event_repository,
        ),
    ):
        result = asyncio.run(
            store.save_batch(
                lease=LEASE,
                events=events,
                now=NOW,
            )
        )

    assert result is True

    state_repository.heartbeat.assert_awaited_once_with(
        lease=LEASE,
        now=NOW,
    )

    assert event_repository.upsert.await_count == 2

    saved_events = [call.args[0] for call in event_repository.upsert.await_args_list]

    assert saved_events == events
    factory.begin.assert_called_once()


def test_lost_lease_prevents_writes() -> None:
    factory = make_factory()
    store = PostgresSyncStore(factory)

    state_repository = MagicMock()
    state_repository.heartbeat = AsyncMock(return_value=False)

    event_repository = MagicMock()
    event_repository.upsert = AsyncMock()

    with (
        patch(
            "app.infrastructure.db.sync_store.PostgresSyncStateRepository",
            return_value=state_repository,
        ),
        patch(
            "app.infrastructure.db.sync_store.PostgresEventRepository",
            return_value=event_repository,
        ),
    ):
        result = asyncio.run(
            store.save_batch(
                lease=LEASE,
                events=[EventSnapshot.model_construct(id=uuid4())],
                now=NOW,
            )
        )

    assert result is False
    event_repository.upsert.assert_not_awaited()


def test_database_error_propagates() -> None:
    factory = make_factory()
    store = PostgresSyncStore(factory)

    state_repository = MagicMock()
    state_repository.heartbeat = AsyncMock(return_value=True)

    event_repository = MagicMock()
    event_repository.upsert = AsyncMock(side_effect=RuntimeError("Database failure"))

    with (
        patch(
            "app.infrastructure.db.sync_store.PostgresSyncStateRepository",
            return_value=state_repository,
        ),
        patch(
            "app.infrastructure.db.sync_store.PostgresEventRepository",
            return_value=event_repository,
        ),
        pytest.raises(RuntimeError, match="Database failure"),
    ):
        asyncio.run(
            store.save_batch(
                lease=LEASE,
                events=[EventSnapshot.model_construct(id=uuid4())],
                now=NOW,
            )
        )

    exit_args = factory.begin.return_value.__aexit__.await_args.args

    assert exit_args[0] is RuntimeError
