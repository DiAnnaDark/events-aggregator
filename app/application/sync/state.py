from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SyncLease:
    started_at: datetime
    last_changed_at: datetime | None
