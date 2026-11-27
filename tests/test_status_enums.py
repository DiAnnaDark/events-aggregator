from enum import StrEnum

import pytest

from app.core.enums import (
    EventStatus,
    OperationStatus,
    OperationType,
    OutboxEventType,
    OutboxStatus,
    SyncStatus,
    TicketStatus,
)


@pytest.mark.parametrize(
    ("enum_type", "expected"),
    [
        (
            TicketStatus,
            {"pending", "active", "uncertain", "failed", "cancelled"},
        ),
        (
            OperationStatus,
            {"prepared", "in_flight", "success", "failed", "unknown"},
        ),
        (OperationType, {"register", "cancel"}),
        (SyncStatus, {"never_run", "running", "success", "failed"}),
        (OutboxStatus, {"pending", "sent"}),
        (OutboxEventType, {"ticket_purchased"}),
        (EventStatus, {"new", "published", "finished", "registration_closed"}),
    ],
)
def test_enum_values(enum_type, expected):
    assert issubclass(enum_type, StrEnum)
    assert {item.value for item in enum_type} == expected
