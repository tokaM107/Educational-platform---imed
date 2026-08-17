"""FastAPI application: RAG tutor over recorded lectures.

    uvicorn app.main:app --reload
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google.genai import errors as genai_errors

from app.api import auth, chat, events, lectures
from app.config import get_settings
from app.db import close_pool, open_pool


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):

    open_pool()

    yield

    close_pool()


app = FastAPI(
    title="Educational AI Platform",
    description=(
        "Ask a question about a recorded lecture; get the doctor's own "
        "explanation simplified, plus the exact stretch of video it came from."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


app.include_router(auth.router)
app.include_router(lectures.router)
app.include_router(chat.router)
app.include_router(events.router)

@app.exception_handler(genai_errors.APIError)
def handle_genai_error(request: Request, error: genai_errors.APIError):
    """Never leak a provider stack trace to a student."""

    logging.getLogger(__name__).error("Gemini API error: %s", error)

    return JSONResponse(
        status_code=502,
        content={
            "detail": "خدمة الذكاء الاصطناعي مش متاحة دلوقتي، جرّب تاني بعد شوية.",
            "provider_status": getattr(error, "code", None),
        },
    )


@app.get("/health", tags=["Health"])
def health_check():

    return {"status": "ok"}


# -------------------------
# Demo UI
# -------------------------

app.mount(
    "/static",
    StaticFiles(directory=settings.static_dir),
    name="static",
)


@app.get("/", include_in_schema=False)
def demo_page():

    return FileResponse(settings.static_dir / "index.html")
