"""Visq 1 main questions: pick targets in the AROA graph, write one question per target, check each question.

Since q1.3 every question makes the person APPLY or WEIGH their own argument (a trade-off, or an 'if ..., what would
your answer be?' case), because questions that ask them to justify what the essay already says are easy for anyone
who has read it to answer."""
import random
import re
from dataclasses import dataclass

from . import config
from .aroa import Graph
from .llm import BudgetExceeded, CallFailed, ask_json
from .prompts import QUESTION_SYSTEM, REVIEW_SYSTEM, TYPE_BRIEFS
from .text import locate, longest_common_run, words

# order in which question types are used; each type is used once before any repeats
TYPE_ORDER = ["tradeoff", "limit", "case"]
TWO_PART = re.compile(r"\b(and|then|also|plus)\s+(why|how|what|explain|describe|say)\b", re.I)
STATEMENT_FIRST = re.compile(r"[.!:;]\s+\S")          # a statement before the question, or two sentences
JARGON = re.compile(r"\b(intermediate claim|premises?|warrants?|inference|inferences|counter-?arguments?|rebuttals?|propositions?)\b", re.I)
PRESUME = re.compile(r"\b(in|from) your (own )?(research|experience)\b|\byour (own )?research\b", re.I)
TRADEOFF_SHAPE = re.compile(r"\b(under what|in what (conditions|situations|cases|circumstances)|what would have to be true|where would you draw|in which (conditions|situations|cases)|when would)\b", re.I)
AI_LABEL = re.compile(r"\bAI[\s\-\u2010-\u2015]*(driven|based|powered|enabled|assisted|run|led|generated|mediated)\b", re.I)
AUX_START = {"would", "do", "does", "did", "can", "could", "should", "will", "is", "are", "was", "were", "have", "has"}
WH = re.compile(r"\b(what|how|why|where|when|which|who|whom|whose)\b", re.I)
BARE_ANSWER = re.compile(r"what would your answer be\?$", re.I)
NUMBER = re.compile(r"\d[\d,.]*")
LINK_BAD = re.compile(r"\b(of your (own )?(reasons|points|arguments)|your (own )?(reasons|points|reasoning|argument|arguments|position|conclusion)|earlier|you said|your essay|the extension)\b", re.I)
FORBIDDEN = re.compile(r"\b(AI|A\.I\.|ChatGPT|GPT|cheat\w*|plagiar\w*|chatbot|generated|detector)\b", re.I)


@dataclass
class Target:
    qtype: str
    target_ids: list
    target_text: str
    passage: str
    extra: str = ""                      # for a 'link' question: the extension prompt the person already answered on this point
    ext_index: int = None


def _candidates(graph: Graph, essay: str) -> dict:
    """For each question type, the (ids, text) options the graph offers."""
    out = {t: [] for t in TYPE_ORDER}
    # tradeoff: an objection against the author's position (best), then two reasons that pull in different directions
    for a in graph.of_kind("counter"):
        claims = graph.linked(a.id, "opposes", "out")
        rebut = graph.linked(a.id, "responds_to", "in")
        ids = [a.id] + [c.id for c in claims[:1]] + [r.id for r in rebut[:1]]
        txt = a.text + (f"  (objection to the position: {claims[0].text})" if claims else "") \
            + (f"  (the essay answers it with: {rebut[0].text})" if rebut else "")
        out["tradeoff"].append((ids, txt))
    for c in graph.of_kind("claim"):
        rs = graph.linked(c.id, "supports", "in")
        for i in range(len(rs) - 1):
            out["tradeoff"].append(([rs[i].id, rs[i + 1].id], f"{rs[i].text}  (set against: {rs[i + 1].text})"))
        out["case"].append(([c.id], c.text))
    for r in graph.of_kind("reason"):
        claims = graph.linked(r.id, "supports", "out")
        out["limit"].append(([r.id] + [c.id for c in claims[:1]],
                             r.text + (f"  (supports the position: {claims[0].text})" if claims else "")))
    return out


def plan_targets(graph: Graph, essay: str, n: int = None, seed: int = None) -> list[Target]:
    """Deterministic for a given essay and seed. Spreads questions over types and never reuses an element."""
    n = n or config.N_QUESTIONS
    rng = random.Random(config.SEED if seed is None else seed)
    cand = _candidates(graph, essay)
    for t in cand:
        # keep objections ahead of reason pairs for tradeoff; shuffle only within each group
        rng.shuffle(cand[t])
    cand["tradeoff"].sort(key=lambda x: len(x[0]) == 2 and x[1].find("(set against") >= 0, reverse=True)   # pairs end up last popped
    used, picks, deferred = set(), [], []
    claim_use = {}
    claim_ids = {e.id for e in graph.of_kind("claim")}
    rounds = 0
    while len(picks) < n and rounds < 4 * n:
        progressed = False
        for t in TYPE_ORDER:
            if len(picks) >= n:
                break
            while cand[t]:
                ids, txt = cand[t].pop()
                if ids[0] in used:
                    continue
                if any(claim_use.get(i, 0) >= config.MAX_PER_CLAIM for i in ids if i in claim_ids):
                    deferred.append((t, ids, txt))           # this claim already anchors enough questions; use only if nothing else is left
                    continue
                used.add(ids[0])
                for i in ids:
                    if i in claim_ids:
                        claim_use[i] = claim_use.get(i, 0) + 1
                picks.append((t, ids, txt))
                progressed = True
                break
        rounds += 1
        if not progressed:
            break
    for t, ids, txt in deferred:                              # relax the cap rather than leave the plan short
        if len(picks) >= n:
            break
        if ids[0] not in used:
            used.add(ids[0])
            picks.append((t, ids, txt))
    return [Target(t, ids, txt, locate(essay, graph.elements[ids[0]].text)) for t, ids, txt in picks]


def opener(q: str) -> str:
    return " ".join(words(q)[:3])


def is_yes_no(q: str) -> bool:
    """True if the question can be answered with one word: its last clause starts with an auxiliary verb and has no
    wh-word and no 'A or B' choice ('If X, would you still expect Y?')."""
    clause = (q or "").strip().rsplit(",", 1)[-1].strip().rstrip("?").strip()
    first = clause.split()[0].lower() if clause.split() else ""
    return first in AUX_START and not WH.search(clause) and " or " not in f" {clause.lower()} "


def check_question(q: str, passage: str, max_words: int, context: str = "") -> str | None:
    """`context` is any extra text whose words the question may reuse (the essay point it draws on, or the person's answer)."""
    q = (q or "").strip()
    if not q.endswith("?") or q.count("?") != 1:
        return "must be exactly one question ending in '?'"
    n = len(q.split())
    if n < 4 or n > max_words:
        return f"must be 4 to {max_words} words (it has {n})"
    if q[:4].lower() == "you " or STATEMENT_FIRST.search(q):
        return "must be one sentence that asks, not a statement about the person followed by a question"
    if TWO_PART.search(q):
        return "must ask for one thing only (no 'and why' / 'and how' second part)"
    if re.search(r"\b(the|this|that) passage\b|\bpassage\b", q, re.I):
        return "must not mention 'the passage' (the person cannot see it); say 'your essay' or name the idea"
    if PRESUME.search(q):
        return "must not assume the person did research or had experience the essay does not mention"
    if BARE_ANSWER.search(q):
        return "must say what the answer is about (e.g. 'what would you recommend for ...?'), not end with a bare 'what would your answer be?'"
    if JARGON.search(q):
        return "must use everyday words (no 'intermediate claim', 'premise', 'counterargument' ...)"
    label = AI_LABEL.search(q)
    if label and not AI_LABEL.search(f"{passage} {context}"):                  # a label the essay never uses is an invention
        return "must not invent a label like 'AI-driven' or 'AI-based' for something the essay does not call that"
    hits = {m.group(0).lower() for m in FORBIDDEN.finditer(q)}
    if hits and not hits <= set(words(f"{passage} {context}")):         # fine when the essay itself is about it; never a hint at the person
        return "must not mention AI, cheating or plagiarism unless the essay itself is about it"
    nums = {m.group(0).strip(",.") for m in NUMBER.finditer(q)}
    if nums and not nums <= {m.group(0).strip(",.") for m in NUMBER.finditer(f"{passage} {context}")}:
        return "must not bring in a number the essay does not contain (the person cannot know it): vary a condition the essay mentions, e.g. 'if the number of affected people were much larger'"
    if is_yes_no(q):
        return "must not be answerable with yes or no: ask 'what', 'how' or 'under what conditions' instead"
    if passage and longest_common_run(words(q), words(passage)) > config.MAX_VERBATIM_RUN:
        return f"copies more than {config.MAX_VERBATIM_RUN} words in a row from the essay"
    return None


def check_variety(q: str, prior: list[str]) -> str | None:
    """Across one question set: no two questions open alike, and 'decide' appears at most once."""
    if any(opener(q) == opener(p) for p in prior):
        return f"starts like an earlier question ('{opener(q)} ...'); open differently"
    if "decid" in q.lower() and any("decid" in p.lower() for p in prior):
        return "the word 'decide' is already used in this set; ask the same thing another way"
    return None


def review_question(client, q: str, qtype: str, passage: str, answer: str = "", context: str = "", prior=(), extra: str = "") -> str | None:
    """A second, cheap call that proofreads the question (typos, unclear reference, several asks). Returns the problem
    or None. If the reviewer itself fails, the question is not blocked."""
    user = (f"TYPE: {qtype}\nQUESTION: {q}\n<passage>\n{passage or '(not located)'}\n</passage>"
            + (("\nQUESTIONS ALREADY IN THIS SET:\n" + "\n".join(f"- {p}" for p in prior)) if prior else "")
            + (f"\nESSAY POINTS THE QUESTION DRAWS ON (it may combine several parts of the essay): {context}" if context else "")
            + (f"\nEXTENSION PROMPT (already answered by the person, about this same point): {extra}" if extra else "")
            + (f"\n<answer>\n{answer}\n</answer>" if answer else ""))
    try:
        d = ask_json(client, REVIEW_SYSTEM, user,
                     lambda d: None if isinstance(d.get("ok"), bool) else "ok must be true or false",
                     temperature=0, max_tokens=config.REVIEW_MAX_TOKENS, tries=2)
    except CallFailed:
        return None
    return None if d["ok"] else (str(d.get("problem") or "the reviewer found a defect").strip())


def write_question(client, target: Target, assignment: str = "", prior: list[str] = (), review: bool = True) -> dict:
    user = (f"TYPE: {target.qtype}\nINSTRUCTION: {TYPE_BRIEFS[target.qtype]}\n"
            f"TARGET_TEXT: {target.target_text}\n"
            f"<passage>\n{target.passage or '(not located)'}\n</passage>\n"
            + (f"ASSIGNMENT: {assignment}\n" if assignment else "")
            + (f"EXTENSION PROMPT (the person already answered it, about this same point; do not reword it): {target.extra}\n" if target.extra else "")
            + (("QUESTIONS ALREADY IN THIS SET (do not repeat their wording, their opening or their kind of situation):\n"
                + "\n".join(f"- {p}" for p in prior) + "\n") if prior else ""))

    state = {"last": False}

    def on_try(i, tries):
        state["last"] = i == tries - 1

    def validate(d):
        q = d.get("question", "")
        limit = config.MAX_TRADEOFF_WORDS if target.qtype == "tradeoff" else config.MAX_QUESTION_WORDS
        hard = check_question(q, target.passage, limit, target.target_text + " " + assignment + " " + target.extra)
        if hard:
            return hard
        if target.extra:
            from .followups import restates
            if restates(q, [target.extra], 0.5):
                return "rewords the extension prompt; ask about the subject of the passage from scratch, in a general way, with a different situation"
            if LINK_BAD.search(q):
                return ("a rebuild question asks ONE thing about the subject, in general terms: no 'which of your reasons/points', no 'earlier' "
                        "or 'you said' or 'your essay'")
        if target.qtype == "tradeoff" and not TRADEOFF_SHAPE.search(q):
            return ("a trade-off question must ask under what conditions one side wins, e.g. 'Under what conditions would X be "
                    "better than Y, and when would it be the other way round?', 'Where would you draw the line between X and Y?' "
                    "or 'What would have to be true for X to outweigh Y?'")
        soft = check_variety(q, list(prior))
        if soft and not state["last"]:                      # a preference: relaxed on the final attempt so the set is not left short
            return soft
        return review_question(client, q, target.qtype, target.passage, context=target.target_text, prior=prior, extra=target.extra) if review else None

    d = ask_json(client, QUESTION_SYSTEM, user, validate, temperature=config.TEMPERATURE_WRITE,
                 max_tokens=config.WRITER_MAX_TOKENS, on_try=on_try)
    out = {"qtype": target.qtype, "question": d["question"].strip(),
           "good_answer_covers": str(d.get("good_answer_covers", "")).strip(),
           "passage": target.passage, "target_ids": target.target_ids, "target_text": target.target_text}
    if target.ext_index is not None:
        out.update(ext_index=target.ext_index, ext_question=target.extra)
    return out


def link_target(ext: dict, i: int) -> Target:
    """A viva target built on the same essay point as extension prompt i (a dict from extension.py)."""
    return Target("link", list(ext["target_ids"]), ext["target_text"], ext["passage"], extra=ext["question"], ext_index=i)


def generate_questions(client, graph: Graph, essay: str, *, n=None, assignment="", seed=None, log=print, links=None) -> list[dict]:
    """Writes questions until there are `n`. A target whose question cannot be made right (after its own retries) is
    skipped and the next candidate from the plan is tried, so the set is refilled. No more than config.MAX_PER_TYPE
    questions of one type are used unless that is the only way to fill the set. Stops at the end of the plan or when the
    spend limit is reached, and then returns what it has."""
    n = n or config.N_QUESTIONS
    plan = plan_targets(graph, essay, 3 * n, seed)            # same priority order as a plain plan, plus spare targets
    out, deferred = [], []
    linked_ids = set()
    for i, ext in enumerate((links or [])[:n]):               # first: one question per extension prompt, same point, new angle
        try:
            out.append(write_question(client, link_target(ext, i), assignment, [q["question"] for q in out]))
            linked_ids.add(ext["target_ids"][0])
        except CallFailed as e:
            log(f"  could not make a linked question for extension prompt {i + 1} ({e}); the slot goes to a plain question")
        except BudgetExceeded as e:
            log(f"  stopping: {e}")
            break
    plan = [t for t in plan if t.target_ids[0] not in linked_ids]
    n_plain = n

    def attempt(t):
        try:
            out.append(write_question(client, t, assignment, [q["question"] for q in out]))
        except CallFailed as e:
            log(f"  skipped a {t.qtype} question ({e}); trying another target")

    try:
        for t in plan:
            if len(out) >= n:
                break
            if sum(1 for q in out if q["qtype"] == t.qtype) >= config.MAX_PER_TYPE:
                deferred.append(t)
                continue
            attempt(t)
        for t in deferred:                                     # relax the per-type cap rather than leave the set short
            if len(out) >= n:
                break
            attempt(t)
    except BudgetExceeded as e:
        log(f"  stopping: {e}")
    if not out:
        raise CallFailed("no questions could be written")
    if len(out) < n:
        log(f"  only {len(out)} of {n} questions could be made (ran out of candidate targets or budget)")
    return out


def redo_questions(client, graph: Graph, essay: str, existing: list[dict], slots: list[int], *, assignment="", seed=None,
                   log=print, links=None) -> list[dict]:
    """Regenerate only the numbered slots (0-based) of a question set. Each new question uses a different essay target,
    prefers the same type as the one it replaces, never reuses a target of a kept question, and is checked against the
    kept questions like any other. A slot that cannot be redone keeps its old question."""
    out = list(existing)
    seed = random.SystemRandom().randrange(10 ** 6) if seed is None else seed
    claim_ids = {e.id for e in graph.of_kind("claim")}
    plan = plan_targets(graph, essay, 40, seed)
    for s in slots:
        kept = [q for i, q in enumerate(out) if i != s]
        kept_first = {q["target_ids"][0] for q in kept}
        claim_use = {}
        for q in kept:
            for i in q["target_ids"]:
                if i in claim_ids:
                    claim_use[i] = claim_use.get(i, 0) + 1
        old = out[s]
        if old.get("ext_index") is not None:                  # a linked question stays linked: new wording, same extension point
            i = old["ext_index"]
            if links and i < len(links):
                try:
                    nq = write_question(client, link_target(links[i], i), assignment, [q["question"] for q in kept])
                    from . import PROMPT_VERSION
                    nq["prompt_version"] = PROMPT_VERSION
                    out[s] = nq
                except CallFailed as e:
                    log(f"  slot {s + 1}: could not redo the linked question ({e}); keeping the old one")
            else:
                log(f"  slot {s + 1}: linked question but the extension prompts are not available; keeping the old one")
            continue
        opts = [t for t in plan if t.target_ids[0] not in kept_first and t.target_ids[0] != old["target_ids"][0]
                and not any(claim_use.get(i, 0) >= config.MAX_PER_CLAIM for i in t.target_ids if i in claim_ids)]
        opts.sort(key=lambda t: t.qtype != old["qtype"])        # same type first (stable, so plan order is kept)
        for t in opts[:6]:
            try:
                nq = write_question(client, t, assignment, [q["question"] for q in kept])
            except CallFailed as e:
                log(f"  slot {s + 1}: skipped a {t.qtype} target ({e}); trying another")
                continue
            from . import PROMPT_VERSION
            nq["prompt_version"] = PROMPT_VERSION
            out[s] = nq
            break
        else:
            log(f"  slot {s + 1}: could not make a better question; keeping the old one")
    return out