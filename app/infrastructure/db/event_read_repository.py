from datetime import UTC, date, datetime, time
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.events.schemas import (
    EventDetail,
    EventSummary,
    PlaceDetail,
    PlaceSummary,
)
from app.infrastructure.db.models import Event, Place


def to_event_summary(event: Event, place: Place) -> EventSummary:
    return EventSummary(
        id=event.id,
        name=event.name,
        place=PlaceSummary(
            id=place.id,
            name=place.name,
            city=place.city,
            address=place.address,
        ),
        event_time=event.event_time,
        registration_deadline=event.registration_deadline,
        status=event.status,
        number_of_visitors=event.number_of_visitors,
    )


class PostgresEventReadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_events(
        self,
        *,
        date_from: date | None,
        page: int,
        page_size: int,
    ) -> tuple[int, list[EventSummary]]:
        if page < 1 or page_size < 1:
            raise ValueError("Invalid pagination parameters")

        count_query = select(func.count()).select_from(Event)

        events_query = select(Event, Place).join(
            Place,
            Event.place_id == Place.id,
        )

        if date_from is not None:
            start_date = datetime.combine(
                date_from,
                time.min,
                tzinfo=UTC,
            )

            count_query = count_query.where(Event.event_time >= start_date)

            events_query = events_query.where(Event.event_time >= start_date)

        count = await self._session.scalar(count_query)

        events_query = (
            events_query.order_by(Event.event_time, Event.id)
            .limit(page_size)
            .offset((page - 1) * page_size)
        )

        result = await self._session.execute(events_query)

        events = [to_event_summary(event, place) for event, place in result.all()]

        return int(count or 0), events

    async def get_event(
        self,
        event_id: UUID,
    ) -> EventDetail | None:
        query = (
            select(Event, Place)
            .join(Place, Event.place_id == Place.id)
            .where(Event.id == event_id)
        )

        result = await self._session.execute(query)
        row = result.one_or_none()

        if row is None:
            return None

        event, place = row
        summary = to_event_summary(event, place)

        return EventDetail(
            **summary.model_dump(exclude={"place"}),
            place=PlaceDetail(
                **summary.place.model_dump(),
                seats_pattern=place.seats_pattern,
            ),
        )
