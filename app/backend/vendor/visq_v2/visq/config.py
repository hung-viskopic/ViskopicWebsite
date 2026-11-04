"""All Visq settings in one place."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MODEL = "openai/gpt-oss-120b"        # same model Alpha used; swap here to compare
REASONING_EFFORT = "low"
TEMPERATURE_EXTRACT = 0.0
TEMPERATURE_WRITE = 0.4

# Student essays and answers are personal data. They only go to providers that keep nothing (zero data retention).
PROVIDER_PREFS = {"zdr": True}
REQUIRE_ZDR = True

EXTRACTION_MAX_TOKENS = 6000
WRITER_MAX_TOKENS = 1500
CALL_TIMEOUT_S = 120

N_QUESTIONS = 5                      # main questions per viva
MAX_FOLLOWUPS = 1                    # follow-ups per main question
MIN_ESSAY_WORDS = 150
MAX_ESSAY_WORDS = 6000
MAX_QUESTION_WORDS = 40
MAX_FOLLOWUP_WORDS = 25
MAX_TRADEOFF_WORDS = 25
MAX_VERBATIM_RUN = 8                 # a question may not copy more than this many words in a row from the essay
WRITE_TRIES = 3
REVIEW_MAX_TOKENS = 600
MAX_PER_CLAIM = 2                   # questions in one set that may be built on the same claim (the thesis would otherwise anchor most of them)
MAX_PER_TYPE = 2                     # at most this many questions of one type per set (relaxed rather than leave the set short)

MAX_SPEND_PER_SESSION_USD = 0.50     # hard stop per command

CACHE_DIR = ROOT / ".visq_cache"
DATA_DIR = ROOT / "data"
QUESTIONS_DIR = DATA_DIR / "questions"
SESSIONS_DIR = DATA_DIR / "sessions"
SEED = 42

# ---- pilot web app ----------------------------------------------------------------------------------------------
PILOT_MIN_WORDS = 500                # essay length the participants must reach
PILOT_OTHERS_PER_PERSON = 4          # other people's essays each participant is asked about, then their own last
READ_SHORT_S, READ_LONG_S, READ_SUMMARY_S, READ_AUTHOR_S = 60, 300, 60, 300
PILOT_TOPICS = [
    "Should cities ban private cars from their centres?",
    "Is remote work better for society than office work?",
    "Should social media platforms be required to verify the age of every user?",
    "Should school start later in the morning?",
    "Should national parks limit the number of visitors per day?",
    "Is a four-day working week a good idea for most employers?",
]

# ---- levels: one suspicion level per essay drives two dials (extension prompts and viva depth) -------------------
# For the pilot the organiser sets the level; later the Sift triage score will set it. Low is never "proven author".
LEVELS = {                            # ext = extension prompts, viva = main viva questions, followups = the MOST follow-ups allowed per main question (optional: the model asks one only when the answer needs it; default is move on)
    "low": {"ext": 2, "viva": 1, "followups": 1},
    "medium": {"ext": 3, "viva": 3, "followups": 2},
    "high": {"ext": 5, "viva": 5, "followups": 3},
}
DEFAULT_LEVEL = "medium"
AUDIT_SHARE = 0.25                    # share of LOW-level essays that get the full (high) set anyway, so triage misses can be measured
EXT_SECONDS = 120                     # time allowed per extension prompt
EXT_GRACE_S = 10                      # an answer this much over time is still accepted (and flagged over_time)
EXT_MAX_WORDS = 150                   # the answer box stops here

# ---- the world-tree trial flow (clarifying questions, typed extension with a changed assumption, short viva): see visq/worldviva.py ------
WV_EXT_SECONDS = 360                  # time shown for the typed extension (overtime is recorded, not enforced by cutting the text)
WV_EXT_MAX_WORDS = 400                # the extension box stops here