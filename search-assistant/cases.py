"""Ten queries the assistant has to get right, and a runner for them.

    python search-assistant/cases.py            # run all ten
    python search-assistant/cases.py 3 7        # run only those

These are end-to-end: a real sentence goes to the real model and the plan it
returns runs against the real catalog. That is the point — the unit tests in
tests/test_search.py already prove the SQL is built correctly from a plan, and
they would all still pass while the model quietly stopped extracting a doctor's
name. Only a live run catches that.

Which means they are not free and not perfectly repeatable, so they live here as
a script rather than in pytest. Run them after touching the prompt, the schema
description, or the catalog.

Each case says only what it actually cares about. `total` is pinned where the
answer is a fact about the seeded catalog ("second year Biochemistry has three
lectures"); elsewhere it asks for a floor, so adding a lecture does not turn a
green run red.

The UI at /static/search.html serves these same rows as its sample chips, so
there is one list, not two that drift.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

for path in (os.path.dirname(_HERE), _HERE):
    if path not in sys.path:
        sys.path.insert(0, path)


CASES = [
    {
        "query": "عايز محاضرات دكتور أحمد حسن في الفسيولوجي",
        "why": "the ordinary case: a doctor and a subject, both named",
        "expect": {
            "outcome": "choose",
            "target": "lectures",
            "filters": [("users", "name"), ("subjects", "name")],
            "total": 9,
            "every": {"subject": "Physiology", "doctor": "Ahmed Hassan"},
        },
    },
    {
        "query": "عايز محاضرات",
        "why": "nothing to filter on — ask, do not guess",
        "expect": {"outcome": "clarify", "clarify": True},
    },
    {
        "query": "عايز كتاب في التشريح",
        "why": "the platform has no books; say so instead of inventing a table",
        "expect": {"outcome": "unsupported", "reason": True},
    },
    {
        "query": "محاضرات سنة تانية في الكيمياء الحيوية",
        "why": "academic_year is the study year, and it is data not text",
        "expect": {
            "outcome": "choose",
            "filters": [("courses", "academic_year"), ("subjects", "name")],
            "total": 3,
            "every": {"subject": "Biochemistry"},
        },
    },
    {
        "query": "مين الدكاترة اللي بيدرسوا هستولوجي",
        "why": "asking for people, not lectures: target flips to users",
        "expect": {
            "outcome": "go",
            "target": "users",
            "filters": [("users", "role"), ("subjects", "name")],
            "total": 1,
            "contains": "Mona Abdelrahman",
        },
    },
    {
        "query": "عايز المحاضرة اللي شرح فيها الجهاز الهيكلي",
        "why": "content, not a title — this has to reach the transcript",
        "expect": {"outcome": "go", "text": True, "total": 1, "url": "/?lecture_id=1"},
    },
    {
        "query": "show me second year physiology lectures",
        "why": "the same question in English must plan identically",
        "expect": {
            "outcome": "choose",
            "target": "lectures",
            "filters": [("courses", "academic_year"), ("subjects", "name")],
            "total": 4,
            "every": {"subject": "Physiology"},
        },
    },
    {
        "query": "عايز محاضرات دكتور أحمد",
        "why": "three doctors are called Ahmed. The catalog answers that, not "
               "a clarifying question asked before the search ran",
        "expect": {"outcome": "choose", "filters": [("users", "name")], "min": 10},
    },
    {
        "query": "محاضرات دكتور زغلول",
        "why": "a name nobody has: empty, and the reply must say which filter missed",
        "expect": {"outcome": "none", "total": 0},
    },
    {
        "query": "الفسيولوجي",
        "history": [
            ("user", "عايز محاضرات"),
            ("model", '{"intent":"clarify","clarify":"عايز محاضرات مادة إيه؟"}'),
        ],
        "why": "an answer to a clarification continues the search, it does not "
               "start a new one",
        "expect": {
            "outcome": "choose",
            "target": "lectures",
            "filters": [("subjects", "name")],
            "min": 1,
            "every": {"subject": "Physiology"},
        },
    },
]


def check(case, out):
    """Compare one result against what the case asked for. Returns the failures."""

    want = case["expect"]
    plan = out.get("plan") or {}
    bad = []

    if out["outcome"] != want["outcome"]:
        bad.append(f"outcome {out['outcome']!r}, wanted {want['outcome']!r}")

    if "target" in want and plan.get("target") != want["target"]:
        bad.append(f"target {plan.get('target')!r}, wanted {want['target']!r}")

    got = {(f["table"], f["column"]) for f in plan.get("filters", [])}

    for pair in want.get("filters", []):
        if pair not in got:
            bad.append(f"no filter on {pair[0]}.{pair[1]}")

    if want.get("text") and not plan.get("text"):
        bad.append("nothing went to the transcript search")

    if "total" in want and out["total"] != want["total"]:
        bad.append(f"{out['total']} rows, wanted {want['total']}")

    if "min" in want and out["total"] < want["min"]:
        bad.append(f"{out['total']} rows, wanted at least {want['min']}")

    if "url" in want and out["url"] != want["url"]:
        bad.append(f"url {out['url']!r}, wanted {want['url']!r}")

    if want.get("clarify") and not out["clarify"]:
        bad.append("no question to ask back")

    if want.get("reason") and not out["reason"]:
        bad.append("no reason given")

    if "contains" in want:
        names = [row.get("title") or row.get("name") for row in out["results"]]
        if want["contains"] not in names:
            bad.append(f"{want['contains']!r} not in the results")

    for key, value in want.get("every", {}).items():
        for row in out["results"]:
            found = row.get(key)
            found = found.get("name") if isinstance(found, dict) else found
            if found != value:
                bad.append(f"a row has {key}={found!r}, wanted {value!r}")
                break

    if out["outcome"] == "none" and len(plan.get("filters", [])) > 1 and not out["notes"]:
        bad.append("empty result with no diagnostic")

    return bad


def quota(out):
    """Whether this reply is a rate limit rather than an answer.

    A 429 is not the assistant being wrong, and grading it as a failure buries
    the four real results under a wall of red. The per-minute limit is worth
    waiting out; the per-day one is not, so the run stops and says why.
    """

    text = " ".join(out.get("notes", []))

    if "RESOURCE_EXHAUSTED" not in text and "429" not in text:
        return None

    return "day" if "PerDay" in text else "minute"


def main(argv):

    import re
    import time

    import search

    picked = [int(n) for n in argv] or list(range(1, len(CASES) + 1))
    failed = 0
    ran = 0

    for position, number in enumerate(picked):

        # The free tier allows five requests a minute. Pacing the run is the
        # difference between grading the assistant and grading the quota.
        if position:
            time.sleep(13)

        case = CASES[number - 1]
        out = search.answer(case["query"], history=case.get("history"))

        limit = quota(out)

        if limit == "minute":
            wait = re.search(r"retryDelay': '(\d+)s", " ".join(out["notes"]))
            time.sleep(int(wait.group(1)) + 2 if wait else 30)
            out = search.answer(case["query"], history=case.get("history"))
            limit = quota(out)

        if limit == "day":
            print(f"\nstopped at {number}. the model's daily free-tier quota is "
                  f"spent — not a failure, and nothing to fix. Re-run tomorrow "
                  f"or set a billed GEMINI_API_KEY.")
            break

        ran += 1
        bad = check(case, out)
        failed += bool(bad)

        mark = "FAIL" if bad else "ok  "
        print(f"\n{mark} {number:>2}. {case['query']}")
        print(f"       {case['why']}")

        plan = out.get("plan") or {}

        for item in plan.get("filters", []):
            shown = item["value"] or item["values"]
            print(f"       · {item['table']}.{item['column']} {item['op']} {shown!r}"
                  f"   ← {item['means']}")

        if plan.get("text"):
            print(f"       · text {plan['text']!r}")

        print(f"       → {out['outcome']}, {out['total']} row(s)"
              + (f", url {out['url']}" if out["url"] else ""))

        for row in out["results"][:3]:
            print(f"           {row.get('title') or row.get('name')}  {row['url'] or ''}")

        if out["total"] > 3:
            print(f"           … {out['total'] - 3} more")

        for line in out["clarify"], out["reason"]:
            if line:
                print(f"       “{line}”")

        for note in out["notes"]:
            print(f"       note: {note}")

        for problem in bad:
            print(f"       ✗ {problem}")

    skipped = len(picked) - ran

    print(f"\n{ran - failed}/{ran} passed"
          + (f", {skipped} not run (quota)" if skipped else ""))

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
