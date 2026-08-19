"""Post-exam statistics: the shaping rules around the SQL aggregation."""

from app.services import exam_stats


def test_a_percentage_with_no_denominator_is_none_not_zero():
    """"Nobody answered" and "everybody got it wrong" are opposite findings."""

    assert exam_stats._percent(0, 10) == 0.0
    assert exam_stats._percent(3, 4) == 75.0
    assert exam_stats._percent(0, 0) is None
    assert exam_stats._percent(5, None) is None


def test_difficulty_calibration_flags_a_mislabelled_question():

    assert exam_stats._calibration("hard", 96.0) == "easier_than_labelled"
    assert exam_stats._calibration("easy", 20.0) == "harder_than_labelled"
    assert exam_stats._calibration("hard", 40.0) == "as_labelled"
    assert exam_stats._calibration("easy", 90.0) == "as_labelled"
    assert exam_stats._calibration("medium", 55.0) == "as_labelled"


def test_calibration_says_nothing_without_a_label_or_a_result():
    """Silence beats a verdict invented from a missing input."""

    assert exam_stats._calibration(None, 90.0) is None
    assert exam_stats._calibration("", 90.0) is None
    assert exam_stats._calibration("hard", None) is None


def test_calibration_ignores_case_and_padding():

    assert exam_stats._calibration("  HARD ", 96.0) == "easier_than_labelled"


def test_distribution_covers_every_score_exactly_once():
    """Fifths are half-open so 60 lands above the pass mark, not below it."""

    buckets = exam_stats._distribution([0, 19.9, 20, 40, 59.9, 60, 80, 99.9, 100])

    #        0-20      20-40   40-60        60-80   80-100
    #      0, 19.9 |      20 | 40, 59.9 |      60 | 80, 99.9, 100
    assert [b["students"] for b in buckets] == [2, 1, 2, 1, 3]
    assert sum(b["students"] for b in buckets) == 9


def test_distribution_puts_full_marks_in_the_top_bucket():
    """100 must not fall off the end of a half-open range."""

    buckets = exam_stats._distribution([100.0])

    assert buckets[-1]["students"] == 1
    assert sum(b["students"] for b in buckets) == 1


def test_an_empty_cohort_distributes_to_nothing():

    assert [b["students"] for b in exam_stats._distribution([])] == [0, 0, 0, 0, 0]


# --- distractor analysis -------------------------------------------------


OPTIONS = ["A) Anterior and posterior", "B) Superior and inferior",
           "C) Right and left", "D) Medial and lateral"]


def test_option_letter_is_read_off_the_stored_text():
    """Options are stored already lettered, so the letter comes from the text."""

    assert exam_stats._option_letter("C) Right and left") == "C"
    assert exam_stats._option_letter("  d. something ") == "D"
    assert exam_stats._option_letter("A: first") == "A"
    assert exam_stats._option_letter("no letter here") is None
    assert exam_stats._option_letter("") is None
    assert exam_stats._option_letter(None) is None


def test_every_option_is_listed_even_when_nobody_picked_it():
    """A distractor nobody touches means the question is really a 3-way choice."""

    rows, total = exam_stats._distractors([("A", 4, 4), ("C", 2, 2)], "C", OPTIONS)

    assert [r["option"] for r in rows] == ["A", "B", "C", "D"]
    assert [r["picks"] for r in rows] == [4, 0, 2, 0]
    assert total == 6
    assert [r["is_correct"] for r in rows] == [False, False, True, False]
    assert rows[0]["percent"] == 66.7


def test_a_choice_matching_no_option_is_shown_not_dropped():
    """A renumbered question, or a client sending something unexpected."""

    rows, total = exam_stats._distractors([("C", 1, 1), ("Z", 2, 2)], "C", OPTIONS)

    stray = [r for r in rows if r["option"] == "Z"]

    assert len(stray) == 1
    assert stray[0]["text"] is None
    assert stray[0]["picks"] == 2
    assert total == 3


def test_no_recorded_choices_gives_an_empty_distribution_not_zeroes():
    """Attempts predating the column must not read as "nobody chose anything"."""

    rows, total = exam_stats._distractors([], "C", OPTIONS)

    assert total == 0
    assert all(r["picks"] == 0 for r in rows)
    assert all(r["percent"] is None for r in rows)


def test_a_dominant_distractor_is_reported():
    """Half the class on one wrong option is a finding, not noise."""

    rows, _ = exam_stats._distractors([("A", 4, 4), ("C", 2, 2), ("B", 2, 2)], "C", OPTIONS)

    top = exam_stats._top_distractor(rows)

    assert top["option"] == "A"
    assert top["percent"] == 50.0


def test_wrong_answers_spread_evenly_are_not_a_distractor_finding():
    """Three distractors at 12% each is a hard question, not a broken one."""

    rows, _ = exam_stats._distractors(
        [("C", 13, 13), ("A", 2, 2), ("B", 2, 2), ("D", 2, 2)], "C", OPTIONS
    )

    assert exam_stats._top_distractor(rows) is None


def test_a_perfect_question_has_no_top_distractor():

    rows, _ = exam_stats._distractors([("C", 6, 6)], "C", OPTIONS)

    assert exam_stats._top_distractor(rows) is None
