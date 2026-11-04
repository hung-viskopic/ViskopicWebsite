"""AROA extraction: claims, reasons, evidences, counterarguments, rebuttals and the links between them.
Cached on disk by essay id, so re-running a command costs nothing."""
import json
from dataclasses import dataclass, field

from . import EXTRACTION_VERSION, config
from .llm import CallFailed, ask_json
from .prompts import EXTRACTION_SYSTEM
from .text import normalise

KINDS = {"claims": "claim", "reasons": "reason", "evidences": "evidence",
         "counterarguments": "counter", "rebuttals": "rebuttal"}


@dataclass
class Element:
    id: str
    kind: str            # claim | reason | evidence | counter | rebuttal
    text: str


@dataclass
class Graph:
    elements: dict = field(default_factory=dict)         # id -> Element
    relations: list = field(default_factory=list)        # (type, from_id, to_id)

    def of_kind(self, kind):
        return [e for e in self.elements.values() if e.kind == kind]

    def linked(self, el_id, rel_type=None, direction="out"):
        """Elements linked to `el_id` (out: el_id is the source, in: el_id is the target)."""
        out = []
        for t, a, b in self.relations:
            if rel_type and t != rel_type:
                continue
            if direction == "out" and a == el_id and b in self.elements:
                out.append(self.elements[b])
            if direction == "in" and b == el_id and a in self.elements:
                out.append(self.elements[a])
        return out

    def to_json(self):
        return {"elements": [e.__dict__ for e in self.elements.values()], "relations": self.relations}

    @classmethod
    def from_json(cls, d):
        g = cls()
        for e in d["elements"]:
            g.elements[e["id"]] = Element(**e)
        g.relations = [tuple(r) for r in d["relations"]]
        return g


def graph_from_extraction(d: dict) -> Graph:
    g = Graph()
    for key, kind in KINDS.items():
        for i, item in enumerate(d.get(key) or [], start=1):
            text = (item if isinstance(item, str) else (item or {}).get("text") or "").strip()
            if not text:
                continue
            eid = str((item.get("id") if isinstance(item, dict) else None) or f"{kind[0]}{i}")
            if eid in g.elements:
                eid = f"{eid}_{kind}{i}"
            g.elements[eid] = Element(eid, kind, text)
    for r in d.get("relationships") or []:
        if isinstance(r, dict) and r.get("from") in g.elements and r.get("to") in g.elements:
            g.relations.append((str(r.get("type") or "supports"), r["from"], r["to"]))
    return g


def _validate(d):
    g = graph_from_extraction(d)
    if not g.of_kind("claim"):
        return "no claims found"
    if len(g.elements) < 3:
        return "fewer than three argument elements found"
    return None


def extract(client, essay: str, *, cache=True, log=print) -> Graph:
    essay = normalise(essay)
    from .text import essay_id
    path = config.CACHE_DIR / f"{essay_id(essay)}_{EXTRACTION_VERSION}.json"
    if cache and path.exists():
        return Graph.from_json(json.loads(path.read_text(encoding="utf-8")))
    try:
        d = ask_json(client, EXTRACTION_SYSTEM, f"<essay>\n{essay}\n</essay>", _validate,
                     temperature=config.TEMPERATURE_EXTRACT, max_tokens=config.EXTRACTION_MAX_TOKENS)
    except CallFailed as e:
        raise CallFailed(f"could not extract an argument structure: {e}") from e
    g = graph_from_extraction(d)
    if cache:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(g.to_json()), encoding="utf-8")
    return g