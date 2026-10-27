import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.application.sync.service import (
    SyncAlreadyRunning,
    SyncLeaseLost,
    SyncService,
)
from app.application.sync.state import SyncLease

NOW = datetime(2026, 10, 27, 16, 0, tzinfo=UTC)


def make_event(changed_at: datetime) -> dict:
    timestamp = changed_at.isoformat()

    return {
        "id": str(uuid4()),
        "name": "Test event",
        "event_time": timestamp,
        "registration_deadline": timestamp,
        "status": "published",
        "number_of_visitors": 10,
        "changed_at": timestamp,
        "created_at": timestamp,
        "status_changed_at": timestamp,
        "place": {
            "id": str(uuid4()),
            "name": "Test place",
            "city": "Moscow",
            "address": "Test address",
            "seats_pattern": "A1-1000",
            "created_at": timestamp,
            "changed_at": timestamp,
        },
    }


class FakeSource:
    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.requested_date: date | None = None

    async def events_since(
        self,
        changed_at: date,
    ) -> AsyncIterator[dict]:
        self.requested_date = changed_at

        for event in self.events:
            yield event


def make_store(lease: SyncLease | None) -> SimpleNamespace:
    return SimpleNamespace(
        try_start=AsyncMock(return_value=lease),
        save_batch=AsyncMock(return_value=True),
        finish_success=AsyncMock(return_value=True),
        finish_failure=AsyncMock(return_value=True),
    )


def test_initial_sync_processes_batches() -> None:
    first = NOW - timedelta(hours=3)
    newest = NOW - timedelta(hours=1)
    middle = NOW - timedelta(hours=2)

    source = FakeSource(
        [
            make_event(first),
            make_event(newest),
            make_event(middle),
        ]
    )

    store = make_store(SyncLease(started_at=NOW, last_changed_at=None))

    service = SyncService(
        source=source,
        store=store,
        clock=lambda: NOW,
        batch_size=2,
    )

    result = asyncio.run(service.run())

    assert source.requested_date == date(2000, 1, 1)
    assert result.processed == 3
    assert result.last_changed_at == newest

    assert store.save_batch.await_count == 2

    batch_sizes = [
        len(call.kwargs["events"]) for call in store.save_batch.await_args_list
    ]

    assert batch_sizes == [2, 1]

    assert store.finish_success.await_args.kwargs["newest_changed_at"] == newest

    store.finish_failure.assert_not_awaited()


def test_empty_sync_preserves_checkpoint() -> None:
    checkpoint = NOW - timedelta(days=1)

    source = FakeSource([])
    store = make_store(
        SyncLease(
            started_at=NOW,
            last_changed_at=checkpoint,
        )
    )

    service = SyncService(
        source=source,
        store=store,
        clock=lambda: NOW,
    )

    result = asyncio.run(service.run())

    assert source.requested_date == checkpoint.date()
    assert result.processed == 0
    assert result.last_changed_at == checkpoint

    store.save_batch.assert_not_awaited()

    assert store.finish_success.await_args.kwargs["newest_changed_at"] == checkpoint


def test_concurrent_sync_is_rejected() -> None:
    source = FakeSource([])
    store = make_store(None)

    service = SyncService(
        source=source,
        store=store,
        clock=lambda: NOW,
    )

    with pytest.raises(SyncAlreadyRunning):
        asyncio.run(service.run())

    assert source.requested_date is None

    store.save_batch.assert_not_awaited()
    store.finish_success.assert_not_awaited()


def test_invalid_event_marks_sync_failed() -> None:
    source = FakeSource([{"invalid": True}])

    store = make_store(SyncLease(started_at=NOW, last_changed_at=None))

    service = SyncService(
        source=source,
        store=store,
        clock=lambda: NOW,
    )

    with pytest.raises(ValidationError):
        asyncio.run(service.run())

    store.finish_failure.assert_awaited_once()
    store.finish_success.assert_not_awaited()

    assert "ValidationError" in (store.finish_failure.await_args.kwargs["error"])


def test_lost_lease_stops_sync() -> None:
    source = FakeSource([make_event(NOW)])

    store = make_store(SyncLease(started_at=NOW, last_changed_at=None))

    store.save_batch.return_value = False

    service = SyncService(
        source=source,
        store=store,
        clock=lambda: NOW,
        batch_size=1,
    )

    with pytest.raises(SyncLeaseLost):
        asyncio.run(service.run())

    store.finish_success.assert_not_awaited()
    store.finish_failure.assert_awaited_once()
