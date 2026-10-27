from uuid import UUID

from pydantic import AwareDatetime, BaseModel


class PlaceSummary(BaseModel):
    id: UUID
    name: str
    city: str
    address: str


class PlaceDetail(PlaceSummary):
    seats_pattern: str


class EventSummary(BaseModel):
    id: UUID
    name: str
    place: PlaceSummary
    event_time: AwareDatetime
    registration_deadline: AwareDatetime
    status: str
    number_of_visitors: int


class EventDetail(EventSummary):
    place: PlaceDetail


class EventPage(BaseModel):
    count: int
    next: str | None
    previous: str | None
    results: list[EventSummary]
