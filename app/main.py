"""FastAPI application: RAG tutor over recorded lectures.

    uvicorn app.main:app --reload
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google.genai import errors as genai_errors

from app.api import (auth, chat, events, exams, lectures, notifications,
                     questions, reports, search, subscriptions)
from app.config import get_settings
from app.db import close_pool, open_pool


settings = get_settings()

# Application logs go nowhere by default: uvicorn configures its own loggers and
# leaves the root one without a handler, so anything the services log —
# especially the background report jobs, which have no response to show for
# themselves — is silently dropped.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)


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
app.include_router(questions.router)
app.include_router(reports.router)
app.include_router(notifications.router)
app.include_router(exams.router)
app.include_router(subscriptions.router)
app.include_router(search.router)

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
# Optional demo UI
# -------------------------

def register_demo_ui(application, app_settings):
    """Register local-only UI routes when explicitly enabled and available."""

    if not app_settings.enable_demo_ui:
        return False

    if not app_settings.static_dir.is_dir():
        logging.getLogger(__name__).warning(
            "ENABLE_DEMO_UI is true but %s does not exist; demo UI disabled",
            app_settings.static_dir,
        )
        return False

    # Keep UI-only imports out of the default production startup path.
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    application.mount(
        "/static",
        StaticFiles(directory=app_settings.static_dir),
        name="static",
    )

    @application.get("/", include_in_schema=False)
    def demo_page():
        return FileResponse(app_settings.static_dir / "index.html")

    return True


register_demo_ui(app, settings)
