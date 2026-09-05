"""Every file the Dockerfiles COPY must survive .dockerignore.

The two images share one build context, so .dockerignore is the union of what
each needs. It denies `rag/*` and re-admits modules one by one, which means a
new `COPY rag/<file>` in either Dockerfile is a silent trap: the file is right
there in git, `docker build` cannot see it, and the failure reads

    ERROR: "/rag/worker.py": not found

naming neither .dockerignore nor the rule that dropped it. That is exactly how
both images were broken at once, and nothing in the test suite noticed --
building an image is too slow and too heavy to run per-commit, so the check has
to be structural. This parses the COPY instructions and replays Docker's
exclusion rules over the paths they name.
"""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent

DOCKERFILES = ["Dockerfile", "gpu/Dockerfile"]


def _segment_regex(segment):
    """One path segment of a pattern, where `*` does not cross a `/`."""

    out = ""

    for char in segment:
        if char == "*":
            out += "[^/]*"
        elif char == "?":
            out += "[^/]"
        else:
            out += re.escape(char)

    return out


def _pattern_regex(pattern):
    """A .dockerignore pattern as an anchored regex, with `**` spanning segments."""

    segments = pattern.split("/")
    out = ""

    for index, segment in enumerate(segments):
        last = index == len(segments) - 1

        if segment == "**":
            # Any number of segments, including none.
            out += ".*" if last else "(?:[^/]+/)*"
            continue

        out += _segment_regex(segment)

        if not last:
            out += "/"

    return out


def dockerignore_rules():
    """(regex, negated) for each pattern, in file order."""

    rules = []

    for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        negated = line.startswith("!")
        pattern = line[1:].strip() if negated else line

        rules.append((re.compile(_pattern_regex(pattern.strip("/"))), negated))

    return rules


def excluded_by(path, rules):
    """The pattern that drops `path` from the build context, or None.

    Docker takes the last pattern that matches, so a later `!rag/audio.py`
    re-admits a file that an earlier `rag/*` denied. A file is also gone when
    one of its parent directories is excluded and nothing re-admits the file
    itself -- that is the `rag/*` case.
    """

    candidates = [path]

    parent = Path(path).parent

    while str(parent) != ".":
        candidates.append(str(parent))
        parent = parent.parent

    verdict = None

    for candidate in candidates:
        for regex, negated in rules:
            if regex.fullmatch(candidate):
                verdict = None if negated else regex.pattern

        # A decision about the file itself wins over its directories.
        if verdict is not None:
            return verdict

    return verdict


def copy_sources(dockerfile):
    """Every source path named by a COPY in one Dockerfile."""

    text = (ROOT / dockerfile).read_text(encoding="utf-8")

    # Rejoin backslash continuations before splitting into instructions.
    text = re.sub(r"\\\s*\n\s*", " ", text)

    sources = []

    for line in text.splitlines():
        line = line.strip()

        if not line.upper().startswith("COPY "):
            continue

        tokens = line.split()[1:]

        # `COPY --from=builder ...` copies from an earlier stage, not from the
        # build context, so .dockerignore has no say over it.
        if any(token.startswith("--from=") for token in tokens):
            continue

        tokens = [token for token in tokens if not token.startswith("--")]

        # The last token is the destination inside the image.
        sources.extend(tokens[:-1])

    return sources


PAIRS = [
    (dockerfile, source)
    for dockerfile in DOCKERFILES
    for source in copy_sources(dockerfile)
]


def test_copy_sources_were_found():
    """Guard the parser itself: a COPY-less parse would make this file vacuous."""

    for dockerfile in DOCKERFILES:
        assert copy_sources(dockerfile), f"parsed no COPY sources from {dockerfile}"


@pytest.mark.parametrize("dockerfile,source", PAIRS)
def test_copy_source_exists(dockerfile, source):
    assert (ROOT / source).exists(), (
        f"{dockerfile} copies {source}, which does not exist in the repository"
    )


@pytest.mark.parametrize("dockerfile,source", PAIRS)
def test_copy_source_is_in_the_build_context(dockerfile, source):
    pattern = excluded_by(source, dockerignore_rules())

    assert pattern is None, (
        f"{dockerfile} copies {source}, but .dockerignore keeps it out of the "
        f"build context (pattern {pattern!r}). The build fails at that COPY "
        f'with \'"/{source}": not found\'. Add `!{source}` to .dockerignore.'
    )
