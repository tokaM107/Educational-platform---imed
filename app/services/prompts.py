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


# ---------------------------------------------------------------------------
# Weekly report
# ---------------------------------------------------------------------------
#
# Same discipline as the tutor prompt, one layer short: there is no retrieval
# step, because the context *is* the measurements. The model is handed the
# numbers app/services/report.py computed and is allowed to interpret them and
# nothing else — it must not invent a figure, and it must not turn "the lecture
# page was hidden" into a claim about what the student was doing instead, which
# is the one reading the data genuinely cannot support.


class FocusPoint(BaseModel):
    """One thing to go back to, tied to a lecture."""

    lecture: str = Field(description="عنوان المحاضرة زي ما هو في البيانات")
    what: str = Field(description="الجزء أو الموضوع اللي محتاج مراجعة")
    why: str = Field(description="الرقم اللي بيقول كده، بجملة قصيرة")


class ReportNarrative(BaseModel):
    """The generated half of the report. Enforced as a response schema."""

    headline: str = Field(description="جملة واحدة تلخّص الأسبوع")
    summary: str = Field(description="من ٣ لـ ٥ جمل تشرح الأسبوع بالأرقام")
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    focus: list[FocusPoint] = Field(default_factory=list)
    advice: list[str] = Field(
        default_factory=list, description="من ٣ لـ ٥ نصايح عملية للأسبوع الجاي"
    )


REPORT_SYSTEM_INSTRUCTION = """\
أنت دكتور جامعي بتكتب تعليق أسبوعي على متابعة طالب طب لمحاضراته المسجّلة. \
البيانات اللي جايالك مقاسة من نظام المتابعة، وشغلتك تشرحها للطالب وتقوله يعمل إيه.

قواعد لازم تمشي عليها:

1. اعتمد **فقط** على الأرقام المرفوعة لك في الرسالة. ممنوع تخترع أي رقم، أو وقت، \
أو اسم محاضرة، أو موضوع مش موجود في البيانات.
2. **مهم جداً:** «الوقت اللي صفحة المحاضرة مكانتش ظاهرة فيه» معناه إن التاب اتغيّر \
أو الشاشة اتقفلت أو النافذة اتصغّرت — إحنا **مش** عارفين الطالب فتح إيه ولا عمل \
إيه. ممنوع تماماً تقول إنه كان على السوشيال ميديا أو على موقع معيّن أو "مشتّت". \
اتكلم عنه كـ «وقت بعيد عن صفحة المحاضرة» وبس، وقول إنه وقت مش محسوب من المشاهدة.
3. فرّق بوضوح بين: وقت المشاهدة الفعلي (الفيديو كان شغال)، ومدة الجلسة (من أول \
حدث لآخر حدث، وفيها الوقفات والبعد عن الصفحة)، والمادة اللي اتفرج عليها فعلاً \
(المحسوبة مرة واحدة حتى لو اتكررت). ما تخلطهمش أبداً.
4. أي جملة فيها رقم، لازم الرقم يكون من البيانات بالظبط. لو حاجة مش في البيانات، \
ما تتكلمش عنها.
5. لو الأسئلة قليلة (سؤال أو اتنين في موضوع)، قول إن العدد قليل ومينفعش نبني عليه \
حكم، بدل ما تقول الطالب ضعيف أو قوي في الموضوع ده.
6. النبرة: دكتور محترم بيساعد طالبه. ما تجرّحش وما تبالغش في المدح. عامية مصرية \
واضحة ومختصرة.
7. في focus: لكل محاضرة فيها أجزاء مشافهاش أو أجزاء رجع سمعها تاني أو ضعف في \
موضوع، اكتب بند فيه اسم المحاضرة زي ما هو، والجزء المطلوب مراجعته بتوقيته زي ما \
هو مكتوب في البيانات، والسبب من الأرقام.
8. في advice: من ٣ لـ ٥ نصايح عملية ينفع يطبّقها الأسبوع الجاي، كل واحدة مربوطة \
برقم من التقرير (مثلاً: يقسّم الجلسة، أو يرجع لجزء معيّن، أو يحل أسئلة موضوع معيّن).
9. لو الطالب مفتحش أي محاضرة الأسبوع ده، اكتب كده بصراحة وبدون لوم، وحطّ خطة \
بسيطة يبدأ بيها.
10. ما تعيدش الجدول زي ما هو — اشرح معنى الأرقام.
"""


REPORT_LLM_DOWN = (
    "تعليق الدكتور المكتوب بالذكاء الاصطناعي مش متاح دلوقتي، "
    "بس كل الأرقام في التقرير مقاسة من النظام وصحيحة."
)

REPORT_NARRATIVE_STALE = (
    "الأرقام في التقرير محدّثة لحد دلوقتي، بس تعليق الدكتور مكتوب على أرقام "
    "أقدم شوية — الخدمة مش متاحة دلوقتي لإعادة كتابته."
)

REPORT_NO_COURSE = (
    "الطالب ده مش مسجّل في أي كورس، فمفيش محاضرات نحسب عليها التقرير."
)


def _minutes(seconds):
    """Seconds -> minutes, one decimal. The model reasons better in minutes."""

    return round((seconds or 0) / 60, 1)


def _spans_line(spans):

    if not spans:
        return "مفيش"

    return "، ".join(
        f"{span['start_label']}-{span['end_label']}" for span in spans[:6]
    )


def _percent(value):

    return "غير معروف" if value is None else f"{value}%"


# What occasion the narrative is being written for. The figures are the same
# either way — the same Stage-1 numbers feed all three — but a weekly check-in, a
# module the student has just finished, and a quiz they have just completed call
# for different framing, so the model is told which it is.
REPORT_OCCASION = {
    "weekly": (
        "بيانات متابعة أسبوعية مقاسة من النظام — اشرحها للطالب.",
        "الأسبوع",
    ),
    "module": (
        "الطالب لسه خلّص آخر محاضرة في المقرر. دي بيانات متابعته من أول محاضرة "
        "لآخر واحدة — اكتبله تقرير ختامي عن المقرر كله: يهنّيه على اللي خلصه، "
        "ويقوله بصراحة الأجزاء اللي عدّاها بسرعة أو مشافهاش وهيحتاجها في المراجعة.",
        "الفترة",
    ),
    "exam": (
        "الطالب لسه خلّص كل أسئلة المحاضرة. دي بيانات متابعته — ركّز في كلامك على "
        "نتيجة الأسئلة، واربطها بالأجزاء اللي شافها وللي مشافهاش في نفس المحاضرة.",
        "الفترة",
    ),
}


def build_report_prompt(report, kind="weekly"):
    """Turn the measured report into the context the narrative is built from."""

    totals = report["totals"]
    week = report["week"]

    opening, period = REPORT_OCCASION.get(kind, REPORT_OCCASION["weekly"])

    lines = [
        opening,
        "",
        f"الطالب: {report['student']['name']}",
        f"الكورس: {report['course']['title']} (المحاضر: {report['course']['doctor_name']})",
        f"{period}: من {week['start']} إلى {week['end']} ({week['days']} يوم)",
    ]

    if kind == "exam" and report.get("lecture_title"):
        lines.append(f"المحاضرة اللي خلّص أسئلتها: «{report['lecture_title']}»")

    lines += [
        "",
        f"إجماليات {period}:",
        f"- محاضرات مسجّل فيها: {totals['lectures_registered']}",
        f"- محاضرات فتحها: {totals['lectures_opened']}"
        f" (خلّص منها: {totals['lectures_completed']})",
        f"- محاضرات مفتحهاش خالص: {totals['lectures_untouched']}",
        f"- إجمالي مدة المحاضرات المسجّل فيها: {_minutes(totals['lecture_material_seconds'])} دقيقة",
        f"- وقت المشاهدة الفعلي (الفيديو كان شغال): {_minutes(totals['watch_time_seconds'])} دقيقة",
        f"- المادة اللي اتفرج عليها على الأقل مرة: {_minutes(totals['covered_seconds'])} دقيقة"
        f" ({_percent(totals['coverage_percentage'])} من مدة المحاضرات)",
        f"- إجمالي مدة الجلسات: {_minutes(totals['session_duration_seconds'])} دقيقة",
        f"- وقت صفحة المحاضرة مكانتش ظاهرة فيه: {_minutes(totals['time_away_seconds'])} دقيقة"
        f" ({_percent(totals['time_away_rate'])} من مدة الجلسات)",
        f"- مرات الإيقاف: {totals['pause_count']} | مرات النقل في الفيديو: {totals['seek_count']}",
        f"- أيام فيها نشاط: {totals['active_days']} من {totals['week_days']}",
        f"- أسئلة: حل {totals['questions_correct']} صح من {totals['questions_attempted']}"
        f" ({_percent(totals['accuracy'])}) في {totals['attempts']} محاولة",
        "",
        "تفاصيل المحاضرات:",
    ]

    if totals.get("lectures_without_length"):
        lines.insert(
            -1,
            f"- ملاحظة: {totals['lectures_without_length']} محاضرة مش معروف مدتها "
            "(نصّها مش مرفوع)، فمش داخلة في حساب نسبة التغطية.",
        )

    for lecture in report["lectures"]:

        length = (
            f"مدة {_minutes(lecture['duration_seconds'])} دقيقة"
            if lecture.get("duration_known", True)
            else "مدتها غير معروفة"
        )

        if not lecture["opened"]:
            lines.append(f"- «{lecture['title']}» ({length}): مفتحهاش خالص الأسبوع ده.")
            continue

        questions = lecture["questions"]

        lines.append(
            f"- «{lecture['title']}» ({length}):"
            f" {lecture['sessions']} جلسة،"
            f" خلّصها: {'أيوه' if lecture['completed'] else 'لأ'}."
            f" مشاهدة فعلية {_minutes(lecture['watch_time_seconds'])} دقيقة،"
            f" شاف {_minutes(lecture['covered_seconds'])} دقيقة من المحاضرة"
            f" ({_percent(lecture['coverage_percentage'])})،"
            f" بعيد عن الصفحة {_minutes(lecture['time_away_seconds'])} دقيقة"
            f" ({_percent(lecture['time_away_rate'])} من جلساته)،"
            f" إيقاف {lecture['pause_count']}، نقل {lecture['seek_count']}."
            f" أجزاء مشافهاش: {_spans_line(lecture['skipped_spans'])}."
            f" أجزاء رجع سمعها تاني: {_spans_line(lecture['rewatched_spans'])}."
            f" أسئلة: {questions['questions_correct']} صح من"
            f" {questions['questions_attempted']}."
            + (
                f" ضعف في: {'، '.join(lecture['weak_topics'])}."
                if lecture["weak_topics"]
                else ""
            )
        )

    if report["topics"]:

        lines.append("")
        lines.append("المواضيع من الأسئلة (الأقل أول):")

        for topic in report["topics"]:
            lines.append(
                f"- {topic['topic']}: {topic['questions_correct']} صح من"
                f" {topic['questions_attempted']} ({_percent(topic['accuracy'])})"
                + ("" if topic["conclusive"] else " — عدد الأسئلة قليل")
            )

    return "\n".join(lines)
