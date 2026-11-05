"""vivaplan: fills a fixed viva blueprint with questions sampled from a student's own world-tree. Pure Python, no model calls.

Fixed: the question TYPES, their counts (the blueprint), the follow-up rules. Sampled: the instances, from the student's own essay,
with an unpublished seed. The extension (a Flip: one axiom changed, the student writes the new tree) comes BEFORE the viva so that the
Place and Counter questions can use the student's new stances.

Anchor types (rebuild, recall) can be answered from memory: a pass is weak evidence, a fail is worth a human's look. Transfer types
(elicit, flip, counter, place) cannot be memorised. Markers compare WORDING only. 'not evident' is not 'wrong'. Nothing here accuses
anyone; it is a reading aid for a human who decides (Visq supports a human decision)."""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from . import vivatext as VT
from . import worldalg as WA

REBUILD, RECALL, ELICIT, FLIP, COUNTER, PLACE = "rebuild", "recall", "elicit", "flip", "counter", "place"
CLARIFY = "clarify"          # extension step 1: not part of the viva blueprint
ANCHORS = frozenset({REBUILD, RECALL})
DEFAULT_BLUEPRINT = ((REBUILD, 2), (RECALL, 1), (ELICIT, 1), (FLIP, 1), (COUNTER, 1), (PLACE, 1))

FOLLOWUPS = VT.FOLLOWUPS
GENERIC = re.compile(r"\b(i don'?t know|not sure|nothing|common sense|obvious(ly)?|it was true|it is true|just true|everyone knows)\b", re.I)


clip, LEAD = VT.clip, VT.LEAD


def src_text(s: WA.Stance) -> str:
    """The text a question is built from: the model's short restatement when there is one, else the student's sentence (the question then
    restates it again, so it is never a word for word quote)."""
    t = " ".join((s.text or "").split())
    return t if t and "…" not in t else " ".join((s.quote or s.text).split())


OBJ = re.compile(r"\b(objections?|objects?|worr(?:y|ies|ied)|fears?|concerns?|critics?|criticis[em]\w*|drawbacks?|downsides?|risks?|problems?|costs?|unfair|opponents?)\b", re.I)


def is_objection(s: WA.Stance) -> bool:
    return s.kind in ("claim", "concession") and bool(OBJ.search(s.quote or s.text))


def rich(s: WA.Stance, n: int = 5) -> bool:
    """Enough content words to make a question out of ('Finally, the council budget matters.' is not)."""
    return len(WA.toks(LEAD.sub("", src_text(s), count=1))) >= n


@dataclass
class Item:
    id: str
    type: str
    anchor: bool
    world: str
    targets: list                  # stance ids (or an axiom id for a flip)
    prompt: str
    expected: dict
    difficulty: dict
    weak: bool = False
    template: str = ""
    slots: dict = field(default_factory=dict)
    ex_kind: str = ""              # which worked example the '?' button shows


def mk(typ, anchor, wid, targets, tkey, slots, expected, diff, ex_kind=None, rng=None, **extra) -> Item:
    tpl = (rng or random).choice(VT.TPL[tkey])
    return Item("", typ, anchor, wid, targets, VT.fill(tpl, slots, **extra), expected, diff, template=tpl, slots=dict(slots), ex_kind=ex_kind or tkey)


@dataclass
class Plan:
    items: list
    skipped: list                  # (type, reason): never silently substituted
    insufficient: bool
    reason: str
    seed: object
    avoid: frozenset = frozenset()          # axiom ids the extension already changed: the viva does not flip them again


def _stratified(cands, depth_of, rng, used, avoid_roots, root_of):
    pool = [c for c in cands if c not in used]
    fresh = [c for c in pool if root_of(c) not in avoid_roots] or pool
    if not fresh:
        return None
    buckets: dict = {}
    for c in fresh:
        buckets.setdefault(min(depth_of(c), 3), []).append(c)
    return rng.choice(buckets[rng.choice(sorted(buckets))])


_NOT_FACT = re.compile(r"\b(want|wants|wanted|wish|wishes|prefer|prefers|should|ought|must|need to|needs to|have to|hope|hopes|"
                       r"believe|believes|value|values|care|cares|aim|aims|goal|goals|like|likes|desire|"
                       r"i|my|me|we|our|us|because|therefore|thus|hence|matters)\b", re.I)


def is_fact(text: str) -> bool:
    """A flip must change a fact about the situation, not what someone wants or thinks should happen. Wishes, values, duties
    and first-person statements are left out: 'Imagine you did not want X' is confusing, 'Imagine buses were poor' is not."""
    return bool(text) and not _NOT_FACT.search(text)


def choose_flip(u: WA.Universe, rng: random.Random, world_id: str = "w0", exclude=()):
    """The axiom to flip for the extension or a Flip question: a keystone when dependencies are recorded, else a stated/given axiom,
    sampled among the first few so that it is not predictable. Returns (world, axiom) or None."""
    w = u.worlds.get(world_id)
    if w is None or w.ghost:
        return None
    ranked = []
    for aid in w.rests:
        n = len(WA.cascade(w, aid).fallen)
        if n and aid not in exclude and is_fact(w.axioms[aid].text):
            ranked.append((-n, aid))
    pool = [w.axioms[a] for _, a in sorted(ranked)[:3] if a in w.axioms]
    if not pool:
        pool = [a for a in w.axioms.values() if a.source in ("stated", "given") and len(WA.toks(a.text)) >= 4 and a.id not in exclude and is_fact(a.text)]
    return (w, rng.choice(pool)) if pool else None


MIN_AXIOMS = 2


def clarifying_items(u: WA.Universe, seed=0, max_n: int = 2) -> list:
    """Extension step 1. A world that says too little about what it takes for granted (fewer than MIN_AXIOMS stated, given or elicited
    axioms), including the implicit world of an essay that states no background at all, gets a question about one of its main points.
    The answers are stored as the student's own assumptions (attach_elicited), so they can also be flipped. At most max_n questions."""
    rng = random.Random(f"clar:{seed}")
    items = []
    gaps = [w for w in u.real() if sum(a.source in ("stated", "given", "elicited") for a in w.axioms.values()) < MIN_AXIOMS and w.stances]
    for w in gaps:
        cands = [w.stances[r] for r in w.roots() if w.stances[r].kind == "claim" and rich(w.stances[r], 4)]
        cands.sort(key=lambda st: -len(w.support(st.id)))
        want = max_n if len(gaps) == 1 else 1
        for st in cands[:3][:want] if len(gaps) == 1 else ([rng.choice(cands[:3])] if cands else []):
            if len(items) >= max_n:
                break
            it = mk(CLARIFY, False, w.id, [st.id], "clarify", {"q": src_text(st)}, {}, _diff(w, st), rng=rng)
            it.id = f"c{len(items) + 1}"
            items.append(it)
    return items


def store_clarification(u: WA.Universe, item: Item, answer: str):
    """Keep a clarifying answer as an assumption of that world, resting on the point it was asked about. Returns the axiom or None."""
    if is_generic(answer) or item.type != CLARIFY:
        return None
    return WA.attach_elicited(u.worlds[item.world], item.targets[0], " ".join(answer.split()))


def extension_prompt(u: WA.Universe, seed=0, minutes: int = 6, world_id: str = "w0", para: dict | None = None):
    """Extension step 2: add a ghost world by changing ONE thing the essay (or the student's own clarifying answer) takes for granted.
    Returns (ghost world, prompt) or (None, reason). Call it AFTER the clarifying answers are stored, so that those can be chosen.
    When wired into Visq, the wording would be rewritten by the model and checked by visq.extension.check_extension."""
    ch = choose_flip(u, random.Random(f"ext:{seed}"), world_id)
    if ch is None:
        return None, "no stated or given fact to change"
    w, a = ch
    ghost = WA.flip(w, a.id)
    u.worlds[ghost.id] = ghost
    return ghost, VT.fill(VT.TPL["ext"][0], {"q": a.text}, para, n=minutes)


def scenario_inputs(u: WA.Universe):
    """What the model sees to invent a what-if: (world, main claim, the world's background statements). No title, no personal data."""
    ws = [w for w in u.real() if w.stances]
    if not ws:
        return None
    w = max(ws, key=lambda x: len(x.stances))
    roots = sorted(w.roots(), key=lambda r: -len(w.support(r)))
    claim = src_text(w.stances[roots[0]]) if roots else ""
    axs = [a for a in w.axioms.values() if a.source in ("stated", "given", "elicited") and len(WA.toks(a.text)) >= 4][:8]
    return (w, claim, axs) if axs and claim else None


def extension_from_scenario(u: WA.Universe, w: WA.World, axiom: WA.Axiom, text: str, minutes: int = 6):
    """Extension step 2 with a what-if from outside the essay. The ghost drops the axiom the what-if bears on, so the viva machinery
    (diff, place, never flipping the same assumption twice) works as before."""
    ghost = WA.flip(w, axiom.id)
    u.worlds[ghost.id] = ghost
    return ghost, VT.TPL["ext_scn"][0].format(q=text, n=minutes)


# ---- reading the extension answer by the four words the prompt used: keep / weaken / drop / add
_VERBS = [("drop", r"remove|drop|delete|abandon|discard|scrap|get rid of|cut out|cut|give up"),
          ("weaken", r"weaken|soften|reduce|downplay|tone down|play down|qualify"),
          ("keep", r"keep|retain|stand by|hold on to|maintain|still make|still use|still say"),
          ("add", r"add|include|introduce|bring in|put in")]
_VERB_ANY = "|".join(v for _, v in _VERBS)
_LEAD = re.compile(r"^(?:(?:and|but|also|then|so|now|next|finally|first|second|lastly),?\s+)*(?:(?:i|we)\s+(?:would|will|'d|could|might|should|can|do|did)?\s*)?"
                   r"(?:(?:also|then|probably|still|definitely)\s+)*", re.I)
_REST = re.compile(r"^(?:(?:the|a|an|this|my|new|another|extra|further|one|entirely|completely|altogether|fully|slightly|somewhat)\s+)*"
                   r"(?:points?|arguments?|ideas?|reasons?|claims?|parts?)?\s*(?:(?:entirely|completely|altogether|fully)\s+)?"
                   r"(?:(?:saying|about|that|on|of|which says)\s+)*", re.I)
_SPLIT = re.compile(r"(?<=[.!?])\s+|\s+(?:and|but)\s+(?=(?:i\s+would\s+)?(?:" + _VERB_ANY + r")\b)", re.I)


def ext_moves(text: str) -> list:
    """Split the extension answer into moves: [{'kind': keep|weaken|drop|add|other, 'text': the point itself, 'sentence': as written}].
    A sentence with none of the four words is 'other'. Reading aid only; it looks at words, not meaning."""
    out = []
    for sent in (x.strip() for x in _SPLIT.split(text or "") if x and x.strip()):
        low = sent.strip()
        lead = _LEAD.match(low)
        body = low[lead.end():] if lead else low
        kind, rest = "other", low
        for k, v in _VERBS:
            m = re.match(rf"(?:{v})\b\s*", body, re.I)
            if m:
                kind, rest = k, body[m.end():]
                break
        else:
            m = re.search(rf"\b(?:would|will|could|'d)\s+(?:{_VERB_ANY})\b", low, re.I)
            if m:
                for k, v in _VERBS:
                    m2 = re.match(rf"(?:would|will|could|'d)\s+(?:{v})\b\s*", low[m.start():], re.I)
                    if m2:
                        kind, rest = k, low[m.start() + m2.end():]
                        break
        if kind != "other":
            rest = _REST.sub("", rest, count=1)
        rest = re.split(r",?\s+which\s+i\b", rest, maxsplit=1)[0].strip(" .,;")
        if len(WA.toks(rest)) >= 3:
            out.append({"kind": kind, "text": rest[:1].upper() + rest[1:] if rest[:1].islower() and kind == "other" else rest, "sentence": sent})
    return out


def graft_moves(ghost: WA.World, moves: list) -> list:
    """The student's written world: what they keep, weaken or add (and sentences with no cue). Dropped points are not part of it.
    Added points get kind 'added' so that the Place question can use only genuinely new points."""
    ids = WA.graft(ghost, [{"text": m["text"], "parent": None, "quote": m.get("sentence", m["text"])} for m in moves if m["kind"] != "drop"])
    keep = [m for m in moves if m["kind"] != "drop"]
    for sid, m in zip(ids, keep):
        ghost.stances[sid].kind = "added" if m["kind"] == "add" else "claim"
    return ids


def flipped_text(u: WA.Universe, ghost: WA.World) -> str:
    return u.worlds[ghost.base].axioms[ghost.flipped].text


class _Ctx:
    def __init__(self, u, rng, extension):
        self.u, self.rng, self.extension = u, rng, extension
        self.real = u.real()
        self.used_roots, self.used_nodes = set(), set()
        self.avoid = {extension.flipped} if extension is not None and extension.flipped else set()

    def all(self):
        return [(w, s) for w in self.real for s in w.stances.values()]

    def pick(self, w_s_list):
        by = {(w.id, s.id): (w, s) for w, s in w_s_list}
        keys = list(by)
        k = _stratified(keys, lambda k: by[k][0].depth(k[1]), self.rng, self.used_nodes, self.used_roots, lambda k: (k[0], by[k][0].root_of(k[1])))
        return by[k] if k else (None, None)

    def take(self, w, s):
        self.used_nodes.add((w.id, s.id))
        self.used_roots.add((w.id, w.root_of(s.id)))


def _diff(w, s):
    return {"depth": w.depth(s.id), "support": len(w.support(s.id)), "root": w.root_of(s.id), "section": s.section}


def _rebuild(c, n):
    cands = [(w, s) for w, s in c.all() if s.parent and not s.weak and s.kind in ("claim", "evidence", "concession") and rich(s)]
    w, s = c.pick(cands)
    if not s:
        return None, "no strong child-parent edge left"
    c.take(w, s)
    p = w.stances[s.parent]
    return mk(REBUILD, True, w.id, [s.id], "rebuild", {"q": src_text(s)}, rng=c.rng, expected=
                {"parent": p.text, "ancestors": [w.stances[a].text for a in w.ancestors(s.id)],
                 "related": [w.stances[k].text for k in sorted(w.support(w.root_of(s.id)) | {w.root_of(s.id)}) if k != s.id]}, diff=_diff(w, s)), ""


def _recall(c, n):
    cands = [(w, s) for w, s in c.all() if s.kind == "claim" and len(w.children(s.id, True)) >= 2 and rich(s, 4)]
    top = sorted(cands, key=lambda ws: -len(ws[0].support(ws[1].id)))[:6]
    w, s = c.pick(top)
    if not s:
        return None, "no stance with two or more strongly linked supports"
    c.take(w, s)
    return mk(RECALL, True, w.id, [s.id], "recall_obj" if is_objection(s) else "recall", {"q": src_text(s)}, rng=c.rng,
              expected={"children": [w.stances[k].text for k in w.children(s.id, True)]}, diff=_diff(w, s)), ""


def _elicit(c, n):
    cands = [(w, w.stances[r]) for w in c.real for r in w.roots() if w.stances[r].kind == "claim" and rich(w.stances[r], 4)]
    w, s = c.pick(cands)
    if not s:
        return None, "no thesis to ask about"
    c.take(w, s)
    return mk(ELICIT, False, w.id, [s.id], "elicit", {"q": src_text(s)}, rng=c.rng, expected={}, diff=_diff(w, s)), ""


def _flip(c, n):
    for wid in [w.id for w in c.real]:
        ch = choose_flip(c.u, c.rng, wid, c.avoid)
        if ch:
            w, a = ch
            cs = WA.cascade(w, a.id)
            exp = {"known": cs.known, "fallen": [w.stances[i].text for i in sorted(cs.fallen)] if cs.known else [],
                   "standing": [w.stances[i].text for i in sorted(cs.standing)] if cs.known else []}
            return mk(FLIP, False, w.id, [a.id], "flip", {"q": a.text}, rng=c.rng, expected=exp,
                      diff={"source": a.source, "dependants_known": cs.known}), ""
    return None, "no stated or given fact to change"


def _counter(c, n):
    real = c.real
    for i, w1 in enumerate(real):                              # the same point in two worlds
        for w2 in real[i + 1:]:
            m = WA.counterparts(w1, w2, 0.4)
            ps = [(a, b) for a, b in sorted(m.items()) if not w1.stances[a].weak and not w2.stances[b].weak]
            if ps:
                a, b = c.rng.choice(ps)
                return mk(COUNTER, False, w1.id, [a], "counter_cross", {"q": src_text(w1.stances[a]), "o": src_text(w2.stances[b])}, rng=c.rng,
                          expected={"mode": "cross-world", "other": w2.stances[b].text}, diff=_diff(w1, w1.stances[a]), ex_kind="counter"), ""
    objs = [(w, s) for w, s in c.all() if is_objection(s) and not s.weak and rich(s, 4) and w.children(s.id, True) and (w.id, s.id) not in c.used_nodes]
    w, s = c.pick(objs)
    if s:                                                                  # an objection the essay raises and answers: ask for the reply
        c.take(w, s)
        reps = [w.stances[k].text for k in w.children(s.id, True)]
        return mk(COUNTER, False, w.id, [s.id], "counter_obj", {"q": src_text(s)}, rng=c.rng, ex_kind="counter",
                  expected={"mode": "objection", "other": reps[0], "replies": reps, "related": [w.stances[k].text for k in sorted(w.support(w.root_of(s.id)) | {w.root_of(s.id)}) if k != s.id]},
                  diff=_diff(w, s)), ""
    cands = [(w, s) for w, s in c.all() if s.kind == "concession" and s.parent and not s.weak and rich(s)]
    w, s = c.pick(cands)
    if not s:
        return None, "no second world, no objection with a reply, and no concession in the essay"
    c.take(w, s)
    return mk(COUNTER, False, w.id, [s.id], "counter_conc", {"q": src_text(s)}, rng=c.rng, ex_kind="counter",
              expected={"mode": "concession", "other": w.stances[s.parent].text,
                        "related": [w.stances[k].text for k in sorted(w.support(w.root_of(s.id)) | {w.root_of(s.id)}) if k != s.id]}, diff=_diff(w, s)), ""


def _closest(base: WA.World | None, text: str):
    """The essay point whose WORDS are closest to `text`, with the overlap. Wording only."""
    if base is None or not base.stances:
        return None, 0.0
    sid = max(base.stances, key=lambda i: WA.jaccard(text, base.stances[i].text))
    return base.stances[sid].text, WA.jaccard(text, base.stances[sid].text)


def _place(c, n):
    g = c.extension
    if g is None or not g.stances:
        return None, "no extension text to place"
    base = c.u.worlds.get(g.base)
    ids = [i for i, s in g.stances.items() if s.kind == "added" and not s.weak and len(WA.toks(s.text)) >= 3]
    if not ids:
        return None, "the extension added no new point to place"
    sid = c.rng.choice(ids)
    s = g.stances[sid]
    near, x = _closest(base, s.text)
    return mk(PLACE, False, g.id, [sid], "place", {"q": src_text(s)}, rng=c.rng,
              expected={"closest": near, "closeness": round(x, 2)}, diff={"added_point": True}), ""


BUILDERS = {REBUILD: _rebuild, RECALL: _recall, ELICIT: _elicit, FLIP: _flip, COUNTER: _counter, PLACE: _place}


def refresh_flip(p: Plan, u: WA.Universe) -> bool:
    """After an elicit answer has been stored (attach_elicited), turn the planned flip into one about the student's OWN assumption: it is
    already attached to a stance, so what should fall is known and the answer can point to named cases in the essay. Returns True if replaced."""
    fl = next((i for i in p.items if i.type == FLIP and not i.expected.get("elicited")), None)
    if fl is None:
        return False
    for w in u.real():
        for a in reversed(list(w.axioms.values())):
            if a.source != "elicited" or not a.node or a.node not in w.stances or a.id in p.avoid:
                continue
            cs = WA.cascade(w, a.id)
            if not cs.known:
                continue
            kin = sorted(w.support(a.node) | {w.root_of(a.node)})
            fl.template, fl.slots, fl.ex_kind = VT.TPL["flip_elicited"][0], {"q": a.text}, "flip_elicited"
            fl.prompt = VT.fill(fl.template, fl.slots)
            fl.targets, fl.world = [a.id], w.id
            fl.expected = {"known": True, "elicited": True, "fallen": [w.stances[i].text for i in sorted(cs.fallen)],
                           "standing": [w.stances[i].text for i in sorted(cs.standing)], "related": [w.stances[i].text for i in kin if i not in cs.fallen]}
            fl.difficulty = {"source": "elicited", "dependants_known": True}
            return True
    return False


def plan(u: WA.Universe, blueprint=DEFAULT_BLUEPRINT, seed=0, extension: WA.World | None = None,
         min_stances: int = 6, min_roots: int = 1) -> Plan:
    """Fill the blueprint from the student's tree. Deterministic for a (universe, seed). A type that cannot be filled is listed in
    `skipped` with the reason and is not replaced by another type. A tree that is too thin gets no questions at all
    (insufficient=True) and goes to a human."""
    rng = random.Random(f"viva:{seed}")
    real = u.real()
    stances = [s for w in real for s in w.stances.values()]
    roots = [r for w in real for r in w.roots()]
    strong = [s for s in stances if s.parent and not s.weak]
    if len(stances) < min_stances or len(roots) < min_roots or len(strong) < 2:
        return Plan([], [], True, f"insufficient structure: {len(stances)} stances, {len(roots)} theses, {len(strong)} strong edges", seed)
    c = _Ctx(u, rng, extension)
    items, skipped = [], []
    for typ, n in blueprint:
        for k in range(n):
            it, why = BUILDERS[typ](c, k)
            if it is None:
                skipped.append((typ, why))
                break
            it.id = f"q{len(items) + 1}"
            items.append(it)
    return Plan(items, skipped, False, "", seed, frozenset(c.avoid))


def plan_short(u: WA.Universe, seed=0, extension: WA.World | None = None, n_anchor: int = 1, n_transfer: int = 2, **kw) -> Plan:
    """A short viva: fill every type once, then keep a seeded random pick of n_anchor anchor questions and n_transfer transfer questions
    (only types that could be filled are candidates). Order stays anchors first, then transfer in blueprint order, so an elicit answer can
    still feed the flip. With a small n the same student can get a different mix on each attempt; that is the point, and also the cost:
    a given attempt may include no flip or place question."""
    full = plan(u, DEFAULT_BLUEPRINT[:0] + tuple((t, 1) for t, _ in DEFAULT_BLUEPRINT), seed, extension, **kw)
    if full.insufficient:
        return full
    rng = random.Random(f"short:{seed}")
    anc = [i for i in full.items if i.anchor]
    tra = [i for i in full.items if not i.anchor]
    keep = set(id(i) for i in rng.sample(anc, min(n_anchor, len(anc))) + rng.sample(tra, min(n_transfer, len(tra))))
    items = [i for i in full.items if id(i) in keep]
    for k, it in enumerate(items, 1):
        it.id = f"q{k}"
    return Plan(items, full.skipped, False, "", seed, full.avoid)


def slot_texts(p: Plan, extra=()) -> list:
    """Every piece of the student's text a question would restate: what to hand to a paraphraser."""
    return list(dict.fromkeys([v for it in p.items for v in it.slots.values()] + list(extra)))


def apply_paraphrase(p: Plan, para: dict) -> None:
    """Re-word each question from a {text: paraphrase} map (texts without a paraphrase keep the light offline restatement)."""
    for it in p.items:
        if it.template:
            it.prompt = VT.fill(it.template, it.slots, para)


def example_for(item: Item, n: int = 0, seed=0) -> str:
    """What the '?' button shows for this question: a worked example on a random different topic."""
    return VT.show_example(item.ex_kind or item.type, n, seed)


def coverage(p: Plan, u: WA.Universe) -> dict:
    """Which theses the viva touched, out of all the theses in the real worlds."""
    allr = {(w.id, r) for w in u.real() for r in w.roots()}
    hit = set()
    for it in p.items:
        w = u.worlds.get(it.world)
        if w is None or w.ghost or it.type == FLIP:
            continue
        for t in it.targets:
            if t in w.stances:
                hit.add((w.id, w.root_of(t)))
    return {"theses_probed": len(hit), "theses_total": len(allr)}


# ---------------------------------------------------------------- answers
def is_generic(answer: str, essay_text: str = "") -> bool:
    a = WA.toks(answer)
    if len(a) < 4 or GENERIC.search(answer or ""):
        return True
    if essay_text:
        return len(a & WA.toks(essay_text)) / len(a) < 0.3
    return False


def followup_for(item: Item, answer: str, essay_text: str = ""):
    """At most one follow-up per item. Returns (key, text) or None."""
    a = (answer or "").strip()
    if len(WA.toks(a)) < 3:
        return "none", FOLLOWUPS["none"]
    check_essay = "" if item.type in (ELICIT, FLIP, PLACE, CLARIFY) else essay_text    # new assumptions and new points need not use the essay's words
    if is_generic(a, check_essay):
        return "generic", FOLLOWUPS["generic"]
    if item.type in (ELICIT, FLIP):
        return item.type, FOLLOWUPS[item.type]
    return None


def _band(x: float, hi: float = 0.5, lo: float = 0.25) -> str:
    return "consistent with the essay" if x >= hi else "partly consistent" if x >= lo else "not evident"


def _with_related(marker: str, a: str, e: dict, detail: str) -> dict:
    """If the answer misses the exact point but uses the words of another point in the same part of the essay, say so (never more than 'partly')."""
    if marker == "not evident":
        x = max([WA.cover(a, z) for z in e.get("related", [])] or [0.0])
        if x >= 0.4:
            return {"marker": "partly consistent (matches another point in the same part of the essay)", "detail": detail + f"; related point {x:.2f}"}
    return {"marker": marker, "detail": detail}


def mark(item: Item, answer: str) -> dict:
    """A coarse reading aid for a human: how closely the WORDS of the answer match what the essay's own structure says. Not a score."""
    a = (answer or "").strip()
    if not a:
        return {"marker": "no answer", "detail": ""}
    t, e = item.type, item.expected
    if t == REBUILD:
        x = max([WA.cover(a, e["parent"])] + [WA.cover(a, z) for z in e["ancestors"]])
        return _with_related(_band(x), a, e, f"wording overlap with the parent point {x:.2f}")
    if t == RECALL:
        ch = e["children"]
        h = sum(WA.cover(a, k) >= 0.3 for k in ch)
        return {"marker": _band(h / len(ch), 0.99, 0.5) if ch else "no reference", "detail": f"mentions {h} of {len(ch)} supports in the essay"}
    if t == CLARIFY:
        return {"marker": "new information (stored as an assumption of the essay)" if not is_generic(a) else "generic answer", "detail": ""}
    if t == ELICIT:
        return {"marker": "new information (stored as an elicited axiom)" if not is_generic(a) else "generic answer", "detail": ""}
    if t == FLIP:
        if not e.get("known"):
            return {"marker": "no reference (what depends on this axiom is not recorded)", "detail": ""}
        f = sum(WA.cover(a, k) >= 0.3 for k in e["fallen"])
        if not e["fallen"]:
            return {"marker": "no reference", "detail": ""}
        return _with_related(_band(f / len(e["fallen"]), 0.5, 0.25), a, e, f"mentions {f} of {len(e['fallen'])} predicted to fall")
    if t == COUNTER:
        x = max([WA.cover(a, z) for z in e.get("replies", [e["other"]])])
        return _with_related(_band(x, 0.4, 0.2), a, e, f"wording overlap with the essay's own reply {x:.2f}" if e["mode"] == "objection" else f"wording overlap with the {e['mode']} point {x:.2f}")
    if t == PLACE:
        near = e.get("closest")
        if not near:
            return {"marker": "a new point; whether it adds to, changes or goes against the essay is for a human to judge", "detail": ""}
        x = WA.cover(a, near)
        return {"marker": "for a human to judge (whether it adds to, changes or goes against the essay is not something wording can show)",
                "detail": f"the added point is closest by wording to: \"{near}\"; the answer uses that point's words {x:.2f}"}
    return {"marker": "no reference", "detail": ""}


NOTE_HEAD = ("Reading aid for a human reviewer. Markers compare wording with the essay's own structure; 'not evident' is not 'wrong', "
             "and nothing here is a finding about a person. Rebuild and recall can be answered from memory (a pass is weak evidence); "
             "elicit, flip, counter and place cannot. Soft-linked edges are never used.")


def ext_section(u: WA.Universe, ghost: WA.World | None, clarify: list, ext_prompt: str, ext_text: str, overtime: bool = False, moves: list | None = None) -> str:
    """The extension part of the note: what the student said they take for granted, the one change they were given, what they wrote, and
    what they kept, weakened, dropped and added (read by those four words), each with the essay point closest by wording. A reading aid."""
    out = ["## Extension (before the viva)", ""]
    for it, ans in clarify:
        out += [f"- clarifying question: {it.prompt}", f"  - answer: {ans or '(none)'}"]
    out.append(f"- the change given: {ext_prompt}")
    out.append(f"- what the student wrote: {ext_text or '(nothing)'}")
    if moves:
        base = u.worlds[ghost.base] if ghost is not None else None
        out.append("- read by the words keep / weaken / drop / add (a sentence with none of them is 'other'; wording only):")
        label = {"keep": "kept", "weaken": "weakened", "drop": "dropped", "add": "added", "other": "other"}
        for m in moves:
            near, x = _closest(base, m["text"])
            tail = (f" - closest essay point (wording overlap {x:.2f}): {near}" if near and x >= 0.3 else " - no close match in the essay")
            out.append(f"  - {label[m['kind']]}: {m['text']}{tail}")
    if overtime:
        out.append("- the extension took longer than the time shown")
    return "\n".join(out + [""])


def note(p: Plan, answers: dict, u: WA.Universe | None = None, essay_text: str = "", ext_md: str = "") -> str:
    out = ["# Viva note", "", NOTE_HEAD, ""] + ([ext_md] if ext_md else [])
    if p.insufficient:
        return "\n".join(out + [f"No questions were generated: {p.reason}. For a human to look at.", ""])
    for it in p.items:
        ans = answers.get(it.id, "")
        if isinstance(ans, dict):
            ans, fu = ans.get("answer", ""), ans.get("followup", "")
        else:
            fu = ""
        m = mark(it, ans)
        out += [f"## {it.id} · {it.type}{' (anchor)' if it.anchor else ''}", f"- asked: {it.prompt}",
                f"- answer: {ans or '(none)'}", f"- reading: {m['marker']}" + (f" ({m['detail']})" if m["detail"] else ""),
                f"- difficulty: {it.difficulty}"]
        if fu:
            out.append(f"- follow-up answer: {fu}")
        out.append("")
    if u is not None:
        cv = coverage(p, u)
        out.append(f"Coverage: {cv['theses_probed']} of {cv['theses_total']} theses touched.")
    for typ, why in p.skipped:
        out.append(f"Not asked ({typ}): {why}.")
    return "\n".join(out) + "\n"