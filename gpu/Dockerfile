# The RunPod Serverless worker image.
#
#   docker build -f gpu/Dockerfile -t <registry>/lecture-asr:v1 .
#
# Built from the repository root, not from gpu/, because the worker imports
# rag.audio, rag.transcribe_cohere and rag.media_url — the same modules the
# workstation path uses, so both produce the identical transcript format.
#
# Base image carries CUDA and a matched PyTorch build. Installing torch from
# PyPI on top of a RunPod GPU image is the classic way to end up with a torch
# that cannot see the card, so it is deliberately not in requirements.
FROM runpod/pytorch:0.7.0-dev-cu1281-torch271-ubuntu2204

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    LOG_LEVEL=INFO

# The weights are a RunPod Cached Model, mounted on the volume by the platform
# and configured on the endpoint together with the Hugging Face token that can
# read the gated repository. Nothing about them lives in this image: no
# download at build, no download at run, and no token in any layer.
#
# HF_HOME points at the mount so anything in the Hugging Face stack that
# consults the cache on its own finds it at $HF_HOME/hub, which is exactly
# where RunPod puts it. The loader does not rely on that -- it resolves the
# snapshot directory itself and passes a path -- but a library that looks
# somewhere else would otherwise look in a directory that is empty.
ENV HF_HOME=/runpod-volume/huggingface-cache

# The worker never fetches weights. If a file is missing from the cache the
# load must fail with a message naming the cache, rather than quietly turning
# into a download from a container that holds no token and may have no route
# out — mid-job, with a GPU already billing.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# ffmpeg is what reads the Bunny rendition and cuts the audio. Without it every
# job fails at AudioError("ffmpeg is not installed"), which is the single most
# common way this image is built wrong.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY gpu/requirements.txt ./gpu/requirements.txt

# `python3`, never `python`. The base image installs Python from the deadsnakes
# PPA as versioned binaries only -- python3.9 through python3.13 -- and never
# creates a plain `python`, so `python -m pip` here failed with exit code 127
# (command not found) before a single dependency was installed.
#
# `python3` is Ubuntu 22.04's own symlink to 3.10, and 3.10 is the interpreter
# the base image installed its CUDA-matched torch into:
#
#     RUN python3.10 -m pip install torch==2.7.1 --index-url .../cu128
#
# so it is the one this worker must use. Do NOT "fix" a missing interpreter by
# apt-installing python3-pip or a newer python3: that puts the requirements
# beside a torch the process cannot import, and the failure shows up not here
# but as the model running on CPU at roughly 1x, which is the exact trap
# gpu/requirements.txt warns about.
RUN python3 -m pip install --no-cache-dir -r gpu/requirements.txt

# Fail the build here, loudly, rather than at the first job, if the interpreter
# that just received the requirements is not the one holding the GPU torch.
RUN python3 --version \
    && python3 -m pip --version \
    && python3 -c "import torch; print('torch', torch.__version__, 'cuda', torch.version.cuda)"

# Only what the worker actually imports. The API, the database layer and the
# embedding client are not here: this container transcribes and returns text,
# and it holds no database credentials to lose.
COPY rag/__init__.py rag/audio.py rag/media_url.py rag/transcribe_cohere.py ./rag/
COPY gpu/__init__.py gpu/handler.py ./gpu/

# Which repository to look for on the volume. It is not downloaded here; the
# name is what turns into the cache directory the loader resolves:
#
#   $HF_HOME/hub/models--CohereLabs--cohere-transcribe-arabic-07-2026/
#
# so it has to match the model configured on the endpoint. ARG so a different
# checkpoint needs an argument rather than an edit.
ARG COHERE_TRANSCRIBE_MODEL=CohereLabs/cohere-transcribe-arabic-07-2026
ENV COHERE_TRANSCRIBE_MODEL=${COHERE_TRANSCRIBE_MODEL}

CMD ["python3", "-u", "-m", "gpu.handler"]
