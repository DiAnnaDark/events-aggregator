from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.enums import TicketStatus


@dataclass(frozen=True)
class TicketCancellationSnapshot:
    ticket_id: UUID
    event_id: UUID
    provider_ticket_id: UUID | None
    status: TicketStatus
    event_time: datetime
