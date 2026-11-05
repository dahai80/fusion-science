import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/web", include_in_schema=False)
async def web_console():
    # Issue #40: built-in web surface for the API (viewer UI). The page itself
    # carries no secrets; every API call it makes still passes through
    # APIKeyMiddleware with the user's own key (prompted in the header bar).
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        logger.error("web console missing: %s", index)
        return JSONResponse(status_code=404, content={"detail": "web_console_not_built"})
    return FileResponse(index, media_type="text/html")
