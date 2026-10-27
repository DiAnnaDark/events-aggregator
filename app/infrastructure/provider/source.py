from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from app.infrastructure.provider.client import EventsProviderClient
from app.infrastructure.provider.paginator import EventsPaginator


class ProviderEventsSource:
    def __init__(
        self,
        client: EventsProviderClient,
    ) -> None:
        self._client = client

    async def events_since(
        self,
        changed_at: date,
    ) -> AsyncIterator[dict[str, Any]]:
        paginator = EventsPaginator(
            self._client,
            changed_at,
        )

        async for event in paginator:
            yield event
