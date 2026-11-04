"""vivatext: everything a STUDENT reads in the extension and the viva, in one place so it can be reviewed as a whole.

Rules for the wording: simple everyday words, no technical terms (no 'stance', 'assumption set', 'axiom', 'world', 'thesis', 'delta'),
short sentences, one thing asked at a time, but not childish. The essay's own sentences are not quoted word for word: a question says
'You said that ...' followed by a restatement. Offline the restatement is light (first person to second person, no 'However,'); with a
model (zero data retention, the same route as tree_algo --llm) it is a real paraphrase in plain words, which is checked by code.

Also here: the '?' button. It shows a worked example on a RANDOM mini-argument about something else (never the student's own topic),
built with the same wording as the real question, and gives a different one each time it is pressed."""
from __future__ import annotations

import random
import re

from . import worldalg as WA

WARNING = "This is not a viva. This is an extension to your piece of work."
EXT_NOTE = "Answer in your own words. There are no trick questions."
VIVA_START = "Now a few short questions about your essay. Answer in your own words."
HELP_HINT = "Type ? on its own line to see an example on a different topic."

TPL = {
    "rebuild": ["You said that {q}. What were you trying to show by saying that? Say it in your own words.",
                "You said that {q}. Why did you put that in your essay? What was it for?"],
    "recall": ["You made the point that {q}. What did you use to back it up? Say what you remember.",
               "You argued that {q}. What reasons or facts did you give for that?"],
    "recall_obj": ["You mentioned the worry that {q}. How did you answer it in your essay?"],
    "elicit": ["You said that {q}. What did you take for granted when you said that? Give one or two things.",
               "You said that {q}. What did you have to believe for that to be true? Give one or two things."],
    "clarify": ["You said that {q}. Your essay does not say what you take for granted here. What do you take for granted? Write a sentence or two."],
    "flip": ["Your essay seems to take it for granted that {q}. Suppose that is false. Name one point of yours that would stop working, "
             "and one that would still hold, and say why."],
    "flip_elicited": ["You told us you took for granted that {q}. Suppose that is false. Name one point of yours that would stop working, "
                      "and one that would still hold, and say why."],
    "counter_obj": ["You said that {q}. Someone could use that against your view. What would you say back?"],
    "counter_cross": ["In one place you said that {o}. In another you said that {q}. Do these two agree, or do they pull against each other? Explain."],
    "counter_conc": ["You said that {q}. Someone could say that is enough to reject your main point. What would you say back?"],
    "place": ["In your extra writing you made the point that {q}. Which point in your essay is that closest to? Does it add to that point, change it, or go against it?"],
    "ext_scn": ["Imagine that {q}. In {n} minutes, write how your argument would change. "
                "Say what you would keep, what you would drop and what you would add."],
    "ext": ["Imagine that this is not true: {q}. In {n} minutes, write how your argument would change. "
            "Say what you would keep, what you would drop and what you would add."],
}
FOLLOWUPS = {
    "none": "Take your time. What is the first thing you would say about this?",
    "generic": "Can you point to a sentence or an example in your essay that you mean?",
    "elicit": "Which sentence in your essay depends on that?",
    "flip": "Which of your points would be hardest to keep, and why?",
}

# words a student should never have to meet (checked by a test)
JARGON = ("stance", "axiom", "thesis", "world", "ghost", "delta", "cascade", "node", "elicit", "rebuild", "flip", "tree", "entail", "counterpart",
          "extraction", "marker", "anchor")


# ---------------------------------------------------------------- restating
LEAD = re.compile(r"^(however|finally|therefore|thus|yet|moreover|furthermore|also|in addition|firstly|secondly|thirdly|first|second|third|"
                  r"for example|for instance|overall|similarly|in contrast|on the other hand|a second reason|a further point|in conclusion)\b[,:]?\s+", re.I)
_FIRST = [(r"\bI am\b", "you are"), (r"\bI'm\b", "you are"), (r"\bI have\b", "you have"), (r"\bI was\b", "you were"), (r"\bI\b", "you"),
          (r"\bmy\b", "your"), (r"\bmine\b", "yours"), (r"\bme\b", "you"), (r"\bmyself\b", "yourself")]


def clip(s: str, n: int = 220) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def restate(text: str) -> str:
    """Light, offline restatement as a clause that follows 'You said that': leading connective removed, first person turned to second,
    no final full stop, first letter lowered unless the word looks like a name. A weaker version of a real paraphrase."""
    t = " ".join((text or "").split()).strip()
    t = t.rstrip(" .")
    t2 = LEAD.sub("", t, count=1)
    t = t2 if len(WA.toks(t2)) >= 4 else t
    for pat, rep in _FIRST:
        t = re.sub(pat, rep, t)
    t = t.replace("…", "").strip()
    w = t.split(" ", 1)[0] if t else ""
    later = t.split(" ", 1)[1] if " " in t else ""
    if w and w[0].isupper() and (len(w) == 1 or not w.isupper()) and not any(ch.isdigit() for ch in w) and w not in re.findall(r"\b[A-Z][a-z]+\b", later) and not re.match(r"[A-Z]", later):
        t = w[0].lower() + t[1:]
    return clip(t, 200)


_SAID = re.compile(r"^(you\s+(said|say|argued|argue|wrote|write|stated|state|made the point|mentioned|claimed)\s+(that\s+)?)+", re.I)


def strip_said(text: str) -> str:
    """A model sometimes starts with 'You said that' although the question already does: drop it so it is not said twice."""
    t = _SAID.sub("", " ".join(str(text or "").split()), count=1)
    return t[:1].lower() + t[1:] if t[:2].isalpha() and not t[:2].isupper() else t


def phrase(v: str, para: dict | None = None) -> str:
    return strip_said((para or {}).get(v) or "") or restate(v)


def fill(template: str, slots: dict, para: dict | None = None, **extra) -> str:
    return template.format(**{k: phrase(v, para) for k, v in slots.items()}, **extra)


# ---------------------------------------------------------------- paraphrase with a model (zero data retention route)
PARA_SYSTEM = (
    "You rewrite short statements taken from a student's essay so that they are NOT word for word. For each statement write ONE clause that can "
    "follow the words 'You said that', in plain everyday words (a short word instead of a long one where you can), with exactly the same meaning. "
    "Do not copy any phrase of three or more words in a row. Do not add facts, opinions or explanations. No full stop at the end. "
    "Speak to the student: where the statement says I or my, write you or your. "
    'Return JSON: {"items": [{"n": 0, "text": "..."}]} with one item per statement, using the same numbers.')


def _copies_a_phrase(orig: str, new: str, k: int = 3) -> bool:
    a = [w for w in re.findall(r"[a-z0-9']+", orig.lower())]
    b = " ".join(re.findall(r"[a-z0-9']+", new.lower()))
    return any(" ".join(a[i:i + k]) in b for i in range(len(a) - k + 1))


def good_paraphrase(orig: str, new: str) -> bool:
    new = (new or "").strip()
    if not new or '"' in new or len(WA.toks(new)) < 3 or len(new.split()) > 2 * len(orig.split()) + 6:
        return False
    if not (WA.toks(orig) & WA.toks(new)):                     # unrelated
        return False
    return not _copies_a_phrase(restate(orig), new)


def paraphrase(client, texts: list, ask=None) -> dict:
    """{original text: paraphrase} for the ones the model rewrote acceptably; anything else falls back to restate() when used.
    Only the short statements are sent (no title, no personal data); the client must be the zero-data-retention one."""
    texts = [t for t in dict.fromkeys(texts) if t]
    if not texts or (client is None and ask is None):
        return {}
    body = "\n".join(f"[{i}] {t}" for i, t in enumerate(texts))
    if ask is None:
        from .llm import CallFailed, ask_json

        def ask(system, user):
            return ask_json(client, system, user, lambda d: None if isinstance(d.get("items"), list) and d["items"] else "items must be a non-empty list",
                            temperature=0.3, max_tokens=3000, tries=2)
        try:
            d = ask(PARA_SYSTEM, f"<statements>\n{body}\n</statements>")
        except CallFailed:
            return {}
    else:
        d = ask(PARA_SYSTEM, f"<statements>\n{body}\n</statements>")
    out = {}
    for r in d.get("items", []):
        try:
            i = int(r.get("n"))
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(texts) and good_paraphrase(texts[i], str(r.get("text", ""))):
            out[texts[i]] = strip_said(r["text"]).rstrip(" .")
    return out


# ---------------------------------------------------------------- a what-if from outside the essay (model, zero data retention route)
SCENARIO_SYSTEM = (
    "You help to set a short writing task for a student who has written an essay. Invent a what-if: a change in the world OUTSIDE the essay "
    "that puts pressure on the essay's argument. After the change, at least one reason in the essay must be weaker, no longer needed, or no longer "
    "enough, so that a student who agrees with the essay still has to change something. Do NOT give a change that helps the essay's side, that "
    "already gets the essay's conclusion for free (for example, the goal is already reached), or that is the opposite of something the essay says. "
    "It must not be a small detail, and it must not hint at what the student should write. It can be about technology, cost, rules, numbers of "
    "people, or time. It must be possible, not silly and not extreme. Use plain everyday words. "
    "Write the what-if as ONE sentence of at most 25 words, WITHOUT the words 'Imagine that' at the start (they are added later). "
    "For example, for an essay that argues for a tax on sugary drinks (a different topic, so do not reuse it): "
    "'most drinks in shops are now made with a cheap new sweetener that is not sugar'. "
    "Say which numbered statement gets weaker (touches) and, in one short sentence, why (pressure). Give three different options. "
    'Return JSON: {"options": [{"text": "...", "touches": 0, "pressure": "..."}]}.')


_LEAD_IMAGINE = re.compile(r"^(imagine\s+(that\s+)?|what if\s+|suppose\s+(that\s+)?)", re.I)


def clean_scenario(text: str, names=()) -> str:
    """Models often repeat the words 'Imagine that' even when told not to: strip them, and a final full stop."""
    t = " ".join(str(text or "").split())
    while _LEAD_IMAGINE.match(t):
        t = _LEAD_IMAGINE.sub("", t, count=1)
    t = t.rstrip(" .")
    first = t.split(" ", 1)[0]
    if (len(first) > 1 and first.isupper()) or first in names:          # an acronym or a name from the essay keeps its capital
        return t
    return t[:1].lower() + t[1:]


def good_scenario(text: str, statements: list) -> bool:
    """A what-if must be short, plain, not copied from the essay, and not just the opposite of something it says (that would be circular)."""
    t = clean_scenario(text)
    n = len(t.split())
    if not t or '"' in t or "?" in t or n < 6 or n > 30:
        return False
    low = t.lower()
    if any(j in low for j in JARGON):
        return False
    for st in statements:
        if _copies_a_phrase(st, t) or WA.jaccard(st, t) >= 0.5:
            return False
        if re.search(r"\b(not|no longer|never|without|stops?|ends?)\b", low) and WA.jaccard(st, t) >= 0.3:
            return False                                         # reads as the opposite of something the essay says
    return True


def scenario(client, claim: str, statements: list, ask=None):
    """{'text': what-if, 'touches': index into `statements`} or None. Only short statements are sent (no title, no personal data);
    the client must be the zero-data-retention one."""
    if not statements or (client is None and ask is None):
        return None
    body = f"<main claim>\n{claim}\n</main claim>\n<statements>\n" + "\n".join(f"[{i}] {t}" for i, t in enumerate(statements)) + "\n</statements>"
    names = {w.strip(",.;:") for st in statements + [claim] for w in st.split()[1:] if w[:1].isupper()}
    if ask is None:
        from .llm import CallFailed, ask_json

        def ask(system, user):
            return ask_json(client, system, user, lambda d: None if isinstance(d.get("options"), list) and d["options"] else "options must be a non-empty list",
                            temperature=0.8, max_tokens=1500, tries=2)
        try:
            d = ask(SCENARIO_SYSTEM, body)
        except CallFailed:
            return None
    else:
        d = ask(SCENARIO_SYSTEM, body)
    for o in d.get("options", []):
        try:
            i = int(o.get("touches"))
        except (TypeError, ValueError):
            continue
        text = clean_scenario(o.get("text", ""), names)
        why = " ".join(str(o.get("pressure", "")).split())
        if 0 <= i < len(statements) and len(why.split()) >= 4 and good_scenario(text, statements + [claim]):
            return {"text": text, "touches": i, "pressure": why[:300]}
    return None


# ---------------------------------------------------------------- the ? button: examples on a random, different topic
POOL = [
    dict(claim="pollution is bad for the economy of a city", scn="nearly all the traffic in the city now runs on clean electricity", support="ill workers miss days at work",
         evidence="one study found that sick days went up in the most polluted districts", why="it shows that dirty air leads to lost work",
         assumption="workers get ill more often when the air is dirty", objection="cleaning the air costs firms a lot of money",
         reply="the cost is small next to the money lost when workers are off sick",
         falls="the point that firms lose money on sick days", falls_why="it needs dirty air to make people ill",
         stands="the point that clean air attracts new companies", stands_why="that rests on how the city looks to investors, not on sickness",
         new="a city could tax the worst polluters to pay for cleaner buses", fit="it adds to my point about the cost of cleaning the air, by saying who could pay",
         add="a tax on the worst polluters"),
    dict(claim="schools should start later in the morning", scn="a cheap new habit programme helps teenagers fall asleep earlier", support="teenagers fall asleep later by nature",
         evidence="a trial in one district found that students were less tired in class", why="it shows that later starts help students stay awake",
         assumption="teenagers cannot simply go to bed earlier", objection="later starts are hard for parents who work early",
         reply="schools could offer a supervised breakfast club",
         falls="the point that students would be less tired", falls_why="it needs teenagers to keep their natural sleep pattern",
         stands="the point that a breakfast club helps working parents", stands_why="it is about parents' hours, not about sleep",
         new="bus times would have to change", fit="it adds to my point about parents, because transport affects them too", add="new bus times"),
    dict(claim="a four-day week can keep output the same", scn="new rules make firms keep every task they do now", support="people waste less time in meetings when time is short",
         evidence="several firms that tried it reported the same results in four days", why="it shows that output did not fall",
         assumption="firms can cut tasks that add little value", objection="some jobs, like care work, cannot be done in fewer days",
         reply="those jobs could use shift rotas so that cover stays the same",
         falls="the point that meetings take up less time", falls_why="it needs firms to be able to cut low-value tasks",
         stands="the point that shift rotas keep care jobs covered", stands_why="it is about staffing, not about meetings",
         new="pay would have to stay the same", fit="it changes my point about firms, because they would need to afford it", add="a rule about pay"),
    dict(claim="councils should keep paying for public libraries", scn="every home in the country gets free internet", support="libraries give free internet to people who have none",
         evidence="a survey found that many users have no internet at home", why="it shows who relies on libraries",
         assumption="people without home internet have no cheap way to get online", objection="most books can now be read online",
         reply="libraries do more than lend books, such as job help and quiet places to study",
         falls="the point that libraries give free internet", falls_why="it needs people to have no other cheap way online",
         stands="the point that libraries give job help", stands_why="it does not depend on internet access",
         new="libraries could share buildings with other services", fit="it adds to my point about cost, by saying how to save money", add="shared buildings"),
    dict(claim="a charge on plastic bags reduces waste", scn="a reward app pays people to take bags back, so the charge no longer feels like a cost", support="people take fewer bags when they have to pay",
         evidence="bag use in shops fell sharply after the charge began", why="it shows that the charge changed what people do",
         assumption="people react to small prices", objection="people just buy thicker bags instead",
         reply="the charge could cover thicker bags too",
         falls="the point that people take fewer bags", falls_why="it needs small prices to change what people do",
         stands="the point that the charge can cover thicker bags", stands_why="it is about how the rule is written",
         new="shops could give the money to charity", fit="it adds to my point about the charge, by saying where the money goes", add="a rule about charity"),
]


def _cap(s: str) -> str:
    return s[0].upper() + s[1:] if s else s


def example(kind: str, n: int = 0, seed=0) -> dict:
    """A worked example for the '?' button: {'topic', 'question', 'answer'}. `kind` is a question type (rebuild, recall, recall_obj, elicit,
    clarify, flip, flip_elicited, counter, place, ext). A new n gives a different mini-argument."""
    rng = random.Random(f"ex:{seed}:{kind}")
    order = list(range(len(POOL)))
    rng.shuffle(order)
    e = POOL[order[n % len(POOL)]]
    t = lambda k, **kw: TPL[k][0].format(**kw)                        # noqa: E731 (the first wording is enough for an example)
    if kind == "rebuild":
        q, a = t("rebuild", q=e["evidence"]), f"I meant it to back up my point that {e['claim']}, because {e['why']}."
    elif kind == "recall":
        q, a = t("recall", q=e["claim"]), f"I gave two things. First, {e['support']}. Second, {e['evidence']}."
    elif kind == "recall_obj":
        q, a = t("recall_obj", q=e["objection"]), f"I said that {e['reply']}."
    elif kind in ("elicit", "clarify"):
        q = t("elicit", q=e["claim"]) if kind == "elicit" else t("clarify", q=e["claim"])
        a = f"I took for granted that {e['assumption']}."
    elif kind in ("flip", "flip_elicited"):
        q = t(kind, q=e["assumption"])
        a = f"{_cap(e['falls'])} would fall, because {e['falls_why']}. {_cap(e['stands'])} would still stand, because {e['stands_why']}."
    elif kind == "counter":
        q, a = t("counter_obj", q=e["objection"]), f"I would say that {e['reply']}."
    elif kind == "place":
        q, a = t("place", q=e["new"]), f"{_cap(e['fit'])}."
    elif kind == "ext":
        q = t("ext_scn", q=e["scn"], n=6)
        a = (f"I would keep {e['stands']}, because {e['stands_why']}. I would drop {e['falls']}, because {e['falls_why']}. "
             f"I would add {e['add']}.")
    else:
        raise KeyError(kind)
    return {"topic": e["claim"], "question": q, "answer": a}


def show_example(kind: str, n: int = 0, seed=0) -> str:
    ex = example(kind, n, seed)
    return ("Example on a different topic (not yours):\n"
            f"  Question: {ex['question']}\n  A good answer: {ex['answer']}\n"
            "Your own answer should be about YOUR essay, in your own words.")