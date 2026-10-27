from datetime import date
from typing import Any

import httpx


class EventsProviderClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient,
        timeout: float = 10.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Events Provider API key is required")

        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client
        self._timeout = timeout

    async def events(
        self,
        *,
        changed_at: date,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        params = {"changed_at": changed_at.isoformat()}

        if cursor is not None:
            params["cursor"] = cursor

        response = await self._client.get(
            f"{self._base_url}/api/events/",
            params=params,
            headers={"x-api-key": self._api_key},
            timeout=self._timeout,
        )

        response.raise_for_status()

        page = response.json()

        if (
            not isinstance(page, dict)
            or "next" not in page
            or not isinstance(page["next"], (str, type(None)))
            or not isinstance(page.get("results"), list)
        ):
            raise ValueError("Invalid Events Provider response")

        return page
