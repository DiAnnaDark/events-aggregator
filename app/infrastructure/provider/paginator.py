from collections.abc import AsyncIterator
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.infrastructure.provider.client import EventsProviderClient


class EventsPaginator:
    def __init__(
        self,
        client: EventsProviderClient,
        changed_at: date,
    ) -> None:
        self._client = client
        self._changed_at = changed_at

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        cursor: str | None = None
        seen_cursors: set[str] = set()

        while True:
            page = await self._client.events(
                changed_at=self._changed_at,
                cursor=cursor,
            )

            for event in page["results"]:
                if not isinstance(event, dict):
                    raise TypeError("Invalid event in provider response")

                yield event

            next_url = page["next"]

            if next_url is None:
                return

            query = parse_qs(urlsplit(next_url).query)
            cursors = query.get("cursor", [])

            if len(cursors) != 1 or not cursors[0]:
                raise ValueError("Invalid pagination cursor")

            cursor = cursors[0]

            if cursor in seen_cursors:
                raise ValueError("Repeated pagination cursor")

            seen_cursors.add(cursor)
