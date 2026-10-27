from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, EmailStr, StringConstraints

PersonName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
    ),
]

SeatName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[A-Z][1-9][0-9]*$",
    ),
]


class TicketCreate(BaseModel):
    event_id: UUID
    first_name: PersonName
    last_name: PersonName
    email: EmailStr
    seat: SeatName


class TicketCreated(BaseModel):
    ticket_id: UUID


class TicketCancelled(BaseModel):
    success: bool
