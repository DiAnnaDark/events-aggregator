from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter(prefix="/api")


@router.post("/sync/trigger")
async def trigger_sync(
    request: Request,
    response: Response,
) -> dict[str, str]:
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
        response.status_code = 202
        return {"status": "already_running"}

    return {"status": "started"}
