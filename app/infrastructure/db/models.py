from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import (
    EventStatus,
    OperationStatus,
    OperationType,
    OutboxEventType,
    OutboxStatus,
    SyncStatus,
    TicketStatus,
)
from app.infrastructure.db.base import Base
from app.infrastructure.db.enum_types import (
    EnumText,
    EventStatusText,
    OutboxEventTypeText,
)


class Place(Base):
    __tablename__ = "places"

    id: Mapped[UUID] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    seats_pattern: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class Event(Base):
    __tablename__ = "events"

    __table_args__ = (
        CheckConstraint(
            "number_of_visitors >= 0",
            name="ck_events_number_of_visitors_nonnegative",
        ),
        Index("ix_events_event_time_id", "event_time", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)

    place_id: Mapped[UUID] = mapped_column(
        ForeignKey("places.id"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)

    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    registration_deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    status: Mapped[EventStatus | str] = mapped_column(EventStatusText(), nullable=False)

    number_of_visitors: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class Ticket(Base):
    __tablename__ = "tickets"

    __table_args__ = (
        Index("ix_tickets_event_id", "event_id"),
        Index(
            "uq_tickets_reserved_event_seat",
            "event_id",
            "seat",
            unique=True,
            postgresql_where=text("status IN ('pending', 'active', 'uncertain')"),
        ),
        Index(
            "uq_tickets_live_provider_ticket_id",
            "provider_ticket_id",
            unique=True,
            postgresql_where=text(
                "provider_ticket_id IS NOT NULL AND status IN ('active', 'uncertain')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)

    provider_ticket_id: Mapped[UUID | None] = mapped_column(
        nullable=True,
    )

    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id"),
        nullable=False,
    )

    first_name: Mapped[str] = mapped_column(Text, nullable=False)
    last_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    seat: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TicketStatus] = mapped_column(EnumText(TicketStatus), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    registered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class TicketOperation(Base):
    __tablename__ = "ticket_operations"

    __table_args__ = (
        Index(
            "ix_ticket_operations_registration_id",
            "registration_id",
        ),
        Index(
            "uq_ticket_operations_unresolved_registration",
            "registration_id",
            unique=True,
            postgresql_where=text("status IN ('prepared', 'in_flight', 'unknown')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)

    registration_id: Mapped[UUID] = mapped_column(
        ForeignKey("tickets.id"),
        nullable=False,
    )

    operation_type: Mapped[OperationType] = mapped_column(
        EnumText(OperationType),
        nullable=False,
    )

    idempotency_key: Mapped[str | None] = mapped_column(
        Text,
        unique=True,
        nullable=True,
    )

    request_fingerprint: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[OperationStatus] = mapped_column(
        EnumText(OperationStatus),
        nullable=False,
    )

    provider_http_status: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
    )

    error_code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class SyncState(Base):
    __tablename__ = "sync_state"

    __table_args__ = (
        CheckConstraint(
            "id = 1",
            name="ck_sync_state_singleton",
        ),
        CheckConstraint(
            "sync_status IN ('never_run', 'running', 'success', 'failed')",
            name="ck_sync_state_status",
        ),
    )

    id: Mapped[int] = mapped_column(
        SmallInteger,
        primary_key=True,
    )

    last_sync_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    sync_status: Mapped[SyncStatus] = mapped_column(
        EnumText(SyncStatus),
        nullable=False,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class Outbox(Base):
    __tablename__ = "outbox"

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'sent')",
            name="ck_outbox_status",
        ),
        Index(
            "ix_outbox_pending_created_at_id",
            "created_at",
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)

    event_type: Mapped[OutboxEventType | str] = mapped_column(
        OutboxEventTypeText(),
        nullable=False,
    )

    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )

    status: Mapped[OutboxStatus] = mapped_column(
        EnumText(OutboxStatus),
        nullable=False,
        server_default="pending",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
