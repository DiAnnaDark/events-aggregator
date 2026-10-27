from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.tickets.cancellation_service import (
    CancellationClosed,
    CancellationConflict,
    CancellationProviderRejected,
    CancellationResultUnknown,
    CancellationStateError,
    CancellationUnavailable,
    TicketCancellationService,
    TicketNotFound,
)
from app.application.tickets.registration_service import (
    EventNotFound,
    ProviderRegistrationRejected,
    ProviderResultUnknown,
    RegistrationClosed,
    RegistrationStateError,
    RegistrationUnavailable,
    SeatLookupUnavailable,
    SeatUnavailable,
    TicketRegistrationService,
)
from app.application.tickets.schemas import (
    TicketCancelled,
    TicketCreate,
    TicketCreated,
)
from app.infrastructure.db.ticket_event_reader import PostgresTicketEventReader
from app.infrastructure.db.ticket_store import PostgresTicketStore

router = APIRouter(prefix="/api")


def get_ticket_registration_service(
    request: Request,
) -> TicketRegistrationService:
    session_factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state,
        "db_session_factory",
        None,
    )

    seats_service = getattr(
        request.app.state,
        "seats_service",
        None,
    )

    provider = getattr(
        request.app.state,
        "registration_provider",
        None,
    )

    if session_factory is None or seats_service is None or provider is None:
        raise HTTPException(
            status_code=503,
            detail="Registration service is unavailable",
        )

    return TicketRegistrationService(
        events=PostgresTicketEventReader(session_factory),
        seats=seats_service,
        provider=provider,
        store=PostgresTicketStore(session_factory),
    )


RegistrationDependency = Annotated[
    TicketRegistrationService,
    Depends(get_ticket_registration_service),
]


@router.post(
    "/tickets",
    response_model=TicketCreated,
    status_code=201,
)
async def create_ticket(
    body: TicketCreate,
    service: RegistrationDependency,
) -> TicketCreated:
    try:
        ticket_id = await service.register(body)

    except EventNotFound as error:
        raise HTTPException(
            status_code=404,
            detail="Event not found",
        ) from error

    except (
        RegistrationUnavailable,
        RegistrationClosed,
        SeatUnavailable,
    ) as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except ProviderRegistrationRejected as error:
        raise HTTPException(
            status_code=409,
            detail="Registration was rejected by Events Provider",
        ) from error

    except SeatLookupUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail="Seat availability is temporarily unavailable",
        ) from error

    except (
        ProviderResultUnknown,
        RegistrationStateError,
    ) as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "Registration could not be confirmed safely. "
                "Do not retry until its status has been checked."
            ),
        ) from error

    return TicketCreated(ticket_id=ticket_id)


def get_ticket_cancellation_service(
    request: Request,
) -> TicketCancellationService:
    session_factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state,
        "db_session_factory",
        None,
    )

    seats_service = getattr(
        request.app.state,
        "seats_service",
        None,
    )

    provider = getattr(
        request.app.state,
        "registration_provider",
        None,
    )

    if session_factory is None or seats_service is None or provider is None:
        raise HTTPException(
            status_code=503,
            detail="Cancellation service is unavailable",
        )

    return TicketCancellationService(
        store=PostgresTicketStore(session_factory),
        provider=provider,
        seats=seats_service,
    )


CancellationDependency = Annotated[
    TicketCancellationService,
    Depends(get_ticket_cancellation_service),
]


@router.delete(
    "/tickets/{ticket_id}",
    response_model=TicketCancelled,
    status_code=200,
)
async def cancel_ticket(
    ticket_id: UUID,
    service: CancellationDependency,
) -> TicketCancelled:
    try:
        await service.cancel(ticket_id)

    except TicketNotFound as error:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found",
        ) from error

    except (
        CancellationUnavailable,
        CancellationClosed,
        CancellationConflict,
        CancellationProviderRejected,
    ) as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except (
        CancellationResultUnknown,
        CancellationStateError,
    ) as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "Cancellation could not be confirmed safely. "
                "Do not retry until its status has been checked."
            ),
        ) from error

    return TicketCancelled(success=True)
