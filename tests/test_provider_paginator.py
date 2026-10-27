import asyncio
from datetime import date
from unittest.mock import AsyncMock, Mock, call

import pytest

from app.infrastructure.provider.client import EventsProviderClient
from app.infrastructure.provider.paginator import EventsPaginator


def test_paginator_fetches_all_pages() -> None:
    client = Mock(spec=EventsProviderClient)

    client.events = AsyncMock(
        side_effect=[
            {
                "next": "http://provider.test/api/events/?cursor=abc",
                "results": [{"id": "event-1"}],
            },
            {
                "next": None,
                "results": [{"id": "event-2"}],
            },
        ]
    )

    async def collect_events() -> list[dict]:
        paginator = EventsPaginator(
            client,
            changed_at=date(2000, 1, 1),
        )

        return [event async for event in paginator]

    events = asyncio.run(collect_events())

    assert events == [
        {"id": "event-1"},
        {"id": "event-2"},
    ]

    client.events.assert_has_awaits(
        [
            call(changed_at=date(2000, 1, 1), cursor=None),
            call(changed_at=date(2000, 1, 1), cursor="abc"),
        ]
    )

    assert client.events.await_count == 2


def test_paginator_rejects_repeated_cursor() -> None:
    client = Mock(spec=EventsProviderClient)

    client.events = AsyncMock(
        side_effect=[
            {
                "next": "http://provider.test/api/events/?cursor=abc",
                "results": [],
            },
            {
                "next": "http://provider.test/api/events/?cursor=abc",
                "results": [],
            },
        ]
    )

    async def collect_events() -> list[dict]:
        paginator = EventsPaginator(
            client,
            changed_at=date(2000, 1, 1),
        )

        return [event async for event in paginator]

    with pytest.raises(ValueError, match="Repeated pagination cursor"):
        asyncio.run(collect_events())

    assert client.events.await_count == 2


def test_paginator_rejects_missing_cursor() -> None:
    client = Mock(spec=EventsProviderClient)

    client.events = AsyncMock(
        return_value={
            "next": "http://provider.test/api/events/?page=2",
            "results": [],
        }
    )

    async def collect_events() -> list[dict]:
        paginator = EventsPaginator(
            client,
            changed_at=date(2000, 1, 1),
        )

        return [event async for event in paginator]

    with pytest.raises(ValueError, match="Invalid pagination cursor"):
        asyncio.run(collect_events())
