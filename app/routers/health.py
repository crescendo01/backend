from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/")
def root():
    return {"service": "crescendo-backend", "status": "ok"}


@router.get("/healthz")
def healthz():
    """Render hits this for its health check — keep it dependency-free and fast."""
    return {"status": "ok"}
