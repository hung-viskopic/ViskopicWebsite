"""Visq 1 follow-ups: after each answer, one short follow-up or move on."""
import re

from . import config
from .llm import CallFailed, ask_json
from .prompts import CLARIFY_SYSTEM, FOLLOWUP_SYSTEM
from .text import words
from .questions import check_question, opener, review_question

SKIP_WORDS = {"", "/skip", "skip", "pass", "idk", "i don't know", "i dont know"}


def is_empty_answer(answer: str) -> bool:
    a = (answer or "").strip().lower()
    return a in SKIP_WORDS or len(a.split()) < 2


BAD_OPENER = re.compile(r"^\s*when you say\b", re.I)
LOOK_LIKE = re.compile(r"\b(look like|in a specific|for a specific|a specific (module|course|exam|scenario|situation|project|class)|a particular (module|course|exam|scenario|situation|project|class))\b", re.I)
ROLE = re.compile(r"\b(you teach|you might (design|teach|run)|you (design|mark|grade|supervise|run)\b|your (own )?(module|modules|course|courses|students|class|classes|lectures?|department|university|college|school|job))\b", re.I)


_STOP = {"what", "would", "when", "where", "which", "that", "this", "with", "your", "their", "they", "have", "does", "about",
         "from", "there", "than", "then", "them", "were", "make", "makes", "could", "should", "being", "into", "such"}


def _stems(text: str) -> set:
    out = set()
    for w in words(text):
        if len(w) >= 4 and w not in _STOP:
            out.add((w[:-1] if w.endswith("s") else w)[:5])
    return out


def restates(q: str, asked: list[str], threshold: float = 0.7) -> bool:
    """True if most of the follow-up's content words are already in a question asked in this group (a rewording of it)."""
    sq = _stems(q)
    return bool(sq) and any(len(sq & _stems(a)) / len(sq) >= threshold for a in asked)


def check_followup(q: str, earlier: list[str], asked: list[str] = (), fu_asked: list[str] = (), session_asked: list[str] = ()) -> str | None:
    """Extra rules for follow-ups on top of check_question (q1.10)."""
    if BAD_OPENER.search(q):
        return "must not begin with 'When you say'; ask about the person's reason or boundary directly"
    if LOOK_LIKE.search(q):
        return "must not ask what something 'would look like' or for a specific situation; probe the reason, not the implementation"
    if ROLE.search(q):
        return "must not assume the person teaches, designs or runs anything"
    if restates(q, list(asked)):
        return "restates a question already asked; probe the person's reason or its boundary instead"
    if restates(q, list(session_asked), 0.55):
        return "is nearly the same as a question already asked earlier in this viva; ask about something else or move on"
    if restates(q, list(fu_asked), 0.45):
        return "is nearly the same as an earlier follow-up on this answer; if that did not get more, move on or ask about a different gap"
    if any(opener(q).split()[:2] == opener(a).split()[:2] for a in earlier):
        return "starts like an earlier question in this session; open differently"
    return None


CLARIFY_ASK = re.compile(r"\b(i\s*(do ?n[o']?t|don't|dont|do not|didn't|did not)\s*(really\s*)?(understand|get)|not sure what (you|this|that)|what do you mean|unclear|confus)", re.I)


def needs_clarification(answer: str) -> bool:
    """A short answer that says the question was not understood, or asks a question back about it."""
    a = (answer or "").strip()
    return 0 < len(a.split()) <= 12 and (bool(CLARIFY_ASK.search(a)) or a.endswith("?"))


def reword(client, question: str, max_words: int = 30) -> str | None:
    """One plainer rewording of a main question. Returns None if no acceptable rewording can be made."""
    def validate(d):
        q = str(d.get("question", "")).strip()
        if q.lower() == question.strip().lower():
            return "must differ from the original"
        return check_question(q, "", max_words, question)
    try:
        d = ask_json(client, CLARIFY_SYSTEM, f"<question>\n{question}\n</question>", validate,
                     temperature=config.TEMPERATURE_WRITE, max_tokens=config.WRITER_MAX_TOKENS, tries=2)
    except CallFailed:
        return None
    return str(d["question"]).strip()


def decide(client, question: str, answer: str, passage: str, history: list[dict], earlier_followups=(), linked=None, session_asked=()) -> dict:
    """Returns {"action": "follow_up"|"move_on", "question": str, "why": str}. Never raises for empty answers."""
    if is_empty_answer(answer):
        return {"action": "move_on", "question": "", "why": "no usable answer to follow up"}
    hist = "\n".join(f"Q: {h['question']}\nA: {h['answer']}" for h in history)
    user = (f"<passage>\n{passage or '(not located)'}\n</passage>\n<history>\n{hist or '(none)'}\n</history>\n"
            f"CURRENT QUESTION: {question}\n<answer>\n{answer.strip()}\n</answer>")
    own_words = " ".join(h["answer"] for h in history)             # what the person already said in this exchange is theirs to build on
    earlier_text = ""
    if linked and linked.get("answer"):               # the same person's typed extension on this point (author sessions only)
        earlier_text = f"{linked.get('question', '')} {linked['answer']}"
        user += (f"\n<earlier_answer>\nEARLIER PROMPT: {linked.get('question', '')}\nEARLIER ANSWER: {linked['answer'].strip()}\n</earlier_answer>")
    asked = [h["question"] for h in history] + [question]
    fu_asked = [h["question"] for h in history[1:]] + ([question] if history else [])      # follow-ups already asked about this answer

    def validate(d):
        act = d.get("action")
        if act not in ("follow_up", "move_on"):
            return "action must be follow_up or move_on"
        if act == "move_on":
            return None
        q = str(d.get("question", "")).strip()
        if q.lower() in {a.strip().lower() for a in asked}:
            return "the follow-up repeats an earlier question"
        bad = check_question(q, "", config.MAX_FOLLOWUP_WORDS, f"{passage} {question} {answer}")      # passage not checked: it may quote the student's own answer
        bad = bad or check_followup(q, list(earlier_followups), asked, fu_asked, list(session_asked))
        return bad or review_question(client, q, "followup", passage, answer.strip(), context=f"QUESTION ASKED: {question}" + (f"\nTHE PERSON'S EARLIER ANSWERS IN THIS EXCHANGE (their own words, so terms from them are fine): {own_words}" if own_words.strip() else "") + (f"\nEARLIER ANSWER: {earlier_text}" if earlier_text else ""))

    d = ask_json(client, FOLLOWUP_SYSTEM, user, validate, temperature=config.TEMPERATURE_WRITE,
                 max_tokens=config.WRITER_MAX_TOKENS)
    return {"action": d["action"], "question": str(d.get("question", "")).strip() if d["action"] == "follow_up" else "",
            "why": str(d.get("why", "")).strip()}