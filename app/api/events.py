from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.events.schemas import (
    EventDetail,
    EventPage,
    EventSeats,
)
from app.application.events.seats_service import SeatsService
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


def get_seats_service(request: Request) -> SeatsService:
    service = getattr(
        request.app.state,
        "seats_service",
        None,
    )

    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Events Provider is unavailable",
        )

    return service


SeatsServiceDependency = Annotated[
    SeatsService,
    Depends(get_seats_service),
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


@router.get(
    "/events/{event_id}/seats",
    response_model=EventSeats,
)
async def get_event_seats(
    event_id: UUID,
    repository: EventReadDependency,
    seats_service: SeatsServiceDependency,
) -> EventSeats:
    event = await repository.get_event(event_id)

    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found",
        )

    if event.status != "published":
        raise HTTPException(
            status_code=409,
            detail="Event is not published",
        )

    try:
        seats = await seats_service.get_available_seats(event_id)

    except httpx.TimeoutException as error:
        raise HTTPException(
            status_code=504,
            detail="Events Provider timeout",
        ) from error

    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail="Event not found in Events Provider",
            ) from error

        raise HTTPException(
            status_code=502,
            detail="Events Provider returned an error",
        ) from error

    except (httpx.RequestError, ValueError) as error:
        raise HTTPException(
            status_code=502,
            detail="Events Provider is unavailable",
        ) from error

    return EventSeats(
        event_id=event_id,
        available_seats=seats,
    )
