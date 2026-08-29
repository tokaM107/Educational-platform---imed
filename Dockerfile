FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    ENABLE_DEMO_UI=false \
    LOG_LEVEL=INFO

WORKDIR /srv/app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY requirements-prod.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements-prod.txt

# app/static is excluded by .dockerignore. Only the HTTP runtime portion of
# rag is copied; transcription and ingestion remain local/offline tooling.
COPY app ./app
COPY rag/__init__.py rag/bunny.py ./rag/
COPY search-assistant ./search-assistant

USER appuser

EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
