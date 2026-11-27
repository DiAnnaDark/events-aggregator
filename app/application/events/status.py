from typing import Annotated

from pydantic import AfterValidator

from app.core.enums import EventStatus, parse_event_status

EventStatusValue = Annotated[
    EventStatus | str,
    AfterValidator(parse_event_status),
]
