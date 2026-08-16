"""Prompt building and the model's reply contract.

Anti-hallucination has three layers:

  1. retrieval.py drops anything past a coarse distance cut-off, so obviously
     unrelated chunks never reach the model;
  2. the model only ever sees transcript excerpts and must answer from them;
  3. it returns `found` and `used_excerpts` as structured JSON, so an
     off-topic question ends as an honest refusal with no video segment —
     distance alone cannot tell the two apart (measured on this lecture:
     on-topic 0.25-0.31, off-topic 0.39).
"""

from pydantic import BaseModel, Field


class TutorReply(BaseModel):
    """What the model must return. Enforced as a response schema."""

    found: bool = Field(
        description="true only if the excerpts actually answer the question"
    )
    answer: str = Field(description="the simplified answer, in Egyptian Arabic")
    used_excerpts: list[int] = Field(
        default_factory=list,
        description="numbers of the excerpts the answer was built from",
    )


SYSTEM_INSTRUCTION = """\
أنت مساعد تعليمي لطلبة كليات الطب، وشغلتك الوحيدة إنك تشرح كلام دكتور المحاضرة \
بشكل مبسط.

قواعد لازم تمشي عليها:

1. اعتمد **فقط** على "مقاطع المحاضرة" المرفقة في الرسالة. ممنوع تضيف أي معلومة \
من برة المحاضرة، حتى لو كنت متأكد إنها صح طبياً.
2. لو المقاطع مفيهاش إجابة كافية، قول بوضوح: "الجزء ده مش موجود في المحاضرة" \
واقترح على الطالب يسأل سؤال تاني. ممنوع التخمين.
3. بسّط شرح الدكتور بلغة سهلة، وحافظ على نفس ترتيب الفكرة اللي شرحها بيها.
4. **لازم** تذكر أمثلة الدكتور نفسه وطرق التذكر اللي استخدمها (زي ما بيشبّه عظمة \
العضد بالـ hammer)، لأن دي اللي الطالب هيفتكرها في الامتحان. لو المقطع فيه مثال، \
اذكره بنص كلام الدكتور تقريباً.
5. المصطلح الطبي اكتبه زي ما الدكتور نطقه، وحطّ جنبه الإنجليزي بين قوسين لما تعرفه.
6. رد بالعامية المصرية، مختصر: من ٣ لـ ٦ جمل أو نقاط.
7. بعد كل معلومة حطّ رقم المقطع اللي جبتها منه كده [1] أو [2].
8. ما تخترعش أرقام دقايق أو ثواني — الطالب هيشوف الفيديو نفسه.

الرد لازم يكون JSON بالشكل ده:

- found: تحطها true بس لو المقاطع فيها إجابة حقيقية للسؤال. لو السؤال عن موضوع \
تاني خالص مش في المحاضرة، حطها false.
- answer: الإجابة المبسطة (أو جملة الاعتذار لو found = false).
- used_excerpts: أرقام المقاطع اللي بنيت عليها الإجابة فعلاً — مش كل المقاطع \
اللي اتعرضت عليك، بس اللي استخدمتها.
"""

NOT_IN_LECTURE = (
    "الجزء ده مش موجود في المحاضرة دي. "
    "جرّب تسأل عن نقطة اتشرحت فيها، وأنا هوديك على مكانها في الفيديو."
)

LLM_DOWN = (
    "لقيت مكان الإجابة في المحاضرة وحطيتهولك على الفيديو تحت 👇 "
    "بس الشرح المبسّط مش متاح دلوقتي، اسمع كلام الدكتور نفسه من المقطع."
)


def to_stamp(seconds):
    """Seconds -> HH:MM:SS."""

    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)

    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def build_context(passages):
    """Number the excerpts so the model can cite them as [1], [2], ..."""

    blocks = []

    for index, passage in enumerate(passages, start=1):

        blocks.append(
            f"[{index}] ({to_stamp(passage.start_ts)} - {to_stamp(passage.end_ts)}) "
            f"من محاضرة «{passage.lecture_title}»:\n{passage.text}"
        )

    return "\n\n".join(blocks)


def build_user_prompt(question, passages):

    return (
        "مقاطع المحاضرة:\n\n"
        f"{build_context(passages)}\n\n"
        "----\n"
        f"سؤال الطالب: {question}\n\n"
        "جاوب من المقاطع اللي فوق بس، وبسّط شرح الدكتور، "
        "واذكر أمثلته وطرق التذكر بتاعته."
    )
