from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class TicketCancellationSnapshot:
    ticket_id: UUID
    event_id: UUID
    provider_ticket_id: UUID | None
    status: str
    event_time: datetime
