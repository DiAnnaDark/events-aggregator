import asyncio
from datetime import date
from unittest.mock import AsyncMock, call

from app.infrastructure.provider.source import ProviderEventsSource


def test_source_collects_events_from_all_pages() -> None:
    provider = AsyncMock()

    provider.events.side_effect = [
        {
            "results": [{"id": "first"}],
            "next": ("https://provider.example/api/events/?cursor=next-page"),
        },
        {
            "results": [{"id": "second"}],
            "next": None,
        },
    ]

    source = ProviderEventsSource(provider)

    changed_at = date(2026, 10, 27)

    async def collect_events() -> list[dict]:
        return [event async for event in source.events_since(changed_at)]

    events = asyncio.run(collect_events())

    assert events == [
        {"id": "first"},
        {"id": "second"},
    ]

    assert provider.events.await_args_list == [
        call(
            changed_at=changed_at,
            cursor=None,
        ),
        call(
            changed_at=changed_at,
            cursor="next-page",
        ),
    ]
