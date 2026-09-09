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

import re

from pydantic import BaseModel, Field


ANSWER_PROMPT_VERSION = "lecture-answer-v3-language-mirroring"
REWRITE_PROMPT_VERSION = "followup-rewrite-v1"
SUMMARY_PROMPT_VERSION = "conversation-summary-v1"


class TutorReply(BaseModel):
    """What the model must return. Enforced as a response schema."""

    found: bool = Field(
        description="true only if the excerpts actually answer the question"
    )
    answer: str = Field(
        description="the simplified answer, matching the latest student's language "
                    "and, for Arabic, their dialect or register"
    )
    used_excerpts: list[int] = Field(
        default_factory=list,
        description="numbers of the excerpts the answer was built from",
    )


class StandaloneQueryReply(BaseModel):
    standalone_query: str = Field(
        description="The retrieval question only, in the student's language"
    )


class ConversationSummaryReply(BaseModel):
    summary: str = Field(description="Bounded conversational state, not evidence")


SYSTEM_INSTRUCTION = """\
You are an educational assistant for medical students. Your only job is to explain
the lecturer's material simply.

MANDATORY LANGUAGE AND VOICE RULE:
- Match the language of the student's LATEST ORIGINAL QUESTION, not the transcript,
  standalone retrieval query, summary, or earlier turns.
- If that question is in English, answer entirely in English.
- If it is in Arabic, answer in Arabic and mirror the student's dialect and register:
  Egyptian Arabic for Egyptian wording, Modern Standard Arabic for formal Arabic,
  and the corresponding dialect for other Arabic dialects. Never default every
  Arabic student to Egyptian Arabic.
- If the student naturally mixes Arabic and English, mirror that style naturally,
  especially for medical terminology. Do not translate their voice into a different
  dialect or a more formal/informal register.

GROUNDING RULES:
1. Use ONLY the supplied transcript excerpts. Never add outside medical knowledge,
   even when you know it is correct.
2. If the excerpts do not contain enough evidence, clearly say that this part is not
   covered in the lecture and invite a different question, using the same language
   and dialect as the student's latest question. Never guess.
3. Simplify the lecturer's explanation and preserve the order of their ideas.
4. Include the lecturer's own examples and memory aids when they appear in an excerpt.
5. Preserve medical terms as the lecturer used them. Add a parenthetical English term
   only when it genuinely helps the explanation.
6. Keep the response concise: 3 to 6 sentences or bullet points.
7. Cite every factual statement with its excerpt number, such as [1] or [2].
8. Never invent timestamps; the application provides video navigation separately.

Return JSON matching this contract:
- found: true only when the excerpts genuinely answer the question.
- answer: the simplified answer, or the same-language/dialect refusal when false.
- used_excerpts: only the excerpt numbers actually used in the answer.
"""

REWRITE_SYSTEM_INSTRUCTION = """\
You rewrite the student's latest message into a standalone transcript-search query.
Resolve references such as it, that point, why, the previous type, وده، وليه from
the supplied conversation state. Preserve the student's language where practical.
Do not answer, explain, or introduce medical facts. If the question is already
standalone, return it unchanged. Return only the structured standalone_query.
"""

SUMMARY_SYSTEM_INSTRUCTION = """\
Summarize conversation state for resolving later follow-ups. Preserve only topics,
pronoun referents, student confusion, distinctions already discussed, and unresolved
questions. Do not add medical facts, do not copy full answers, and never describe
the summary as evidence. Return only the structured summary.
"""

NOT_IN_LECTURE = (
    "الجزء ده مش موجود في المحاضرة دي. "
    "جرّب تسأل عن نقطة اتشرحت فيها، وأنا هوديك على مكانها في الفيديو."
)

NOT_IN_LECTURE_AR = (
    "هذا الجزء غير مذكور في هذه المحاضرة. "
    "جرّب أن تسأل عن نقطة شُرحت فيها، وسأرشدك إلى موضعها في الفيديو."
)

NOT_IN_LECTURE_EN = (
    "This part is not covered in this lecture. "
    "Try asking about a topic explained in it, and I’ll point you to the right "
    "place in the video."
)

LLM_DOWN = (
    "لقيت مكان الإجابة في المحاضرة وحطيتهولك على الفيديو تحت 👇 "
    "بس الشرح المبسّط مش متاح دلوقتي، اسمع كلام الدكتور نفسه من المقطع."
)

LLM_DOWN_AR = (
    "وجدتُ موضع الإجابة في المحاضرة وأرفقتُ مقطع الفيديو أدناه، لكن الشرح "
    "المبسّط غير متاح مؤقتاً. يمكنك الاستماع إلى شرح المحاضر في ذلك المقطع."
)

LLM_DOWN_EN = (
    "I found the relevant place in the lecture and linked the video segment below, "
    "but the simplified explanation is temporarily unavailable. You can still hear "
    "the lecturer’s explanation in that segment."
)


_ARABIC_CHARACTER = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")
_EGYPTIAN_MARKER = re.compile(
    r"(?:^|\s)(?:إيه|ايه|إزاي|ازاي|ليه|عايز|عاوز|ده|دي|دول|مش|مفيش|فين|"
    r"كده|بتاع|قولي|اشرحلي)(?:\s|$|[؟?!،,.])"
)


def uses_arabic(text):
    """Whether the latest student message is written using Arabic script."""

    return bool(_ARABIC_CHARACTER.search(text or ""))


def uses_egyptian_arabic(text):
    """Recognize common Egyptian markers for deterministic fallback copy."""

    return bool(_EGYPTIAN_MARKER.search(text or ""))


def not_in_lecture(question):
    if not uses_arabic(question):
        return NOT_IN_LECTURE_EN
    return NOT_IN_LECTURE if uses_egyptian_arabic(question) else NOT_IN_LECTURE_AR


def llm_down(question):
    if not uses_arabic(question):
        return LLM_DOWN_EN
    return LLM_DOWN if uses_egyptian_arabic(question) else LLM_DOWN_AR


def cross_video_notice(question):
    if uses_egyptian_arabic(question):
        return "الإجابة دي من فيديو تاني مرتبط بنفس الكورس."
    if uses_arabic(question):
        return "هذه الإجابة من فيديو آخر مرتبط بالمقرر نفسه."
    return "This answer comes from another video in the same course."


def assistant_unavailable_notice(question):
    if uses_egyptian_arabic(question):
        return "المساعد الذكي مش متاح دلوقتي — الفيديو والمقاطع شغالة عادي."
    if uses_arabic(question):
        return "المساعد الذكي غير متاح حالياً، لكن الفيديو والمقاطع ما زالت متاحة."
    return (
        "The AI assistant is temporarily unavailable, but the video and its "
        "segments are still available."
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
            f"من فيديو «{passage.video_title}»:\n{passage.text}"
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


def build_rewrite_prompt(question, history, summary=""):
    history_text = "\n".join(
        f"{role}: {content}" for role, content in history
    ) or "(none)"
    return (
        f"Previous bounded summary:\n{summary or '(none)'}\n\n"
        f"Recent conversation:\n{history_text}\n\n"
        f"Latest student message:\n{question}\n\n"
        "Return the standalone retrieval query without answering it."
    )


def build_conversational_prompt(question, standalone_query, passages, summary=""):
    return (
        "Conversation summary (context only; NEVER evidence):\n"
        f"{summary or '(none)'}\n\n"
        "Transcript excerpts (the ONLY medical evidence):\n\n"
        f"{build_context(passages)}\n\n"
        "----\n"
        f"Original student question: {question}\n"
        f"Standalone retrieval query: {standalone_query}\n\n"
        "Answer the original question using only the transcript excerpts. "
        "Use conversation memory only to understand references. Match the language, "
        "Arabic dialect, code-switching, and level of formality of the ORIGINAL "
        "student question exactly; other text in this prompt must not influence the "
        "response language."
    )


def build_summary_prompt(previous_summary, messages):
    conversation = "\n".join(
        f"{role}: {content}" for role, content in messages
    )
    return (
        f"Previous summary:\n{previous_summary or '(none)'}\n\n"
        f"New conversation to incorporate:\n{conversation}\n\n"
        "Produce the bounded updated conversational-state summary."
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

    checkpoints = report.get("checkpoints")
    mastery = report.get("mastery")
    progress = report.get("progress")
    retention = report.get("retention")
    trend = report.get("trend", {})
    if checkpoints:
        lines += [
            "",
            "نقاط التحقق داخل الفيديو:",
            f"- ظهر {checkpoints['shown']}، اتجاوب {checkpoints['answered']}، "
            f"صح {checkpoints['correct']}، الدقة {_percent(checkpoints['accuracy'])}.",
            f"- الإكمال {_percent(checkpoints['completion'])}، التخطي "
            f"{_percent(checkpoints['skip_rate'])}، انتهاء الوقت "
            f"{_percent(checkpoints['timeout_rate'])}، وسيط زمن الإجابة "
            f"{checkpoints['median_response_time_seconds']} ثانية.",
        ]
    if mastery:
        overall = mastery["overall"]
        lines.append(
            f"- الإتقان المحسوب: {_percent(overall['score'])}، "
            f"مستوى الدليل {overall['confidence']} من {overall['evidence_items']} عنصر مقيم."
        )
    if progress and progress["status"] == "AVAILABLE":
        lines.append(
            f"- التقدم المطلوب حتى الآن {_percent(progress['expected_percentage'])}، "
            f"الفعلي {_percent(progress['actual_percentage'])}، الفارق "
            f"{progress['gap_pp']} نقطة مئوية."
        )
    if retention:
        if retention["status"] == "AVAILABLE":
            lines.append(
                f"- الاحتفاظ المتأخر {_percent(retention['delayed_retention'])}، "
                f"التغير {retention['retention_drop_pp']} نقطة مئوية."
            )
        else:
            lines.append("- الاحتفاظ: بيانات إعادة التقييم المتأخر غير كافية.")
    if trend:
        lines.append("")
        lines.append("مقارنة بالأسبوع السابق (السابق ← الحالي، والتغير):")
        for key, value in trend.items():
            lines.append(
                f"- {key}: {value['previous']} ← {value['current']} "
                f"(التغير {value['delta']})"
            )

    if report.get("action_plan"):
        lines.append("")
        lines.append("أولويات محسوبة حتمياً — اختصرها واشرحها ولا تضف رقماً:")
        for item in report["action_plan"]:
            lines.append(f"- {item['action']} السبب: {item['evidence']}")

    return "\n".join(lines)
