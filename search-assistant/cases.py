"""Manual end-to-end prompts for the catalog-only search assistant."""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for path in (os.path.dirname(_HERE), _HERE):
    if path not in sys.path:
        sys.path.insert(0, path)


CASES = [
    {
        "query": "عايز كورسات دكتور أحمد",
        "why": "course result filtered by doctor name",
        "expect": {"target": "courses", "filters": [("users", "name")]},
    },
    {
        "query": "show me the newest anatomy books",
        "why": "book metadata search with newest ordering",
        "expect": {"target": "books", "text": True, "sort": "newest"},
    },
    {
        "query": "مين الدكاترة اللي عندهم كورسات؟",
        "why": "doctors are users, but students can never be returned",
        "expect": {"target": "users"},
    },
    {
        "query": "عايز أقسام طب بالعربي",
        "why": "Arabic category search",
        "expect": {"target": "categories"},
    },
    {
        "query": "المراحل التعليمية للثانوي",
        "why": "school levels use the unified educational-level target",
        "expect": {"target": "educational_levels"},
    },
    {
        "query": "college levels for the faculty of medicine",
        "why": "college levels share the same safe result shape",
        "expect": {"target": "educational_levels"},
    },
    {
        "query": "beginner English courses for year 2",
        "why": "canonical course language, level, and academic-year filters",
        "expect": {"target": "courses", "filters": [("courses", "academic_year")]},
    },
    {
        "query": "عايز كتاب",
        "why": "a supported but empty request needs one clarification",
        "expect": {"outcome": "clarify", "clarify": True},
    },
    {
        "query": "عايز المحاضرة اللي بتشرح القلب",
        "why": "lectures are outside the search boundary",
        "expect": {"outcome": "unsupported", "reason": True},
    },
    {
        "query": "سنة تانية",
        "history": [
            ("user", "عايز كورسات"),
            ("model", '{"intent":"clarify","clarify":"كورسات لسنة كام؟"}'),
        ],
        "why": "a clarification answer keeps the supported course context",
        "expect": {"target": "courses", "filters": [("courses", "academic_year")]},
    },
]


def check(case, out):
    want = case["expect"]
    result_plan = out.get("plan") or {}
    failures = []

    expected_outcome = want.get("outcome")
    if expected_outcome and out["outcome"] != expected_outcome:
        failures.append(f"outcome {out['outcome']!r}, wanted {expected_outcome!r}")
    if "target" in want and result_plan.get("target") != want["target"]:
        failures.append(f"target {result_plan.get('target')!r}, wanted {want['target']!r}")

    got = {(item["table"], item["column"]) for item in result_plan.get("filters", [])}
    for pair in want.get("filters", []):
        if pair not in got:
            failures.append(f"no filter on {pair[0]}.{pair[1]}")

    if want.get("text") and not result_plan.get("text"):
        failures.append("no metadata search text")
    if "sort" in want and result_plan.get("sort") != want["sort"]:
        failures.append(f"sort {result_plan.get('sort')!r}, wanted {want['sort']!r}")
    if want.get("clarify") and not out.get("clarify"):
        failures.append("no clarification question")
    if want.get("reason") and not out.get("reason"):
        failures.append("no unsupported reason")
    return failures


def main(argv):
    import search

    picked = [int(number) for number in argv] or list(range(1, len(CASES) + 1))
    failed = 0

    for number in picked:
        case = CASES[number - 1]
        out = search.answer(case["query"], history=case.get("history"))
        failures = check(case, out)
        failed += bool(failures)
        print(f"{'FAIL' if failures else 'ok  '} {number:>2}. {case['query']}")
        print(f"       {case['why']}")
        for failure in failures:
            print(f"       x {failure}")

    print(f"\n{len(picked) - failed}/{len(picked)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
