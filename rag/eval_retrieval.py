"""Sanity-check retrieval (and optionally the tutor's answer) from the CLI.

    python -m rag.eval_retrieval                 # retrieval only, no LLM call
    python -m rag.eval_retrieval --with-answer   # full RAG path
    python -m rag.eval_retrieval -q "سؤالك هنا"

Questions below deliberately avoid the lecture's own wording, so a pass means
the embeddings generalise instead of matching keywords.
"""

import argparse
import sys

from app.config import get_settings
from app.db import close_pool, connection
from app.services import retrieval
from app.services.embeddings import Embedder
from app.services.prompts import to_stamp
from app.services.tutor import TutorService


QUESTIONS = [
    "لو بصّيت على عظمة ووجدت إن سطحها الخارجي مش بالضرورة مسطح أو منتظم، لكن "
    "تركيبها الداخلي فيه تجاويف هوائية مرتبطة بالجيوب الأنفية، إيه التصنيف اللي "
    "هتنتمي له؟ وليه ماينفعش أصنفها ضمن التصنيفات التانية اللي اتشرحت؟",

    "فيه عظمة ممكن تكون شكلها من الأمام بسيط وسطحها ناعم، لكن لو بصّيت عليها من "
    "الناحية التانية هتلاقي نتوء عظمي واضح. هل ده يمنع تصنيفها ضمن أحد أنواع "
    "العظام حسب الشكل؟ وضّح من المثال المذكور في المحاضرة.",

    "لو أردنا تصنيف عظمة صغيرة موجودة في منطقة اليد اعتمادًا على أبعادها، وليس "
    "على تركيبها أو وجود تجاويف بداخلها، فإلى أي فئة يمكن أن تنتمي؟ وما المثال "
    "الذي ذكره المحاضر، وما الفئة التي قد يختلط الأمر بينها وبينها؟",

    # Off-topic on purpose: this must come back ungrounded, not invented.
    "ايه اكتر حاجة بتعلي خطر الاصابة بالستروك؟",
]


def parse_args(argv=None):

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("-q", "--question", action="append", dest="questions")
    parser.add_argument("--lecture-id", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument(
        "--with-answer",
        action="store_true",
        help="also call the LLM and print the grounded answer",
    )

    return parser.parse_args(argv)


def main(argv=None):

    args = parse_args(argv)
    settings = get_settings()

    questions = args.questions or QUESTIONS

    embedder = Embedder()
    tutor = TutorService(embedder=embedder) if args.with_answer else None

    try:
        with connection() as conn:

            for question in questions:

                print("\n" + "=" * 72)
                print(f"Q: {question}")
                print("=" * 72)

                passages = retrieval.search(
                    conn,
                    embedder.embed_query(question),
                    top_k=args.top_k or settings.top_k,
                    lecture_id=args.lecture_id,
                )

                relevant = retrieval.keep_relevant(passages)

                for passage in passages:

                    mark = " " if passage in relevant else "x"

                    print(
                        f"\n{mark} #{passage.chunk_id}  "
                        f"{to_stamp(passage.start_ts)} --> {to_stamp(passage.end_ts)}  "
                        f"distance {passage.distance:.4f}"
                    )
                    print("  " + passage.text[:220] + " ...")

                for segment in retrieval.to_segments(relevant):
                    print(
                        f"\n  -> play {to_stamp(segment.start_ts)} "
                        f"and flag at {to_stamp(segment.end_ts)}"
                    )

                if not relevant:
                    print("\n  -> nothing within the distance cut-off "
                          f"({settings.max_distance}) — tutor will refuse")

                if tutor:
                    result = tutor.ask(
                        conn,
                        question,
                        lecture_id=args.lecture_id,
                    )
                    print(f"\n  grounded={result.grounded}\n")
                    print(result.answer)

    finally:
        close_pool()

    return 0


if __name__ == "__main__":
    sys.exit(main())
