import httpx


class CapashinoClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient,
        timeout: float = 10.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Capashino API key is required")

        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client
        self._timeout = timeout

    async def send(
        self,
        *,
        message: str,
        reference_id: str,
        idempotency_key: str,
    ) -> None:
        if not message.strip():
            raise ValueError("Notification message is required")

        if not reference_id.strip() or not idempotency_key.strip():
            raise ValueError("Notification identifiers are required")

        response = await self._client.post(
            f"{self._base_url}/api/notifications",
            json={
                "message": message,
                "reference_id": reference_id,
                "idempotency_key": idempotency_key,
            },
            headers={"X-API-Key": self._api_key},
            timeout=self._timeout,
        )

        response.raise_for_status()

        if response.status_code != 201:
            raise ValueError("Unexpected Capashino response status")
