from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.events.schemas import EventDetail, EventPage
from app.infrastructure.db.event_read_repository import (
    PostgresEventReadRepository,
)

router = APIRouter(prefix="/api")


async def get_event_read_repository(
    request: Request,
) -> AsyncIterator[PostgresEventReadRepository]:
    session_factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state,
        "db_session_factory",
        None,
    )

    if session_factory is None:
        raise HTTPException(
            status_code=503,
            detail="Database is unavailable",
        )

    async with session_factory() as session:
        yield PostgresEventReadRepository(session)


EventReadDependency = Annotated[
    PostgresEventReadRepository,
    Depends(get_event_read_repository),
]


@router.get("/events", response_model=EventPage)
async def list_events(
    request: Request,
    repository: EventReadDependency,
    date_from: date | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1)] = 20,
) -> EventPage:
    count, events = await repository.list_events(
        date_from=date_from,
        page=page,
        page_size=page_size,
    )

    def page_url(number: int) -> str:
        return str(
            request.url.include_query_params(
                page=number,
                page_size=page_size,
            )
        )

    next_url = page_url(page + 1) if page * page_size < count else None
    previous_url = page_url(page - 1) if page > 1 else None

    return EventPage(
        count=count,
        next=next_url,
        previous=previous_url,
        results=events,
    )


@router.get("/events/{event_id}", response_model=EventDetail)
async def get_event(
    event_id: UUID,
    repository: EventReadDependency,
) -> EventDetail:
    event = await repository.get_event(event_id)

    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found",
        )

    return event
