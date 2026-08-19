"""A synthetic multi-course dataset for testing the platform end to end.

    python -m scripts.seed_test_data --dry-run   # plan only, write nothing
    python -m scripts.seed_test_data             # insert, then fire the report triggers
    python -m scripts.seed_test_data --no-reports
    python -m scripts.seed_test_data --remove    # take it all back out

Everything written here is *test* data and says so: users are `test.*@example.com`,
and every course, lecture and topic title carries a `[TEST]` prefix, so nothing
can be mistaken for real teaching material in the UI or in a report. People's
names are realistic because a report that reads "Student 7" tests nothing about
how the page handles real names.

What it does NOT do, on purpose:

  query_embeddings   `embedding` is NOT NULL vector(1536). There is no honest way
                     to invent one, and a random vector would poison the
                     similarity search. Left alone entirely.
  transcript_chunks  one marker row per synthetic lecture, `embedding NULL`. A
                     lecture's length comes from `MAX(end_ts)` and without it the
                     engagement analytics have no denominator, so this row is
                     what makes coverage measurable. Retrieval filters on
                     `embedding IS NOT NULL`, so it never reaches the tutor.
  lectures.video_url left NULL rather than pointing at a file that is not there.
  reports /          not inserted. The application creates these, through
  notifications      app.services.triggers, and seeding them by hand would test
                     the seed rather than the product. The events and attempts
                     below are arranged so the real triggers fire, and this
                     script then calls them.

Existing rows are never touched: inserts are additive, and --remove deletes only
rows carrying the test marker.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from app.db import connection


MARKER = "[TEST]"
EMAIL_DOMAIN = "example.com"

# Sessions are laid down over the last fortnight, with most of the activity in
# the last seven days so a weekly report has something inside its window while
# the module reports still have a history behind them.
DAYS = 14


# Two of the three share the first name أحمد, and both teach Physiology, so
# "عايز محاضرات دكتور أحمد في الفسيولوجي" is genuinely ambiguous and the
# assistant has to ask which one rather than guessing.
DOCTORS = [
    ("test.doctor01@example.com", "د. أحمد حسن"),
    ("test.doctor02@example.com", "د. أحمد محمود"),
    ("test.doctor03@example.com", "د. منى عبد الرحمن"),
]

# Canonical subject names, English. Deliberately not marked [TEST]: a subject is
# reference data a real deployment would also have, the same as `topics`, and
# --remove drops only the ones no course points at.
SUBJECTS = ["Anatomy", "Physiology", "Histology", "Biochemistry"]

STUDENTS = [
    ("test.student01@example.com", "سارة إبراهيم محمد"),
    ("test.student02@example.com", "عمر خالد فؤاد"),
    ("test.student03@example.com", "نورهان مصطفى علي"),
    ("test.student04@example.com", "يوسف حسن الديب"),
    ("test.student05@example.com", "مريم عادل شاكر"),
    ("test.student06@example.com", "كريم سمير عبد الله"),
    ("test.student07@example.com", "هبة الله ناصر"),
    ("test.student08@example.com", "محمود أنور زكي"),
    ("test.student09@example.com", "ريم طارق سعيد"),
    ("test.student10@example.com", "أحمد فتحي الجندي"),
]

TOPICS = [
    "Anatomical Terminology",
    "Bone Classification",
    "Vertebral Column",
    "Thoracic Cage",
    "Upper Limb",
    "Epithelial Tissue",
    "Connective Tissue",
    "Muscle Tissue",
    "Membrane Transport",
    "Nerve Physiology",
    "Cardiac Cycle",
]

# The catalog the search will be tested against:
#
#   (course key, title, doctor index, subject, academic_year, [modules])
#   module = (module key, title, position, [ (lecture key, title, duration) ])
#
# Titles are unchanged from the previous version on purpose — the upsert keys on
# title, so renaming a course would orphan the existing one and everything
# hanging off it. Module titles carry no [TEST] marker because the course above
# them already does, and "Cardiovascular" reads better in a catalog than
# "[TEST] Cardiovascular".
#
# The shape is built to be searched, so it contains on purpose:
#   · one subject across two academic years   (Physiology 1 and 2)
#   · two doctors called أحمد, both on Physiology
#   · one doctor teaching several courses      (each أحمد teaches two)
#   · courses with several modules, modules with several lectures
COURSES = [
    ("anatomy", "Anatomy 1 — تشريح ١", 0, "Anatomy", 1, [
        ("an_general", "General Anatomy", 1, [
            ("a1", "Introduction and Anatomical Terminology", 2700),
        ]),
        ("an_skeletal", "Skeletal System", 2, [
            ("a2", "Bone Classification and Structure", 3300),
        ]),
        ("an_axial", "Axial Skeleton", 3, [
            ("a3", "The Vertebral Column", 3600),
            ("a4", "The Thoracic Cage", 2400),
        ]),
        ("an_appendicular", "Appendicular Skeleton", 4, [
            ("a5", "Bones of the Upper Limb", 3000),
        ]),
    ]),
    ("histology", "Histology 1 — أنسجة ١", 2, "Histology", 1, [
        ("hi_basic", "Basic Tissues", 1, [
            ("h1", "Introduction to Tissues and Microscopy", 2100),
            ("h2", "Epithelial Tissue", 2700),
            ("h3", "Connective Tissue Proper", 3000),
        ]),
        ("hi_special", "Muscle and Nervous Tissue", 2, [
            ("h4", "Muscle and Nervous Tissue", 2850),
        ]),
    ]),
    ("physiology", "Physiology 1 — فسيولوجي ١", 0, "Physiology", 1, [
        ("ph_general", "General Physiology", 1, [
            ("p1", "Homeostasis and Body Fluid Compartments", 2400),
            ("p2", "Transport Across the Cell Membrane", 3150),
        ]),
        ("ph_nerve", "Nerve and Muscle", 2, [
            ("p3", "Resting Membrane and Action Potential", 3450),
        ]),
        ("ph_cardio", "Cardiovascular", 3, [
            ("p4", "The Cardiac Cycle", 2700),
            ("p5", "Cardiac Output and Blood Pressure", 2900),
        ]),
        ("ph_resp", "Respiratory", 4, [
            ("p6", "Mechanics of Breathing", 2600),
            ("p7", "Gas Exchange and Transport", 2800),
        ]),
        ("ph_renal", "Renal", 5, [
            ("p8", "Glomerular Filtration", 3000),
            ("p9", "Tubular Reabsorption and Secretion", 2750),
        ]),
    ]),
    ("physiology2", "Physiology 2 — فسيولوجي ٢", 1, "Physiology", 2, [
        ("p2_endo", "Endocrine", 1, [
            ("q1", "The Hypothalamic-Pituitary Axis", 3100),
            ("q2", "Thyroid and Parathyroid Hormones", 2850),
        ]),
        ("p2_neuro", "Neurophysiology", 2, [
            ("q3", "Sensory Pathways", 3300),
            ("q4", "Motor Control and Reflexes", 3050),
        ]),
    ]),
    ("biochem", "Biochemistry 1 — كيمياء حيوية ١", 1, "Biochemistry", 2, [
        ("bc_carb", "Carbohydrate Metabolism", 1, [
            ("b1", "Glycolysis", 2950),
            ("b2", "The Citric Acid Cycle", 3200),
        ]),
        ("bc_protein", "Proteins and Enzymes", 2, [
            ("b3", "Enzyme Kinetics", 2700),
        ]),
    ]),
]

# lecture key -> (topic name, stem, options, correct letter, difficulty)
QUESTIONS = {
    "a1": [
        ("Anatomical Terminology", "In the standard anatomical position, the palms face which direction?",
         ["A) Anteriorly", "B) Posteriorly", "C) Medially", "D) Laterally"], "A", "easy"),
        ("Anatomical Terminology", "Which term describes a structure closer to the trunk than another?",
         ["A) Distal", "B) Proximal", "C) Superficial", "D) Caudal"], "B", "easy"),
        ("Anatomical Terminology", "The sagittal plane divides the body into which parts?",
         ["A) Anterior and posterior", "B) Superior and inferior",
          "C) Right and left", "D) Medial and lateral"], "C", "medium"),
        ("Anatomical Terminology", "A structure described as 'ipsilateral' to another is:",
         ["A) On the opposite side", "B) On the same side",
          "C) Closer to the midline", "D) Further from the surface"], "B", "medium"),
    ],
    "a2": [
        ("Bone Classification", "A bone that is roughly as wide as it is long is classified as:",
         ["A) Long bone", "B) Short bone", "C) Flat bone", "D) Irregular bone"], "B", "easy"),
        ("Bone Classification", "Sesamoid bones characteristically develop within:",
         ["A) A tendon", "B) The medullary cavity",
          "C) The periosteum", "D) A synovial capsule"], "A", "medium"),
        ("Bone Classification", "The shaft of a long bone is called the:",
         ["A) Epiphysis", "B) Metaphysis", "C) Diaphysis", "D) Apophysis"], "C", "medium"),
        ("Bone Classification", "Which group consists entirely of flat bones?",
         ["A) Femur and humerus", "B) Sternum, scapula and parietal bone",
          "C) Carpals and tarsals", "D) Vertebrae and mandible"], "B", "hard"),
    ],
    "a3": [
        ("Vertebral Column", "How many vertebrae are present in the adult vertebral column?",
         ["A) 26", "B) 33", "C) 24", "D) 30"], "A", "medium"),
        ("Vertebral Column", "The foramen transversarium is a feature of which vertebrae?",
         ["A) Cervical", "B) Thoracic", "C) Lumbar", "D) Sacral"], "A", "medium"),
        ("Vertebral Column", "The cervical curvature develops when an infant:",
         ["A) Is born", "B) Begins to lift its head",
          "C) Begins to walk", "D) Reaches puberty"], "B", "medium"),
        ("Vertebral Column", "Which vertebra is known as the axis?",
         ["A) C1", "B) C2", "C) C7", "D) T1"], "B", "easy"),
    ],
    "a4": [
        ("Thoracic Cage", "How many pairs of ribs articulate directly with the sternum by their own costal cartilage?",
         ["A) 5", "B) 6", "C) 7", "D) 8"], "C", "medium"),
        ("Thoracic Cage", "Ribs 11 and 12 are described as:",
         ["A) True ribs", "B) False ribs", "C) Floating ribs", "D) Cervical ribs"], "C", "easy"),
        ("Thoracic Cage", "The sternal angle lies at the level of which costal cartilage?",
         ["A) First", "B) Second", "C) Third", "D) Fourth"], "B", "hard"),
        ("Thoracic Cage", "Which structure forms the inferior part of the sternum?",
         ["A) Manubrium", "B) Body", "C) Xiphoid process", "D) Costal margin"], "C", "easy"),
    ],
    "a5": [
        ("Upper Limb", "The radius is located on which side of the forearm in anatomical position?",
         ["A) Lateral", "B) Medial", "C) Posterior", "D) Anterior"], "A", "easy"),
        ("Upper Limb", "How many carpal bones are there in each wrist?",
         ["A) 5", "B) 7", "C) 8", "D) 10"], "C", "easy"),
        ("Upper Limb", "The olecranon process is a feature of which bone?",
         ["A) Humerus", "B) Radius", "C) Ulna", "D) Scapula"], "C", "medium"),
        ("Upper Limb", "The surgical neck of the humerus is clinically important because it is related to:",
         ["A) The radial nerve", "B) The axillary nerve",
          "C) The median nerve", "D) The ulnar nerve"], "B", "hard"),
    ],
    "h1": [
        ("Epithelial Tissue", "The four basic tissue types of the body are epithelial, connective, muscle and:",
         ["A) Adipose", "B) Nervous", "C) Cartilage", "D) Vascular"], "B", "easy"),
        ("Epithelial Tissue", "Which stain combination is most routinely used in histology?",
         ["A) Haematoxylin and eosin", "B) Gram stain",
          "C) Ziehl-Neelsen", "D) Periodic acid-Schiff"], "A", "easy"),
        ("Epithelial Tissue", "Haematoxylin stains acidic structures such as nuclei which colour?",
         ["A) Pink", "B) Blue-purple", "C) Brown", "D) Green"], "B", "medium"),
    ],
    "h2": [
        ("Epithelial Tissue", "Simple squamous epithelium lining blood vessels is specifically called:",
         ["A) Mesothelium", "B) Endothelium", "C) Urothelium", "D) Mesenchyme"], "B", "medium"),
        ("Epithelial Tissue", "Which epithelium lines the urinary bladder?",
         ["A) Stratified squamous", "B) Simple columnar",
          "C) Transitional", "D) Pseudostratified"], "C", "medium"),
        ("Epithelial Tissue", "Pseudostratified ciliated columnar epithelium is characteristic of the:",
         ["A) Oesophagus", "B) Trachea", "C) Stomach", "D) Colon"], "B", "hard"),
    ],
    "h3": [
        ("Connective Tissue", "The most abundant protein fibre in connective tissue proper is:",
         ["A) Elastin", "B) Collagen", "C) Reticulin", "D) Fibrillin"], "B", "easy"),
        ("Connective Tissue", "Which cell is chiefly responsible for producing the extracellular matrix?",
         ["A) Fibroblast", "B) Macrophage", "C) Mast cell", "D) Plasma cell"], "A", "easy"),
        ("Connective Tissue", "Dense regular connective tissue is the principal component of:",
         ["A) Dermis", "B) Tendons", "C) Adipose tissue", "D) Lymph nodes"], "B", "medium"),
    ],
    "h4": [
        ("Muscle Tissue", "Which muscle type is striated and under voluntary control?",
         ["A) Smooth", "B) Cardiac", "C) Skeletal", "D) Myoepithelial"], "C", "easy"),
        ("Muscle Tissue", "Intercalated discs are a distinguishing feature of which muscle?",
         ["A) Skeletal", "B) Cardiac", "C) Smooth", "D) All three"], "B", "medium"),
        ("Muscle Tissue", "The functional contractile unit of skeletal muscle is the:",
         ["A) Myofibril", "B) Sarcomere", "C) Sarcolemma", "D) Myofilament"], "B", "medium"),
    ],
    "p1": [
        ("Membrane Transport", "Approximately what fraction of total body water is intracellular?",
         ["A) One third", "B) One half", "C) Two thirds", "D) Three quarters"], "C", "medium"),
        ("Membrane Transport", "Homeostatic control systems most commonly operate by:",
         ["A) Positive feedback", "B) Negative feedback",
          "C) Feed-forward only", "D) Open-loop control"], "B", "easy"),
        ("Membrane Transport", "The main cation of the extracellular fluid is:",
         ["A) Potassium", "B) Sodium", "C) Calcium", "D) Magnesium"], "B", "easy"),
    ],
    "p2": [
        ("Membrane Transport", "Movement of water across a semipermeable membrane is termed:",
         ["A) Diffusion", "B) Osmosis", "C) Filtration", "D) Active transport"], "B", "easy"),
        ("Membrane Transport", "The Na+/K+ ATPase pumps which ions in which direction per cycle?",
         ["A) 3 Na+ in, 2 K+ out", "B) 3 Na+ out, 2 K+ in",
          "C) 2 Na+ out, 3 K+ in", "D) 1 Na+ out, 1 K+ in"], "B", "hard"),
        ("Membrane Transport", "Facilitated diffusion differs from simple diffusion because it:",
         ["A) Requires ATP", "B) Requires a carrier protein",
          "C) Moves against the gradient", "D) Only moves water"], "B", "medium"),
    ],
    "p3": [
        ("Nerve Physiology", "The resting membrane potential of a typical neuron is closest to:",
         ["A) -70 mV", "B) -20 mV", "C) 0 mV", "D) +30 mV"], "A", "easy"),
        ("Nerve Physiology", "Depolarisation during the action potential is caused mainly by influx of:",
         ["A) Potassium", "B) Chloride", "C) Sodium", "D) Calcium"], "C", "medium"),
        ("Nerve Physiology", "Saltatory conduction depends on the presence of:",
         ["A) Nodes of Ranvier", "B) Synaptic vesicles",
          "C) Dendritic spines", "D) Nissl bodies"], "A", "medium"),
    ],
    "p4": [
        ("Cardiac Cycle", "The first heart sound (S1) corresponds to closure of which valves?",
         ["A) Aortic and pulmonary", "B) Mitral and tricuspid",
          "C) Mitral and aortic", "D) Tricuspid and pulmonary"], "B", "medium"),
        ("Cardiac Cycle", "Isovolumetric contraction occurs when:",
         ["A) All valves are closed", "B) The aortic valve is open",
          "C) The mitral valve is open", "D) The ventricle is filling"], "A", "hard"),
        ("Cardiac Cycle", "The normal pacemaker of the heart is the:",
         ["A) AV node", "B) SA node", "C) Bundle of His", "D) Purkinje fibres"], "B", "easy"),
    ],
}

# student index -> course keys. Counts: anatomy 8, histology 6, physiology 4;
# several students sit on more than one course.
# Left without a subscription on purpose, so the paywall has a blocked case to
# test: student10 is enrolled on Physiology but has not paid its teacher.
UNSUBSCRIBED = {(9, "physiology")}

ENROLMENTS = {
    0: ["anatomy", "histology", "physiology"],
    1: ["anatomy", "histology"],
    2: ["anatomy"],
    3: ["anatomy", "histology"],
    4: ["anatomy", "histology"],
    5: ["anatomy", "physiology"],
    6: ["anatomy"],
    7: ["anatomy"],
    8: ["histology", "physiology"],
    9: ["histology", "physiology"],
}


# Viewing behaviour, one entry per (student, lecture). Steps use absolute video
# seconds so a plan reads the same however long the lecture is:
#
#   ("watch", ts)  play until the playhead reaches ts
#   ("seek", ts)   jump there and carry on
#   ("away", secs) the page goes hidden while the video keeps running
#   ("idle", secs) sit paused
#   ("pause",) ("complete",)
#
# The set is arranged to exercise every branch of the analytics: near-complete
# and abandoned viewings, skimming, rewinding, tab absence, one long sitting
# against many short ones, and the two completion patterns that decide whether a
# module report fires.
PLANS = [
    # --- Anatomy L1: sessions, a skimmer, an early drop-out, a long sitting ---
    (0, "a1", [(12, 20, [("watch", 1200), ("pause",)]),
               (10, 21, [("seek", 1200), ("watch", 2700), ("complete",)])]),
    (1, "a1", [(11, 19, [("watch", 1500), ("pause",), ("idle", 900),
                         ("watch", 2700), ("complete",)])]),
    (2, "a1", [(11, 22, [("watch", 260), ("pause",), ("idle", 120),
                         ("watch", 410), ("pause",)])]),
    (3, "a1", [(10, 18, [("watch", 300), ("seek", 900), ("watch", 1150),
                         ("seek", 2000), ("watch", 2280), ("seek", 2620),
                         ("watch", 2700), ("complete",)])]),
    (6, "a1", [(9, 17, [("watch", 2700), ("complete",)])]),
    (7, "a1", [(9, 23, [("watch", 220), ("pause",)])]),

    # --- Anatomy L2: a tab absence, and S2's second completion ---
    (0, "a2", [(9, 20, [("watch", 3300), ("complete",)])]),
    (1, "a2", [(10, 20, [("watch", 3300), ("complete",)])]),
    (4, "a2", [(8, 21, [("watch", 700), ("away", 300), ("watch", 1100),
                        ("pause",), ("idle", 600), ("watch", 1500), ("pause",)])]),
    (2, "a2", [(8, 19, [("watch", 900), ("pause",)])]),

    # --- Anatomy L3: the replay hotspots ---------------------------------
    # Four students rewind over roughly the same stretch with different edges,
    # so the analytics has overlapping-but-not-identical regions to group.
    (0, "a3", [(7, 20, [("watch", 1260), ("seek", 1200), ("watch", 1260),
                        ("watch", 1800), ("pause",)]),
               (6, 21, [("seek", 1800), ("watch", 3600), ("complete",)])]),
    (1, "a3", [(7, 18, [("watch", 1270), ("seek", 1210), ("watch", 1270),
                        ("watch", 2100), ("pause",), ("idle", 300),
                        ("watch", 3600), ("complete",)])]),
    (2, "a3", [(7, 22, [("watch", 1250), ("seek", 1190), ("watch", 1250),
                        ("seek", 1190), ("watch", 1250), ("pause",)])]),
    (3, "a3", [(6, 19, [("watch", 1280), ("seek", 1220), ("watch", 1280),
                        ("watch", 1500), ("pause",)])]),
    # the smaller, second hotspot
    (4, "a3", [(6, 20, [("seek", 2300), ("watch", 2445), ("seek", 2400),
                        ("watch", 2445), ("pause",)])]),
    # many short sittings
    (5, "a3", [(5, 8, [("watch", 400), ("pause",)]),
               (5, 13, [("seek", 400), ("watch", 780), ("pause",)]),
               (4, 9, [("seek", 780), ("watch", 1150), ("pause",)]),
               (4, 21, [("seek", 2415), ("watch", 2460), ("seek", 2415),
                        ("watch", 2460), ("pause",)])]),

    # --- Anatomy L4 and L5: only S1 finishes the course -------------------
    (0, "a4", [(5, 20, [("watch", 2400), ("complete",)])]),
    (0, "a5", [(4, 20, [("watch", 3000), ("complete",)])]),
    # the duplicate: S1 replays the end of the last lecture days later, which
    # emits a second `complete`. No second module report may appear.
    (0, "a5", [(2, 19, [("seek", 2900), ("watch", 3000), ("complete",)])]),
    (1, "a4", [(4, 18, [("watch", 900), ("pause",)])]),
    (3, "a5", [(3, 22, [("watch", 1400), ("away", 420), ("watch", 1900),
                        ("pause",)])]),

    # --- Histology --------------------------------------------------------
    (8, "h1", [(6, 17, [("watch", 2100), ("complete",)])]),
    (8, "h2", [(3, 18, [("watch", 1300), ("pause",)])]),
    (9, "h2", [(3, 20, [("watch", 1350), ("away", 240), ("watch", 1800),
                        ("pause",)])]),
    (9, "h1", [(5, 19, [("watch", 2100), ("complete",)])]),
    (0, "h1", [(2, 21, [("watch", 1000), ("pause",)])]),
    (4, "h3", [(2, 20, [("watch", 800), ("seek", 400), ("watch", 800),
                        ("pause",)])]),

    # --- Physiology -------------------------------------------------------
    (5, "p1", [(4, 19, [("watch", 2400), ("complete",)])]),
    (5, "p2", [(1, 20, [("watch", 1200), ("pause",)])]),
    (8, "p4", [(1, 21, [("watch", 700), ("pause",), ("idle", 240),
                        ("watch", 1500), ("away", 360), ("watch", 1900),
                        ("pause",)])]),
    (9, "p3", [(0, 18, [("watch", 900), ("pause",)])]),
]

# Every question of these (student, lecture) pairs gets answered, which is what
# makes the exam report fire. Everyone else leaves some unanswered.
EXAM_COMPLETIONS = [(0, "a1"), (1, "a2")]

# How often a student gets a question right, and which lectures they answer on.
# Deterministic: the nth question of a lecture is correct when n falls inside the
# quota, so re-running produces the same dataset rather than a new one.
PERFORMANCE = {
    0: (0.90, ["a1", "a2", "a3", "h1"]),      # strong
    1: (0.65, ["a1", "a2", "a3"]),            # average
    2: (0.35, ["a1", "a3"]),                  # struggling
    3: (0.60, ["a1", "a5"]),                  # average
    4: (0.50, ["a2", "a3", "h3"]),            # struggling-average
    5: (0.75, ["a3", "p1"]),                  # good
    6: (0.90, ["a1"]),                        # strong
    7: (0.30, ["a1"]),                        # struggling
    8: (0.70, ["h1", "p4"]),                  # average
    9: (0.55, ["h1", "h2", "p3"]),            # average
}

HEARTBEAT = 30


class Recorder:
    """Emits the event stream the player would have sent for one sitting."""

    def __init__(self, rows, student_id, lecture_id, session_id, clock, video_ts=0.0):
        self.rows = rows
        self.student_id = student_id
        self.lecture_id = lecture_id
        self.session_id = session_id
        self.clock = clock
        self.video_ts = float(video_ts)
        self.playing = False

    def _emit(self, kind):
        self.rows.append((self.student_id, self.lecture_id, kind,
                          round(self.video_ts, 1), self.session_id, self.clock))

    def _advance(self, seconds):
        self.clock += timedelta(seconds=seconds)
        self.video_ts += seconds

    def _ensure_playing(self):
        if not self.playing:
            self._emit("play")
            self.playing = True

    def watch(self, target):
        self._ensure_playing()
        remaining = max(target - self.video_ts, 0)
        while remaining > HEARTBEAT:
            self._advance(HEARTBEAT)
            self._emit("heartbeat")
            remaining -= HEARTBEAT
        self._advance(remaining)

    def seek(self, target):
        self._ensure_playing()
        self.video_ts = float(target)
        self._emit("seek")

    def away(self, seconds):
        """Hidden tab. The video runs on, so this also leaves a coverage hole."""
        self._ensure_playing()
        self._emit("tab_hidden")
        self._advance(seconds)
        self._emit("tab_visible")

    def pause(self):
        if self.playing:
            self._emit("pause")
            self.playing = False

    def idle(self, seconds):
        self.clock += timedelta(seconds=seconds)

    def complete(self):
        self._ensure_playing()
        self._emit("complete")
        self.playing = False


# -------------------------
# Inserts
# -------------------------


def upsert_user(cur, email, name, role):
    """Never duplicates: email is unique, so an existing row is reused.

    The name is refreshed on an existing row, because the seed's doctors were
    renamed to create the ambiguous-name cases and a stale name would leave the
    catalog testing the wrong thing.
    """

    cur.execute("SELECT id, name FROM users WHERE email = %s", (email,))
    row = cur.fetchone()

    if row:

        if row[1] != name:
            cur.execute("UPDATE users SET name = %s WHERE id = %s", (name, row[0]))

        return row[0], False

    cur.execute(
        "INSERT INTO users (role, name, email) VALUES (%s, %s, %s) RETURNING id",
        (role, name, email),
    )

    return cur.fetchone()[0], True


def upsert_subject(cur, name):
    """Canonical subject. Unmarked: it is reference data, not a fixture."""

    cur.execute("SELECT id FROM subjects WHERE name = %s", (name,))
    row = cur.fetchone()

    if row:
        return row[0], False

    cur.execute("INSERT INTO subjects (name) VALUES (%s) RETURNING id", (name,))

    return cur.fetchone()[0], True


def upsert_module(cur, course_id, title, position):
    """A module inside a course. UNIQUE(course_id, title) makes this safe."""

    cur.execute(
        "SELECT id FROM modules WHERE course_id = %s AND title = %s",
        (course_id, title),
    )
    row = cur.fetchone()

    if row:
        cur.execute(
            "UPDATE modules SET position = %s WHERE id = %s", (position, row[0])
        )
        return row[0], False

    cur.execute(
        "INSERT INTO modules (course_id, title, position) VALUES (%s, %s, %s) "
        "RETURNING id",
        (course_id, title, position),
    )

    return cur.fetchone()[0], True


def upsert_topic(cur, name):

    marked = f"{MARKER} {name}"

    cur.execute("SELECT id FROM topics WHERE name = %s", (marked,))
    row = cur.fetchone()

    if row:
        return row[0], False

    cur.execute("INSERT INTO topics (name) VALUES (%s) RETURNING id", (marked,))

    return cur.fetchone()[0], True


def upsert_course(cur, title, doctor_id, subject_id, academic_year):
    """A course with its place in the catalog.

    An existing course has its doctor, subject and year refreshed: those are the
    fields the catalog search filters on, and the whole point of re-running the
    seed is to bring the catalog up to the current definition.
    """

    marked = f"{MARKER} {title}"

    cur.execute("SELECT id FROM courses WHERE title = %s", (marked,))
    row = cur.fetchone()

    if row:
        cur.execute(
            "UPDATE courses SET doctor_id = %s, subject_id = %s, academic_year = %s "
            "WHERE id = %s",
            (doctor_id, subject_id, academic_year, row[0]),
        )
        return row[0], False

    cur.execute(
        "INSERT INTO courses (doctor_id, subject_id, academic_year, title) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (doctor_id, subject_id, academic_year, marked),
    )

    return cur.fetchone()[0], True


def upsert_lecture(cur, title, doctor_id, course_id, duration, module_id=None):
    """A lecture plus the one transcript row that gives it a length.

    `video_url` stays NULL: inventing a path to a file that is not on disk would
    make /api/lectures report a video that 404s. The transcript row carries no
    embedding, so retrieval (which filters on `embedding IS NOT NULL`) never
    sees it, but `MAX(end_ts)` gives the analytics the denominator it needs.
    """

    marked = f"{MARKER} {title}"

    cur.execute("SELECT id FROM lectures WHERE title = %s", (marked,))
    row = cur.fetchone()

    if row:
        # File an already-seeded lecture under its module. course_id is left
        # alone: everything else in the app hangs off it.
        cur.execute(
            "UPDATE lectures SET module_id = %s WHERE id = %s", (module_id, row[0])
        )
        return row[0], False

    cur.execute(
        """
        INSERT INTO lectures (doctor_id, course_id, module_id, title, video_url)
        VALUES (%s, %s, %s, %s, NULL) RETURNING id
        """,
        (doctor_id, course_id, module_id, marked),
    )
    lecture_id = cur.fetchone()[0]

    cur.execute(
        """
        INSERT INTO transcript_chunks (lecture_id, text, start_ts, end_ts, embedding)
        VALUES (%s, %s, 0, %s, NULL)
        """,
        (lecture_id, f"{MARKER} synthetic lecture — {title}. No transcript.", duration),
    )

    return lecture_id, True


def upsert_question(cur, lecture_id, topic_id, stem, options, correct, difficulty):

    cur.execute(
        "SELECT id FROM questions WHERE lecture_id = %s AND stem = %s",
        (lecture_id, stem),
    )
    row = cur.fetchone()

    if row:
        return row[0], False

    cur.execute(
        """
        INSERT INTO questions
            (lecture_id, topic_id, stem, options, correct_option, difficulty)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (lecture_id, topic_id, stem, Jsonb(options), correct, difficulty),
    )

    return cur.fetchone()[0], True


def option_letters(cur):
    """question id -> (correct letter, [wrong letters]) for every test question.

    Read back from the database rather than derived from QUESTIONS above, so a
    chosen option can never disagree with the answer key that scores it.
    """

    cur.execute(
        """
        SELECT q.id, q.correct_option, q.options
        FROM questions AS q
        JOIN lectures AS l ON l.id = q.lecture_id
        WHERE l.title LIKE %s
        """,
        (f"{MARKER}%",),
    )

    letters = {}

    for question_id, correct, options in cur.fetchall():

        available = []

        for text in options or []:
            label = str(text).strip()
            if len(label) >= 2 and label[1] in ").:-":
                available.append(label[0].upper())

        right = (correct or "").strip().upper()
        letters[question_id] = (right, [x for x in available if x != right] or [right])

    return letters


def build_events(students, lectures, base):
    """Turn PLANS into video_events rows."""

    rows = []
    counter = {}

    for student_index, lecture_key, sessions in PLANS:

        student_id = students[student_index]
        lecture_id = lectures[lecture_key]

        for day, hour, steps in sessions:

            key = (student_index, lecture_key)
            counter[key] = counter.get(key, 0) + 1

            session_id = f"test-s{student_index + 1:02d}-{lecture_key}-{counter[key]}"
            clock = base + timedelta(days=DAYS - day, hours=hour)

            recorder = Recorder(rows, student_id, lecture_id, session_id, clock)

            for step in steps:

                action = step[0]

                if action == "watch":
                    recorder.watch(step[1])
                elif action == "seek":
                    recorder.seek(step[1])
                elif action == "away":
                    recorder.away(step[1])
                elif action == "idle":
                    recorder.idle(step[1])
                elif action == "pause":
                    recorder.pause()
                elif action == "complete":
                    recorder.complete()

    return rows


def build_attempts(students, questions_by_lecture, base, letters):
    """Answers, at each student's own accuracy, plus the two full sweeps.

    Correctness is a quota rather than a coin toss: with three or four questions
    per lecture, random draws routinely hand a struggling student full marks and
    the analytics then has nothing to show.
    """

    rows = []
    exam_pairs = set(EXAM_COMPLETIONS)

    for student_index, (accuracy, lecture_keys) in sorted(PERFORMANCE.items()):

        student_id = students[student_index]
        moment = base + timedelta(days=DAYS - 6, hours=20)

        for lecture_key in lecture_keys:

            question_ids = questions_by_lecture.get(lecture_key, [])

            if not question_ids:
                continue

            full_sweep = (student_index, lecture_key) in exam_pairs

            # Everyone else deliberately leaves the last question alone, so the
            # exam trigger stays a property of the two pairs above.
            answered = question_ids if full_sweep else question_ids[:-1]

            if not answered:
                continue

            correct_quota = round(accuracy * len(answered))

            for position, question_id in enumerate(answered):

                correct = position < correct_quota
                right, wrong = letters[question_id]

                # Wrong answers converge on one distractor rather than scattering
                # evenly — that is exactly what the instructor view exists to
                # detect: a class piling onto the same wrong option means the
                # question is ambiguous or the distractor teaches something
                # false. One option is left unpicked, so "nobody chose D" is
                # testable too.
                picked_wrong = wrong[0] if (student_index + position) % 4 else (
                    wrong[1] if len(wrong) > 1 else wrong[0]
                )

                # Every third right answer arrives on the second try, so the
                # per-question and per-attempt counts differ in the data.
                if correct and position % 3 == 2:
                    rows.append((student_id, question_id, False, picked_wrong, moment))
                    moment += timedelta(minutes=2)

                rows.append((
                    student_id, question_id, correct,
                    right if correct else picked_wrong, moment,
                ))
                moment += timedelta(minutes=4)

    return rows


# -------------------------
# Integrity
# -------------------------


# Each check is a query that returns the *offending rows*, so a failure can show
# what it found rather than only how many. They run over the whole database, not
# just the seeded rows: an integrity check that ignores the existing data is not
# an integrity check.
CHECKS = [
    ("enrolments pointing at a missing student or course",
     """SELECT e.id FROM enrollments e
        LEFT JOIN users u ON u.id = e.student_id
        LEFT JOIN courses c ON c.id = e.course_id
        WHERE u.id IS NULL OR c.id IS NULL"""),
    ("duplicate enrolments",
     """SELECT student_id, course_id, count(*) FROM enrollments
        GROUP BY 1,2 HAVING count(*) > 1"""),
    ("video events pointing at a missing student or lecture",
     """SELECT v.id FROM video_events v
        LEFT JOIN users u ON u.id = v.student_id
        LEFT JOIN lectures l ON l.id = v.lecture_id
        WHERE u.id IS NULL OR l.id IS NULL"""),
    ("video events with an event_type outside the CHECK",
     """SELECT id, event_type FROM video_events WHERE event_type NOT IN
        ('play','pause','seek','skip','complete','rewatch_segment',
         'heartbeat','tab_hidden','tab_visible')"""),
    ("video events with a negative or null video_ts",
     "SELECT id, video_ts FROM video_events WHERE video_ts IS NULL OR video_ts < 0"),
    ("video events with no session_id",
     "SELECT id FROM video_events WHERE session_id IS NULL"),
    ("events whose video_ts runs past the lecture length",
     """SELECT v.id, v.video_ts, d.duration FROM video_events v
        JOIN (SELECT lecture_id, max(end_ts) AS duration FROM transcript_chunks
              GROUP BY lecture_id) d ON d.lecture_id = v.lecture_id
        WHERE v.video_ts > d.duration + 1"""),
    ("attempts pointing at a missing student or question",
     """SELECT a.id FROM question_attempts a
        LEFT JOIN users u ON u.id = a.student_id
        LEFT JOIN questions q ON q.id = a.question_id
        WHERE u.id IS NULL OR q.id IS NULL"""),
    ("attempts by a student not enrolled on that question's course",
     """SELECT a.id, u.email, l.title FROM question_attempts a
        JOIN users u ON u.id = a.student_id
        JOIN questions q ON q.id = a.question_id
        JOIN lectures l ON l.id = q.lecture_id
        WHERE l.course_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM enrollments e
          WHERE e.student_id = a.student_id AND e.course_id = l.course_id)"""),
    ("questions whose correct_option is not one of its options",
     """SELECT q.id, q.correct_option FROM questions q WHERE NOT EXISTS (
          SELECT 1 FROM jsonb_array_elements_text(q.options) AS o(v)
          WHERE upper(left(o.v, 1)) = upper(q.correct_option))"""),
    ("questions whose options are not a JSON array of 4",
     """SELECT id, jsonb_typeof(options) FROM questions
        WHERE jsonb_typeof(options) <> 'array' OR jsonb_array_length(options) <> 4"""),
    ("questions on a lecture that is in no course",
     """SELECT q.id, l.title FROM questions q JOIN lectures l ON l.id = q.lecture_id
        WHERE l.course_id IS NULL"""),
    ("lectures with no length (no transcript row)",
     """SELECT l.id, l.title FROM lectures l WHERE NOT EXISTS
        (SELECT 1 FROM transcript_chunks t WHERE t.lecture_id = l.id)"""),
    ("notifications pointing at a missing report",
     """SELECT n.id FROM notifications n WHERE n.report_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM reports r WHERE r.id = n.report_id)"""),
    ("notifications pointing at a missing user",
     """SELECT n.id FROM notifications n
        LEFT JOIN users u ON u.id = n.user_id WHERE u.id IS NULL"""),
    ("reports pointing at a missing student or course",
     """SELECT r.id FROM reports r
        LEFT JOIN users u ON u.id = r.student_id
        LEFT JOIN courses c ON c.id = r.course_id
        WHERE u.id IS NULL OR c.id IS NULL"""),
    ("reports with a kind outside the CHECK",
     "SELECT id, kind FROM reports WHERE kind NOT IN ('module','exam')"),
    ("duplicate reports for one completion",
     """SELECT student_id, course_id, kind, COALESCE(lecture_id,0), count(*)
        FROM reports GROUP BY 1,2,3,4 HAVING count(*) > 1"""),
    ("users with a role outside the CHECK",
     "SELECT id, role FROM users WHERE role NOT IN ('student','doctor')"),
    ("transcript chunks with a bogus time range",
     "SELECT id FROM transcript_chunks WHERE end_ts <= start_ts"),
]


def run_checks(cur):

    print("\nINTEGRITY  (whole database, not only the seeded rows)")
    failures = 0

    for label, sql in CHECKS:

        cur.execute(f"SELECT count(*) FROM ({sql}) AS offending")
        count = cur.fetchone()[0]

        print(f"  {'ok  ' if count == 0 else 'FAIL'} {count:>4}  {label}")

        if not count:
            continue

        failures += 1

        cur.execute(f"SELECT * FROM ({sql}) AS offending LIMIT 3")
        for row in cur.fetchall():
            print(f"            -> {row}")

    return failures


# -------------------------
# Removal
# -------------------------


def remove(cur):
    """Delete only what carries the test marker. Existing rows are untouched."""

    cur.execute("SELECT id FROM users WHERE email LIKE %s", (f"test.%@{EMAIL_DOMAIN}",))
    user_ids = [row[0] for row in cur.fetchall()]

    cur.execute("SELECT id FROM courses WHERE title LIKE %s", (f"{MARKER}%",))
    course_ids = [row[0] for row in cur.fetchall()]

    cur.execute("SELECT id FROM lectures WHERE title LIKE %s", (f"{MARKER}%",))
    lecture_ids = [row[0] for row in cur.fetchall()]

    removed = {}

    def run(label, sql, params):
        cur.execute(sql, params)
        removed[label] = cur.rowcount

    if user_ids:
        # users cascades nowhere, so every child goes first.
        run("notifications", "DELETE FROM notifications WHERE user_id = ANY(%s) "
            "OR student_id = ANY(%s)", (user_ids, user_ids))
        run("reports", "DELETE FROM reports WHERE student_id = ANY(%s)", (user_ids,))
        run("report_narratives",
            "DELETE FROM report_narratives WHERE student_id = ANY(%s)", (user_ids,))
        run("question_attempts",
            "DELETE FROM question_attempts WHERE student_id = ANY(%s)", (user_ids,))
        run("video_events",
            "DELETE FROM video_events WHERE student_id = ANY(%s)", (user_ids,))
        run("enrollments",
            "DELETE FROM enrollments WHERE student_id = ANY(%s)", (user_ids,))
        run("subscriptions",
            "DELETE FROM subscriptions WHERE student_id = ANY(%s) "
            "OR doctor_id = ANY(%s)", (user_ids, user_ids))

    if lecture_ids:
        # lectures cascades to questions, attempts, events and chunks.
        run("lectures", "DELETE FROM lectures WHERE id = ANY(%s)", (lecture_ids,))

    if course_ids:
        run("courses", "DELETE FROM courses WHERE id = ANY(%s)", (course_ids,))

    if user_ids:
        run("users", "DELETE FROM users WHERE id = ANY(%s)", (user_ids,))

    run("topics", "DELETE FROM topics WHERE name LIKE %s AND NOT EXISTS "
        "(SELECT 1 FROM questions q WHERE q.topic_id = topics.id)", (f"{MARKER}%",))

    # modules cascade with their course, so by here they are already gone.
    # Subjects are unmarked reference data: drop only the ones nothing uses.
    run("subjects", "DELETE FROM subjects WHERE name = ANY(%s) AND NOT EXISTS "
        "(SELECT 1 FROM courses c WHERE c.subject_id = subjects.id)", (SUBJECTS,))

    return removed


# -------------------------
# Main
# -------------------------


def seed(conn, dry_run=False):
    """All the inserts, in one transaction. Returns what was created."""

    base = datetime.now(timezone.utc) - timedelta(days=DAYS)
    made = {key: 0 for key in
            ("users", "topics", "subjects", "courses", "modules", "lectures",
             "enrollments", "questions", "attempts", "events", "subscriptions")}

    with conn.cursor() as cur:

        doctors = []
        for email, name in DOCTORS:
            user_id, created = upsert_user(cur, email, name, "doctor")
            doctors.append(user_id)
            made["users"] += created

        students = []
        for email, name in STUDENTS:
            user_id, created = upsert_user(cur, email, name, "student")
            students.append(user_id)
            made["users"] += created

        topics = {}
        for name in TOPICS:
            topic_id, created = upsert_topic(cur, name)
            topics[name] = topic_id
            made["topics"] += created

        subjects = {}
        for name in SUBJECTS:
            subject_id, created = upsert_subject(cur, name)
            subjects[name] = subject_id
            made["subjects"] += created

        courses = {}
        lectures = {}
        modules = {}
        course_lectures = {}

        for key, title, doctor_index, subject, year, module_specs in COURSES:

            course_id, created = upsert_course(
                cur, title, doctors[doctor_index], subjects[subject], year
            )
            courses[key] = course_id
            made["courses"] += created

            course_lectures[key] = []

            for module_key, module_title, position, lecture_specs in module_specs:

                module_id, created = upsert_module(
                    cur, course_id, module_title, position
                )
                modules[module_key] = module_id
                made["modules"] += created

                for lecture_key, lecture_title, duration in lecture_specs:
                    lecture_id, created = upsert_lecture(
                        cur, lecture_title, doctors[doctor_index], course_id,
                        duration, module_id,
                    )
                    lectures[lecture_key] = lecture_id
                    course_lectures[key].append(lecture_key)
                    made["lectures"] += created

        # Paid access first: enrolling on a course whose teacher a student does
        # not subscribe to is exactly what the paywall refuses, so the seed buys
        # access before it enrols anybody.
        for student_index, course_keys in sorted(ENROLMENTS.items()):

            for course_key in course_keys:

                if (student_index, course_key) in UNSUBSCRIBED:
                    continue

                course_doctor = doctors[
                    next(c[2] for c in COURSES if c[0] == course_key)
                ]

                cur.execute(
                    """
                    INSERT INTO subscriptions (student_id, doctor_id) VALUES (%s, %s)
                    ON CONFLICT (student_id, doctor_id) DO NOTHING
                    """,
                    (students[student_index], course_doctor),
                )
                made["subscriptions"] += cur.rowcount

        for student_index, course_keys in sorted(ENROLMENTS.items()):
            for course_key in course_keys:
                cur.execute(
                    """
                    INSERT INTO enrollments (student_id, course_id) VALUES (%s, %s)
                    ON CONFLICT (student_id, course_id) DO NOTHING
                    """,
                    (students[student_index], courses[course_key]),
                )
                made["enrollments"] += cur.rowcount

        questions_by_lecture = {}

        for lecture_key, items in QUESTIONS.items():
            ids = []
            for topic, stem, options, correct, difficulty in items:
                question_id, created = upsert_question(
                    cur, lectures[lecture_key], topics[topic],
                    stem, options, correct, difficulty,
                )
                ids.append(question_id)
                made["questions"] += created
            questions_by_lecture[lecture_key] = ids

        events = build_events(students, lectures, base)
        attempts = build_attempts(
            students, questions_by_lecture, base, option_letters(cur)
        )

        if not dry_run:

            # Users, courses and questions dedupe on a natural key; events and
            # attempts have none, so re-running would stack another copy on top
            # of the last one and silently double every engagement figure. Both
            # are replaced, scoped to rows this script wrote: `test-` sessions
            # and the test students.
            cur.execute(
                "DELETE FROM video_events WHERE session_id LIKE 'test-%%'"
            )
            cur.execute(
                "DELETE FROM question_attempts WHERE student_id = ANY(%s)",
                (students,),
            )

            cur.executemany(
                """
                INSERT INTO video_events
                    (student_id, lecture_id, event_type, video_ts, session_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                events,
            )
            made["events"] = len(events)

            cur.executemany(
                """
                INSERT INTO question_attempts
                    (student_id, question_id, is_correct, selected_option, answered_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                attempts,
            )
            made["attempts"] = len(attempts)

        else:
            made["events"] = len(events)
            made["attempts"] = len(attempts)

    return made, {
        "doctors": doctors, "students": students, "courses": courses,
        "lectures": lectures, "course_lectures": course_lectures,
        "questions": questions_by_lecture, "subjects": subjects,
        "modules": modules,
    }


def fire_triggers(ids):
    """Let the application produce the reports, rather than inserting them here.

    `reports` and `notifications` are written by app.services.triggers when a
    student finishes a module or a lecture's questions. Seeding them by hand
    would test the seed instead of the product, so the events above are arranged
    to satisfy the real conditions and the real code path is called.
    """

    from app.services import triggers

    fired = []

    for student_index, lecture_key in EXAM_COMPLETIONS:
        report_id = triggers.after_question_attempt(
            ids["students"][student_index], ids["lectures"][lecture_key]
        )
        if report_id:
            fired.append(("exam", student_index + 1, lecture_key, report_id))

    # Every lecture with a `complete` is offered to the module trigger; it decides
    # for itself whether that completion was the one that finished the course.
    for student_index, lecture_key, _ in PLANS:
        report_id = triggers.after_lecture_completed(
            ids["students"][student_index], ids["lectures"][lecture_key]
        )
        if report_id:
            fired.append(("module", student_index + 1, lecture_key, report_id))

    mark_one_read(ids)

    return fired


def mark_one_read(ids):
    """Leave one notification read, so both states exist to test against.

    Through the same service the endpoint uses, rather than an UPDATE here: the
    thing worth testing is that `read_at` is set the way the product sets it.
    """

    from app.services import notifications

    with connection() as conn:

        inbox = notifications.inbox(conn, ids["students"][0])

        if len(inbox["items"]) < 2:
            return

        # The older of the student's two, so an unread one is still on top.
        notifications.mark_read(conn, notification_id=inbox["items"][-1]["id"])


def summarise(cur, ids, made, fired):

    print("\nCREATED")
    for label in ("users", "subjects", "courses", "modules", "enrollments",
                  "subscriptions", "lectures", "topics", "questions", "attempts",
                  "events"):
        print(f"  {label + ':':<14} {made[label]}")

    cur.execute(
        "SELECT count(*) FROM reports WHERE student_id = ANY(%s)", (ids["students"],))
    print(f"  {'reports:':<14} {cur.fetchone()[0]}")

    cur.execute(
        "SELECT count(*) FROM notifications WHERE student_id = ANY(%s)",
        (ids["students"],))
    print(f"  {'notifications:':<14} {cur.fetchone()[0]}")

    if fired:
        print("\nREPORTS FIRED BY THE APPLICATION")
        for kind, student, lecture_key, report_id in fired:
            print(f"  {kind:<7} student{student:02d}  {lecture_key}  -> report {report_id}")

    print("\nTEST MAP")

    reverse_course = {v: k for k, v in ids["courses"].items()}

    for index, (email, name) in enumerate(STUDENTS):

        student_id = ids["students"][index]

        cur.execute(
            """
            SELECT c.title FROM enrollments e JOIN courses c ON c.id = e.course_id
            WHERE e.student_id = %s ORDER BY c.id
            """, (student_id,))
        enrolled = [row[0].replace(f"{MARKER} ", "") for row in cur.fetchall()]

        cur.execute(
            """
            SELECT DISTINCT l.title FROM video_events v JOIN lectures l ON l.id = v.lecture_id
            WHERE v.student_id = %s AND v.event_type = 'complete' ORDER BY l.title
            """, (student_id,))
        completed = [row[0].replace(f"{MARKER} ", "") for row in cur.fetchall()]

        cur.execute(
            """
            SELECT DISTINCT l.title FROM video_events v JOIN lectures l ON l.id = v.lecture_id
            WHERE v.student_id = %s AND l.id NOT IN (
              SELECT lecture_id FROM video_events
              WHERE student_id = %s AND event_type = 'complete')
            ORDER BY l.title
            """, (student_id, student_id))
        partial = [row[0].replace(f"{MARKER} ", "") for row in cur.fetchall()]

        cur.execute(
            """
            SELECT l.title FROM lectures l
            WHERE l.title LIKE %s AND EXISTS (SELECT 1 FROM questions q WHERE q.lecture_id = l.id)
              AND NOT EXISTS (
                SELECT 1 FROM questions q WHERE q.lecture_id = l.id AND NOT EXISTS (
                  SELECT 1 FROM question_attempts a
                  WHERE a.question_id = q.id AND a.student_id = %s))
            ORDER BY l.title
            """, (f"{MARKER}%", student_id))
        swept = [row[0].replace(f"{MARKER} ", "") for row in cur.fetchall()]

        cur.execute(
            "SELECT kind, COALESCE(lecture_id, 0) FROM reports WHERE student_id = %s "
            "ORDER BY id", (student_id,))
        reports = cur.fetchall()

        print(f"\n  student{index + 1:02d}  {name}  <{email}>")
        print(f"    enrolled            : {', '.join(enrolled) or '—'}")
        print(f"    completed lectures  : {', '.join(completed) or '—'}")
        print(f"    partial viewing     : {', '.join(partial) or '—'}")
        print(f"    all questions done  : {', '.join(swept) or '—'}")
        print(f"    reports produced    : "
              f"{', '.join(k for k, _ in reports) if reports else '—'}")


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="plan and count, write nothing")
    parser.add_argument("--remove", action="store_true",
                        help="delete the test data and stop")
    parser.add_argument("--no-reports", action="store_true",
                        help="skip the report triggers (no model calls)")
    args = parser.parse_args()

    with connection() as conn:

        if args.remove:
            with conn.cursor() as cur:
                removed = remove(cur)
            conn.commit()
            print("REMOVED")
            for label, count in removed.items():
                print(f"  {label + ':':<20} {count}")
            return 0

        try:
            made, ids = seed(conn, dry_run=args.dry_run)
        except Exception:
            conn.rollback()
            print("seed failed — rolled back, nothing was written")
            raise

        if args.dry_run:
            conn.rollback()
            print("DRY RUN — nothing written")
            for label, count in made.items():
                print(f"  {label + ':':<14} {count}")
            return 0

        conn.commit()

    # Reports run outside that transaction on purpose: they are application code
    # that manages its own commits, and a model call has no business holding a
    # write transaction open for half a minute.
    fired = [] if args.no_reports else fire_triggers(ids)

    with connection() as conn:
        with conn.cursor() as cur:
            summarise(cur, ids, made, fired)
            failures = run_checks(cur)

    print()
    print("Remove it all again with:  python -m scripts.seed_test_data --remove")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
