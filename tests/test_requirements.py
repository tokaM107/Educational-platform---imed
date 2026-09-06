"""The dependency files must install on the hosts that use them.

Written after a Coolify deployment of the transcription worker died at

    ERROR: Could not find a version that satisfies mlx-metal==0.32.0

mlx-metal is Apple Silicon only and ships no Linux wheel, so an unmarked pin
made `pip install -r requirements.txt` impossible on any Linux host. Nothing in
this repository imports mlx; it was left over from a local Whisper experiment
and had never been installed anywhere but a Mac.

These tests read the files rather than installing from them — a real install is
minutes and a network — but they check the two things that actually broke:
what the marker set evaluates to on Linux, and whether the production file
still names everything `python -m rag.worker` imports.
"""

from pathlib import Path

import pytest
from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parent.parent

DEV = ROOT / "requirements.txt"
PROD = ROOT / "requirements-prod.txt"


# A Linux production host, for evaluating environment markers.
LINUX = {
    "sys_platform": "linux",
    "platform_system": "Linux",
    "platform_machine": "x86_64",
    "os_name": "posix",
    "python_version": "3.12",
    "python_full_version": "3.12.0",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
    "extra": "",
}


# Distributions that publish no Linux wheel at all. Pinning one of these
# without a marker does not degrade the install, it ends it.
APPLE_ONLY = {"mlx", "mlx-metal", "mlx-whisper"}


# Third-party packages the worker's import graph reaches:
#
#   rag.worker -> requests, rag.bunny, rag.ingest, rag.transcribe_runpod
#   rag.ingest -> app.services.embeddings -> google-genai, pgvector
#   app.db     -> psycopg, psycopg-pool, pgvector
#   app.config -> python-dotenv
#
# Dropping any of these from the production file leaves an image that builds
# and then fails on the first job.
WORKER_NEEDS = {
    "requests", "google-genai", "pgvector",
    "psycopg", "psycopg-pool", "python-dotenv",
}


def requirements(path):

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].strip()

        if line:
            yield Requirement(line)


def installed_on(path, environment):
    """The distribution names that survive marker evaluation."""

    return {
        req.name.lower()
        for req in requirements(path)
        if req.marker is None or req.marker.evaluate(environment)
    }


@pytest.mark.parametrize("path", [DEV, PROD], ids=["requirements", "prod"])
def test_every_requirement_line_parses(path):
    """A malformed marker silently drops or keeps the wrong package."""

    assert list(requirements(path))


def test_no_apple_only_package_is_installed_on_linux():
    """The regression: this is what ended the Coolify build."""

    offenders = APPLE_ONLY & installed_on(DEV, LINUX)

    assert not offenders, (
        f"{sorted(offenders)} publish no Linux wheel, so pip cannot install "
        'requirements.txt on a Linux host. Add: ; sys_platform == "darwin"'
    )


def test_the_apple_only_packages_are_still_available_on_a_mac():
    """Marking them must not mean deleting them from local development."""

    macos = dict(LINUX, sys_platform="darwin", platform_system="Darwin",
                 platform_machine="arm64")

    assert APPLE_ONLY <= installed_on(DEV, macos)


def test_the_production_file_has_no_apple_only_package():
    assert not APPLE_ONLY & {req.name.lower() for req in requirements(PROD)}


def test_the_production_file_is_installable_anywhere():
    """No markers at all: it is the same set on every host that runs it."""

    marked = [str(req) for req in requirements(PROD) if req.marker is not None]

    assert not marked, f"production pins carry environment markers: {marked}"


def test_the_production_file_covers_the_worker():
    """`python -m rag.worker` runs from requirements-prod.txt alone."""

    named = {req.name.lower() for req in requirements(PROD)}

    missing = {name for name in WORKER_NEEDS if name not in named}

    assert not missing, (
        f"requirements-prod.txt is missing {sorted(missing)}, which the "
        "transcription worker imports at start-up"
    )


def test_the_production_file_carries_no_gpu_inference_stack():
    """Transcription runs on RunPod; this host only submits and polls.

    torch and transformers are several gigabytes and are why the GPU image
    exists separately. A worker that pulled them in would make every deploy of
    the queue process pay for a model it never loads.
    """

    named = {req.name.lower() for req in requirements(PROD)}

    assert not named & {
        "torch", "transformers", "accelerate", "librosa", "sentencepiece",
    }


def test_every_pin_is_exact():
    """These files are a lockfile in effect; a range makes deploys differ."""

    for path in (DEV, PROD):
        for req in requirements(path):
            assert all(spec.operator == "==" for spec in req.specifier), (
                f"{path.name}: {req} is not pinned to one version"
            )
