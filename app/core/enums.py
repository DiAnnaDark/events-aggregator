from enum import StrEnum


class TicketStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    UNCERTAIN = "uncertain"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OperationStatus(StrEnum):
    PREPARED = "prepared"
    IN_FLIGHT = "in_flight"
    SUCCESS = "success"
    FAILED = "failed"
    UNKNOWN = "unknown"


class OperationType(StrEnum):
    REGISTER = "register"
    CANCEL = "cancel"


class SyncStatus(StrEnum):
    NEVER_RUN = "never_run"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"


class OutboxEventType(StrEnum):
    TICKET_PURCHASED = "ticket_purchased"


class EventStatus(StrEnum):
    NEW = "new"
    PUBLISHED = "published"
    FINISHED = "finished"
    REGISTRATION_CLOSED = "registration_closed"


def parse_event_status(value: str) -> EventStatus | str:
    try:
        return EventStatus(value)
    except ValueError:
        return value
