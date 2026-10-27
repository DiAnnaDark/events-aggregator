from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api")


@router.post("/sync/trigger")
async def trigger_sync(request: Request) -> dict[str, str]:
    runner = getattr(
        request.app.state,
        "sync_runner",
        None,
    )

    if runner is None:
        raise HTTPException(
            status_code=503,
            detail="Synchronization is not configured",
        )

    if not runner.trigger():
        raise HTTPException(
            status_code=409,
            detail="Synchronization is already running",
        )

    return {"status": "started"}
