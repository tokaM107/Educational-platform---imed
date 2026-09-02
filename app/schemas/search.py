"""Smart Search Assistant: request and reply.

The plan and result rows stay loosely typed on purpose. A result is a course,
doctor, book, category, or educational level. The envelope around those safe
catalog shapes is what callers branch on, and that is pinned.
"""

from pydantic import BaseModel, Field


class Turn(BaseModel):
    """One earlier message, so a clarification answer keeps its context."""

    role: str = Field(description="'user' or 'model'")
    content: str


class SearchRequest(BaseModel):

    query: str = Field(description="what the student typed, exactly as typed")
    history: list[Turn] = Field(
        default_factory=list,
        description="the conversation so far, oldest first",
    )


class SearchResponse(BaseModel):

    ok: bool
    query: str
    outcome: str = Field(
        description="go (one match, follow url) · choose (several, ask which) · "
                    "none · clarify · unsupported · error"
    )
    url: str | None = Field(description="set only when outcome is 'go'")
    results: list[dict] = Field(default_factory=list)
    total: int = 0
    clarify: str = ""
    reason: str = ""
    missing: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    dropped: list[dict] = Field(default_factory=list)
    plan: dict | None = Field(
        default=None, description="what the model understood, for the test page"
    )
    sql: str = ""
