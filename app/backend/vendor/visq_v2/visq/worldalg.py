"""worldalg: the objects and operations of the world-tree model. Pure Python, no model calls, no network.

Vocabulary (a proposal; rename freely):
  Universe  the set of worlds an essay argues in.
  World     axioms + a forest of stances.
  Axiom     an assumption: stated (framing background), given (definition/spec/case fact), delta (added by a change),
            elicited (the student said it in the viva). Never extracted from the text by guesswork.
  Stance    an untyped claim in a tree. Thesis = a root. Ground = a leaf.
  Entailment  an edge: the children plus the world's axioms deduce the parent. A soft link (weak=True) is never scored.
  Delta     axioms added / removed / changed. Variant = base + delta. Island = a world that is independent of w0.
  Ghost     a world the student helps create: an axiom flipped, and the student writes the tree for it.
Operations: support, pillars, walk, apply, diff, counterparts, flip, graft, attach, attach_elicited, cascade, keystone.

Everything here is a MODEL of an argument, not a proof. Edges are heuristic. Outputs are prompts for a human reader and
never verdicts about a person."""
from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field

STOP = frozenset("""the a an of to in on and or but is are was were be been it this that these those for with as by at from not no so if
then than there their its they them which who whom what when where why how can could would should may might will shall do does did has have
had also more most such very into about over under between you your i we our he she his her one""".split())
_TOK = re.compile(r"[a-z0-9]+")


def stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[:-len(suf)]
    return w


def toks(s: str) -> set:
    return {stem(w) for w in _TOK.findall((s or "").lower()) if w not in STOP and len(w) > 2}


def jaccard(a: str, b: str) -> float:
    A, B = toks(a), toks(b)
    return len(A & B) / len(A | B) if A and B else 0.0


def cover(answer: str, target: str) -> float:
    """Share of the target's content words that the answer uses. Wording only; it knows nothing about meaning."""
    A, B = toks(answer), toks(target)
    return len(A & B) / len(B) if A and B else 0.0


_SPLIT = re.compile(r",\s+and\s+|;\s+|,\s+but\s+", re.I)


def split_compound(text: str) -> list:
    """One change at a time: a stated sentence that joins two assumptions ('..., and ...') becomes two axioms. A part is kept
    only if it has enough words to stand alone; otherwise the sentence stays whole."""
    parts = [p.strip(" .") for p in _SPLIT.split(text.strip())]
    if len(parts) > 1 and all(len(toks(p)) >= 4 for p in parts):
        return [p[0].upper() + p[1:] + "." for p in parts]
    return [text.strip()]


def axiom_id(text: str) -> str:
    return "a" + hashlib.md5(" ".join(sorted(toks(text))).encode()).hexdigest()[:6]


@dataclass
class Axiom:
    id: str
    text: str
    source: str            # stated | given | delta | elicited
    label: str = ""        # the essay sentence label it came from, if any
    node: str = ""         # for an elicited axiom: the stance it was said about


def make_axiom(text: str, source: str, label: str = "", node: str = "") -> Axiom:
    return Axiom(axiom_id(text), text.strip(), source, label, node)


@dataclass
class Stance:
    id: str
    text: str
    parent: str | None = None
    world: str = "w0"
    kind: str = "claim"
    quote: str = ""
    label: str = ""
    weak: bool = False
    score: float = 1.0
    section: str = ""


@dataclass
class Delta:
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    changed: list = field(default_factory=list)    # (from_text, to_text)

    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


@dataclass
class World:
    id: str
    name: str = ""
    base: str | None = None
    independent: bool = False
    ghost: bool = False
    flipped: str = ""                                  # for a ghost: the axiom id that was flipped
    axioms: dict = field(default_factory=dict)         # axiom id -> Axiom
    stances: dict = field(default_factory=dict)        # stance id -> Stance, in document order
    rests: dict = field(default_factory=dict)          # axiom id -> set of stance ids that need it ("Rests-on", unknown by default)
    delta: Delta = field(default_factory=Delta)

    # --- shape of the tree
    def roots(self) -> list:
        return [i for i, s in self.stances.items() if s.parent is None]

    def children(self, i: str, strong_only: bool = False) -> list:
        return [j for j, s in self.stances.items() if s.parent == i and not (strong_only and s.weak)]

    def parent_of(self, i: str):
        return self.stances[i].parent

    def depth(self, i: str) -> int:
        d = 0
        while self.stances[i].parent is not None:
            i = self.stances[i].parent
            d += 1
        return d

    def root_of(self, i: str) -> str:
        while self.stances[i].parent is not None:
            i = self.stances[i].parent
        return i

    def ancestors(self, i: str) -> list:
        out = []
        while self.stances[i].parent is not None:
            i = self.stances[i].parent
            out.append(i)
        return out

    def support(self, i: str) -> set:
        """Support(n): every stance underneath n, i.e. everything offered for it."""
        out, todo = set(), [i]
        while todo:
            for c in self.children(todo.pop()):
                out.add(c)
                todo.append(c)
        return out


@dataclass
class Universe:
    worlds: dict = field(default_factory=dict)

    def real(self) -> list:
        return [w for w in self.worlds.values() if not w.ghost]

    @classmethod
    def from_result(cls, res: dict) -> "Universe":
        """Build from a tree_algo result dict (the .algo.json). Stated axioms are the framing background; givens are the
        sentences the labeller marked 'given'; a variant gets its base's axioms with its delta applied."""
        fr = res.get("framing") or {}
        stated = [make_axiom(t, "stated", b.get("label", "")) for b in fr.get("background", []) if b.get("text") for t in split_compound(b["text"])]
        u = cls()
        for wd in res["worlds"]:
            w = World(wd["id"], wd.get("name", ""), wd.get("base"), bool(wd.get("independent")))
            d = wd.get("delta") or {}
            w.delta = Delta(list(d.get("added", [])), list(d.get("removed", [])), [tuple(c) if not isinstance(c, dict) else (c["from"], c["to"]) for c in d.get("changed", [])])
            if w.id == "w0":
                w.axioms = {a.id: a for a in stated}
            elif w.base in u.worlds and not w.independent:
                w.axioms = apply_delta(u.worlds[w.base].axioms, w.delta)
            for g in wd.get("axioms", []):
                a = make_axiom(g["text"], "given", g.get("label", ""))
                w.axioms[a.id] = a
            u.worlds[w.id] = w
        for n in res["nodes"]:
            w = u.worlds.get(n["world"])
            if w is None:
                continue
            w.stances[n["id"]] = Stance(n["id"], n["text"], n.get("parent"), w.id, n.get("kind", "claim"), n.get("quote", ""),
                                        n.get("label", ""), bool(n.get("weak")), n.get("score", 1.0), n.get("section", ""))
        return u


# ---------------------------------------------------------------- deltas and variants
def _find_axiom(axioms: dict, text: str, thresh: float = 0.5):
    best = max(axioms.values(), key=lambda a: jaccard(a.text, text), default=None)
    return best if best is not None and jaccard(best.text, text) >= thresh else None


def apply_delta(axioms: dict, delta: Delta) -> dict:
    """Apply(base, delta) on the axiom set: returns a new dict of axioms."""
    out = dict(axioms)
    for t in delta.removed:
        a = _find_axiom(out, t)
        if a:
            out.pop(a.id)
    for old, new in delta.changed:
        a = _find_axiom(out, old)
        if a:
            out.pop(a.id)
        n = make_axiom(new, "delta")
        out[n.id] = n
    for t in delta.added:
        n = make_axiom(t, "delta")
        out[n.id] = n
    return out


def apply(world: World, delta: Delta, new_id: str) -> World:
    """A variant: the base world's axioms with the delta applied. Its tree is empty, because the trees come from whoever argues in it."""
    return World(new_id, f"{world.name} + change", world.id, False, False, "", apply_delta(world.axioms, delta), {}, {}, delta)


def flip(world: World, axiom_id: str, new_text: str | None = None, ghost_id: str | None = None) -> World:
    """Flip(w, a): a ghost world with axiom a removed (or replaced by new_text). One change only. The tree is empty; the student writes it."""
    a = world.axioms[axiom_id]
    d = Delta(removed=[a.text]) if not new_text else Delta(changed=[(a.text, new_text)])
    axioms = {k: v for k, v in world.axioms.items() if k != axiom_id}
    if new_text:
        n = make_axiom(new_text, "delta")
        axioms[n.id] = n
    return World(ghost_id or f"g.{world.id}.{axiom_id}", f"{world.name} without one assumption", world.id, False, True, axiom_id, axioms, {}, {}, d)


def graft(ghost: World, items: list) -> list:
    """Graft(g, items): add the student's stances to a ghost. items = [{'text':..., 'parent': index-or-None}], parents earlier in the list."""
    ids = []
    for k, it in enumerate(items):
        sid = f"{ghost.id}.s{len(ghost.stances) + 1}"
        p = ids[it["parent"]] if it.get("parent") is not None else None
        ghost.stances[sid] = Stance(sid, it["text"], p, ghost.id, it.get("kind", "claim"), it.get("quote", it["text"]))
        ids.append(sid)
    return ids


def graft_result(ghost: World, res: dict) -> list:
    """Graft the stances of a tree_algo result (run on the student's extension text) into a ghost."""
    ids = {}
    for n in res["nodes"]:
        sid = f"{ghost.id}.{n['id']}"
        ids[n["id"]] = sid
        ghost.stances[sid] = Stance(sid, n["text"], ids.get(n.get("parent")), ghost.id, n.get("kind", "claim"), n.get("quote", ""),
                                    n.get("label", ""), bool(n.get("weak")), n.get("score", 1.0))
    return list(ids.values())


def attach(world: World, axiom_id: str, stance_ids) -> None:
    """Record that these stances need this axiom (Rests-on). Without this, Cascade has nothing to work from."""
    world.rests.setdefault(axiom_id, set()).update(stance_ids)


_LEAD_IN = re.compile(r"^\s*(?:i|we)\s+(?:took (?:it )?for granted|assumed|assume|was assuming|am assuming|believed|believe|think|thought|had to assume|had to believe)"
                      r"(?:\s+that)?[,:]?\s+", re.I)


_REASON = re.compile(r",?\s+(?:because|since|as|so that|which means|unlike|whereas|while)\s+", re.I)


def clean_assumption(text: str) -> str:
    """'I took for granted that X.' -> 'X.' so that the assumption can be shown or flipped as a statement of its own."""
    t = " ".join((text or "").split())
    t2 = _LEAD_IN.sub("", t, count=1)
    t = t2 if len(toks(t2)) >= 3 else t
    head = _REASON.split(t, maxsplit=1)[0].strip(" ,;")              # keep the assumption itself, not the reason given for it
    t = head if len(toks(head)) >= 4 else t
    t = t[0].upper() + t[1:] if t else t
    return t if t.endswith((".", "!", "?")) else t + "."


def attach_elicited(world: World, node_id: str, text: str) -> Axiom:
    """Elicit + Attach: store what the student said they assumed, as an axiom of this world resting on that stance."""
    a = make_axiom(clean_assumption(text), "elicited", node=node_id)
    world.axioms[a.id] = a
    attach(world, a.id, [node_id])
    return a


# ---------------------------------------------------------------- what-if
@dataclass
class Cascade:
    fallen: set
    standing: set
    known: bool        # False when nothing is recorded as resting on the axiom: then nothing can be predicted
    mode: str


def cascade(world: World, axiom_id: str, mode: str = "soft") -> Cascade:
    """Which stances fall if the axiom goes. Stances that rest on it fall. Then, going up: under 'soft' a parent falls when all its
    (strong) children fell; under 'hard' it falls when any strong child fell. Soft links never carry a fall upward."""
    direct = set(world.rests.get(axiom_id, ()))
    fallen: set = set()
    for i in sorted(world.stances, key=lambda k: -world.depth(k)):
        kids = world.children(i, strong_only=True)
        if i in direct:
            fallen.add(i)
        elif kids:
            f = [k in fallen for k in kids]
            if (mode == "hard" and any(f)) or (mode == "soft" and all(f)):
                fallen.add(i)
    return Cascade(fallen, set(world.stances) - fallen, bool(direct), mode)


def keystone(world: World, mode: str = "soft"):
    """The axiom whose removal would bring down the most stances: (axiom_id, size), or None if no dependencies are recorded."""
    best = None
    for aid in sorted(world.rests):
        n = len(cascade(world, aid, mode).fallen)
        if n and (best is None or n > best[1]):
            best = (aid, n)
    return best


def pillars(world: World, min_support: int = 2) -> list:
    """Stances with the largest support, biggest first (ties by document order)."""
    sized = [(len(world.support(i)), -k, i) for k, i in enumerate(world.stances)]
    return [i for n, _, i in sorted(sized, reverse=True) if n >= min_support]


def walk(world: World, rng: random.Random, steps: int = 1) -> list:
    """Walk: a random thesis (one that has children, if any does), then a random child, `steps` times. Returns the path."""
    roots = world.roots()
    withkids = [r for r in roots if world.children(r)]
    if not roots:
        return []
    cur = rng.choice(withkids or roots)
    path = [cur]
    for _ in range(steps):
        kids = world.children(cur)
        if not kids:
            break
        cur = rng.choice(kids)
        path.append(cur)
    return path


# ---------------------------------------------------------------- comparing worlds
def counterparts(w1: World, w2: World, thresh: float = 0.5) -> dict:
    """Counterpart(n): match stances of w1 to stances of w2 one to one by wording (greedy on word overlap). Returns {id1: id2}."""
    pairs = sorted(((jaccard(a.text, b.text), i, j) for i, a in w1.stances.items() for j, b in w2.stances.items()), key=lambda t: (-t[0], t[1], t[2]))
    m1, used2 = {}, set()
    for sc, i, j in pairs:
        if sc < thresh:
            break
        if i not in m1 and j not in used2:
            m1[i] = j
            used2.add(j)
    return m1


@dataclass
class Diff:
    delta: Delta
    shared: dict        # id in w1 -> id in w2
    only1: list         # dropped in w2
    only2: list         # new in w2
    rewired: list       # (id in w1, id in w2) whose parent counterparts differ


def diff(w1: World, w2: World, thresh: float = 0.5) -> Diff:
    """Diff(w1, w2): the axiom delta, and the stances reused, dropped, new and rewired."""
    if w2.base == w1.id and not w2.delta.empty():
        d = w2.delta
    else:
        d = Delta(added=[a.text for a in w2.axioms.values() if not _find_axiom(w1.axioms, a.text)],
                  removed=[a.text for a in w1.axioms.values() if not _find_axiom(w2.axioms, a.text)])
    m = counterparts(w1, w2, thresh)
    rev = set(m.values())
    rew = []
    for i, j in m.items():
        pi, pj = w1.stances[i].parent, w2.stances[j].parent
        if (pi is None) != (pj is None) or (pi is not None and m.get(pi) != pj):
            rew.append((i, j))
    return Diff(d, m, [i for i in w1.stances if i not in m], [j for j in w2.stances if j not in rev], rew)