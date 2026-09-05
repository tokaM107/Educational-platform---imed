FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    ENABLE_DEMO_UI=false \
    ENABLE_GRADING_DEMO_UI=false \
    LOG_LEVEL=INFO

WORKDIR /srv/app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY requirements-prod.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements-prod.txt

# .dockerignore retains only the grading/login browser assets under app/static.
#
# The image serves two commands: the API (the default below) and the
# transcription worker (`python -m rag.worker`, run as a second container off
# the same image). So the rag modules the worker needs are here too.
#
# What is still absent is the ASR itself. rag/transcribe_cohere.py needs
# torch, transformers and a multi-gigabyte model, and this server has no GPU to
# run them on — the model lives in the RunPod Serverless worker (gpu/handler.py,
# built from gpu/Dockerfile), and ASR_BACKEND=runpod reaches it over RunPod's
# REST API using nothing beyond `requests`.
#
# ffmpeg is absent for the same reason, and that absence is load-bearing: the
# GPU worker fetches the video from Bunny and extracts the audio at its end, so
# no media ever passes through this container. If something here ever needs
# ffmpeg, the media has started flowing the wrong way.
COPY app ./app
COPY rag/__init__.py rag/bunny.py rag/chunking.py rag/ingest.py \
     rag/media_url.py rag/transcribe_runpod.py rag/transcript_format.py \
     rag/worker.py ./rag/
COPY search-assistant ./search-assistant

USER appuser

EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
