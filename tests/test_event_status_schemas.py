from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.events.schemas import EventDetail, EventSummary
from app.application.sync.schemas import EventSnapshot
from app.core.enums import EventStatus


@pytest.mark.parametrize(
    "schema",
    [EventSnapshot, EventSummary, EventDetail],
)
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("new", EventStatus.NEW),
        ("published", EventStatus.PUBLISHED),
        ("finished", EventStatus("finished")),
        ("registration_closed", EventStatus("registration_closed")),
        ("future_status", "future_status"),
    ],
)
def test_event_status_conversion(schema, status, expected):
    now = datetime.now(UTC)

    data = {
        "id": uuid4(),
        "name": "Test Event",
        "place": {
            "id": uuid4(),
            "name": "Test Place",
            "city": "Test City",
            "address": "Test Street",
            "seats_pattern": "A1-100",
            "created_at": now,
            "changed_at": now,
        },
        "event_time": now,
        "registration_deadline": now,
        "status": status,
        "number_of_visitors": 0,
        "changed_at": now,
        "created_at": now,
        "status_changed_at": now,
    }

    event = schema.model_validate(data)

    assert event.status == expected
    assert type(event.status) is type(expected)
    assert event.model_dump(mode="json")["status"] == status
