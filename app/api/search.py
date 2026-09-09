"""Smart Search Assistant: one sentence in, safe catalog results out.

Two stages, both living in search-assistant/ so they can be run and tested on
their own: `extract_info` turns the sentence into a plan, `search` runs that plan
against the catalog. This endpoint is the thin part that joins them and hands the
result to the page.

The import is done by hand because the directory name has a hyphen in it, which
makes it a valid folder and an invalid module name. Loading by path keeps the
folder name the owner chose rather than renaming the feature to suit Python.
"""

import importlib.util
import logging
import sys
from pathlib import Path
from time import monotonic

from fastapi import APIRouter, Depends

from app.api.deps import get_conn
from app.schemas.search import SearchRequest, SearchResponse


_DIR = Path(__file__).resolve().parents[2] / "search-assistant"

# On the path as well as loaded by path: search.py imports extract_info by name.
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))


def _load(name):

    spec = importlib.util.spec_from_file_location(name, _DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    return module


extract_info = _load("extract_info")
assistant = _load("search")
cases = _load("cases")
logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api",
    tags=["Search"],
)


@router.post("/search", response_model=SearchResponse)
def smart_search(
    data: SearchRequest,
    conn=Depends(get_conn),
):
    """Read the question and search the public catalog boundary.

    This endpoint is intentionally public and unmetered. Its server-owned query
    map still limits results to public catalog metadata.

    Never raises on a bad question: an unreachable model comes back as
    `outcome: "error"` with the reason, because a search box that throws a 500
    at a student is worse than one that says it could not understand.
    """

    started = monotonic()
    logger.info(
        "AI search started: query_chars=%d history_turns=%d",
        len(data.query),
        len(data.history),
    )

    try:
        envelope = extract_info.extract(
            data.query,
            history=[(turn.role, turn.content) for turn in data.history],
        )

        result = assistant.search(envelope, conn=conn)
    except Exception:
        logger.exception(
            "AI search failed after %.3fs", monotonic() - started
        )
        raise

    if result.get("outcome") == "error":
        logger.error(
            "AI search returned an error after %.3fs: %s",
            monotonic() - started,
            "; ".join(result.get("notes", [])) or "unknown error",
        )
    else:
        logger.info(
            "AI search completed: outcome=%s target=%s results=%d duration=%.3fs",
            result.get("outcome"),
            (result.get("plan") or {}).get("target", ""),
            result.get("total", 0),
            monotonic() - started,
        )

    return SearchResponse(**{
        key: value for key, value in result.items() if key != "params"
    })


@router.get("/search/cases")
def sample_cases():
    """The queries on the test page.

    Served from search-assistant/cases.py rather than copied into the page, so
    the chips you click and the script that grades them are the same ten rows.
    """

    return [
        {"n": number, "query": case["query"], "why": case["why"],
         "history": [{"role": role, "content": text}
                     for role, text in case.get("history", [])]}
        for number, case in enumerate(cases.CASES, start=1)
    ]
