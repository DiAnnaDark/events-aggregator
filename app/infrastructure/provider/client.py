from datetime import date
from typing import Any
from uuid import UUID

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

    async def seats(
        self,
        *,
        event_id: UUID,
    ) -> list[str]:
        response = await self._client.get(
            f"{self._base_url}/api/events/{event_id}/seats/",
            headers={"x-api-key": self._api_key},
            timeout=self._timeout,
        )

        response.raise_for_status()

        data = response.json()

        if (
            not isinstance(data, dict)
            or not isinstance(data.get("seats"), list)
            or not all(isinstance(seat, str) for seat in data["seats"])
        ):
            raise ValueError("Invalid Events Provider seats response")

        return data["seats"]

    async def register(
        self,
        *,
        event_id: UUID,
        first_name: str,
        last_name: str,
        email: str,
        seat: str,
    ) -> UUID:
        response = await self._client.post(
            f"{self._base_url}/api/events/{event_id}/register/",
            json={
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "seat": seat,
            },
            headers={"x-api-key": self._api_key},
            timeout=self._timeout,
        )

        response.raise_for_status()

        data = response.json()

        ticket_id = data.get("ticket_id") if isinstance(data, dict) else None

        if not isinstance(ticket_id, str):
            raise TypeError("Invalid Events Provider registration response")

        try:
            return UUID(ticket_id)
        except ValueError as error:
            raise ValueError("Invalid Events Provider registration response") from error

    async def unregister(
        self,
        *,
        event_id: UUID,
        ticket_id: UUID,
    ) -> None:
        response = await self._client.request(
            "DELETE",
            f"{self._base_url}/api/events/{event_id}/unregister/",
            json={"ticket_id": str(ticket_id)},
            headers={"x-api-key": self._api_key},
            timeout=self._timeout,
        )

        response.raise_for_status()

        data = response.json()

        if not isinstance(data, dict) or data.get("success") is not True:
            raise ValueError("Invalid Events Provider cancellation response")
