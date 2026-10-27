from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.sync.schemas import EventSnapshot
from app.infrastructure.db.models import Event, Place


class PostgresEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, event: EventSnapshot) -> None:
        place_insert = insert(Place).values(**event.place.model_dump())

        place_updates = {
            column: getattr(place_insert.excluded, column)
            for column in (
                "name",
                "city",
                "address",
                "seats_pattern",
                "changed_at",
            )
        }

        place_statement = place_insert.on_conflict_do_update(
            index_elements=[Place.id],
            set_=place_updates,
            where=Place.changed_at <= place_insert.excluded.changed_at,
        )

        await self._session.execute(place_statement)

        event_values = event.model_dump(exclude={"place"})
        event_values["place_id"] = event.place.id

        event_insert = insert(Event).values(**event_values)

        event_updates = {
            column: getattr(event_insert.excluded, column)
            for column in (
                "place_id",
                "name",
                "event_time",
                "registration_deadline",
                "status",
                "number_of_visitors",
                "changed_at",
                "status_changed_at",
            )
        }

        event_statement = event_insert.on_conflict_do_update(
            index_elements=[Event.id],
            set_=event_updates,
            where=Event.changed_at <= event_insert.excluded.changed_at,
        )

        await self._session.execute(event_statement)
