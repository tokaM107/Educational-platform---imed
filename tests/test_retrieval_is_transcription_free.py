"""A student's question must never start a GPU.

The retrieval path is a pgvector lookup over chunks the worker stored earlier.
Nothing on it may reach Bunny, ffmpeg, RunPod or an ASR model — not because
that would be slow, but because it would put a metered GPU on the latency path
of every question and bill per chat message.

These are structural tests: they assert on what the query modules import and
what they do, rather than on a wired-up request. That is deliberate. The way
this regresses is somebody adding a convenient "transcribe it if missing"
fallback inside retrieval, and an import check catches that on the commit that
introduces it.
"""

import ast
from pathlib import Path

import pytest

from app.services import retrieval


ROOT = Path(__file__).resolve().parent.parent

# Modules on the runtime query path.
QUERY_PATH = [
    "app/services/retrieval.py",
    "app/services/tutor.py",
    "app/api/chat.py",
    "app/api/search.py",
]

# Anything whose presence would mean transcription reached the query path.
FORBIDDEN = (
    "rag.transcribe_cohere",
    "rag.transcribe_runpod",
    "rag.transcribe_whisper",
    "rag.audio",
    "rag.worker",
    "gpu.handler",
    "transformers",
    "torch",
    "runpod",
)


def imported_modules(path):
    """Every module named by an import statement in one file."""

    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))

    names = set()

    for node in ast.walk(tree):

        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)

        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)

    return names


@pytest.mark.parametrize("path", QUERY_PATH)
def test_the_query_path_imports_nothing_that_transcribes(path):
    imported = imported_modules(path)

    for forbidden in FORBIDDEN:
        assert not any(
            name == forbidden or name.startswith(forbidden + ".")
            for name in imported
        ), f"{path} imports {forbidden} — transcription is on the query path"


def test_retrieval_only_ever_reads_transcript_chunks():
    """It selects; it does not write, and it does not call out."""

    source = (ROOT / "app/services/retrieval.py").read_text(encoding="utf-8")
    upper = source.upper()

    for statement in ("INSERT ", "UPDATE ", "DELETE ", "CREATE "):
        assert statement not in upper, f"retrieval.py contains {statement.strip()}"


def test_searching_issues_a_database_query_and_nothing_else(monkeypatch):
    """The whole of `search`: one SQL statement against transcript_chunks."""

    executed = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            executed.append(sql)

        def fetchall(self):
            return []

    class Conn:
        def cursor(self):
            return Cursor()

    # Any outbound HTTP on this path would be a GPU or a CDN read.
    import requests

    def forbidden(*args, **kwargs):
        raise AssertionError("retrieval must not make network calls")

    for name in ("get", "post", "request"):
        monkeypatch.setattr(requests, name, forbidden)

    retrieval.search(Conn(), [0.0] * 4, top_k=3)

    assert len(executed) == 1
    assert "transcript_chunks" in executed[0]
