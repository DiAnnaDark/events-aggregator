from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field


class PlaceSnapshot(BaseModel):
    id: UUID
    name: str
    city: str
    address: str
    seats_pattern: str
    created_at: AwareDatetime
    changed_at: AwareDatetime


class EventSnapshot(BaseModel):
    id: UUID
    name: str
    place: PlaceSnapshot
    event_time: AwareDatetime
    registration_deadline: AwareDatetime
    status: str
    number_of_visitors: int = Field(ge=0)
    changed_at: AwareDatetime
    created_at: AwareDatetime
    status_changed_at: AwareDatetime
