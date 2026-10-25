from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
