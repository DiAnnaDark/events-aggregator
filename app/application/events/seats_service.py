import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Protocol
from uuid import UUID


class SeatsProvider(Protocol):
    async def seats(self, *, event_id: UUID) -> list[str]: ...


@dataclass(frozen=True)
class CacheEntry:
    seats: tuple[str, ...]
    expires_at: float


class SeatsService:
    def __init__(
        self,
        provider: SeatsProvider,
        *,
        ttl_seconds: float = 30.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Cache TTL must be positive")

        self._provider = provider
        self._ttl_seconds = ttl_seconds
        self._clock = clock

        self._cache: dict[UUID, CacheEntry] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}

    async def get_available_seats(
        self,
        event_id: UUID,
    ) -> list[str]:
        lock = self._locks.setdefault(event_id, asyncio.Lock())

        async with lock:
            cached = self._cache.get(event_id)

            if cached is not None and self._clock() < cached.expires_at:
                return list(cached.seats)

            seats = await self._provider.seats(event_id=event_id)

            self._cache[event_id] = CacheEntry(
                seats=tuple(seats),
                expires_at=self._clock() + self._ttl_seconds,
            )

            return list(seats)

    async def invalidate(self, event_id: UUID) -> None:
        lock = self._locks.setdefault(event_id, asyncio.Lock())

        async with lock:
            self._cache.pop(event_id, None)
