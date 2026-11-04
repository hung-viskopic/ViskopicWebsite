"""The world-tree trial flow for the portal: essay -> (warning) -> clarifying questions -> a typed extension -> a short viva, with a
'?' example button at every question. No HTTP here; pilot.py calls this and webapp.py serves it.

How it works: after the essay is submitted a background job builds the world-tree (heuristics always; a zero-data-retention model for
sentence kinds, worlds and paraphrases when `use_llm` and a real model is available), then everything is a small state machine kept in
one JSON file per participant (data/worldviva/<code>.json): status clarify -> ext_intro -> extension -> viva -> done. The universe is
rebuilt from the saved tree plus the saved answers each time, so nothing lives only in memory.

Nothing here decides anything about a person. The reviewer note (note()) is a reading aid for a human."""
import json
import os
import secrets
import threading
import time
from dataclasses import asdict

from . import config
from . import treealgo as TA
from . import vivaplan as VP
from . import vivatext as VT
from . import worldalg as WA

EXT_GRACE_S = 10


class WorldViva:
    def __init__(self, dirpath, client_factory, error, background=False):
        self.dir = dirpath
        self.client_factory = client_factory
        self.error = error                                  # the exception class to raise for a problem to show the person
        self.background = background
        self.lock = threading.RLock()
        self._threads = []

    # ---- storage ------------------------------------------------------------------------------------------------
    def path(self, code):
        return self.dir / f"{code}.json"

    def has(self, code):
        return self.path(code).exists()

    def load(self, code):
        p = self.path(code)
        if not p.exists():
            raise self.error("no trial state for this person")
        return json.loads(p.read_text(encoding="utf-8"))

    def save(self, code, st):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path(code).with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path(code))

    def delete(self, code):
        self.path(code).unlink(missing_ok=True)

    def wait_background(self, timeout=60):
        for t in list(self._threads):
            t.join(timeout)

    # ---- starting: build the tree in the background -----------------------------------------------------------------
    def start(self, code, essay_text, topic, seconds, use_llm, full=False):
        with self.lock:
            st = {"status": "preparing", "error": "", "seed": secrets.randbelow(10 ** 6), "seconds": int(seconds), "full": bool(full),
                  "use_llm": bool(use_llm), "llm": False, "topic": topic, "elicited": [], "help": {}, "answers": {}, "clar_answers": {},
                  "ci": 0, "qi": 0, "step": "main", "created": time.time()}
            self.save(code, st)
        self._run(code, essay_text)

    def retry(self, code, essay_text):
        with self.lock:
            st = self.load(code)
            if st["status"] not in ("failed", "preparing"):
                raise self.error("nothing to retry")
            st.update(status="preparing", error="")
            self.save(code, st)
        self._run(code, essay_text)

    def _run(self, code, essay_text):
        if self.background:
            t = threading.Thread(target=self._prepare, args=(code, essay_text), daemon=True)
            self._threads.append(t)
            t.start()
        else:
            self._prepare(code, essay_text)

    def _client(self, st):
        """A real model client, or None (offline). The fake stand-in is not a language model, so it is treated as offline."""
        if not st.get("use_llm"):
            return None
        try:
            c = self.client_factory()
        except Exception:
            return None
        return None if c is None or type(c).__name__ == "FakeClient" else c

    def _prepare(self, code, essay_text):
        try:
            with self.lock:
                st = self.load(code)
            client = self._client(st)
            llm = bool(client)
            try:
                res = TA.run_essay(client, essay_text, st.get("topic", ""), llm)
            except Exception:
                if client is None:
                    raise
                res, llm, client = TA.run_essay(None, essay_text, st.get("topic", ""), False), False, None
            u = WA.Universe.from_result(res)
            clar = VP.clarifying_items(u, seed=st["seed"])
            para = {}
            if client:
                try:
                    para = VT.paraphrase(client, VP.slot_texts(VP.Plan(clar, [], False, "", 0), [a.text for a in u.worlds["w0"].axioms.values()]))
                except Exception:
                    para = {}
            for it in clar:
                it.prompt = VT.fill(it.template, it.slots, para)
            with self.lock:
                st = self.load(code)
                st.update(tree=res, llm=llm, para=para, clarify=[asdict(i) for i in clar], ci=0, step="main", error="")
                if clar:
                    st["status"] = "clarify"
                else:
                    self._make_extension(st, essay_text)
                self.save(code, st)
        except Exception as e:                              # shown to the organiser, who can retry
            with self.lock:
                try:
                    st = self.load(code)
                except self.error:
                    return
                st.update(status="failed", error=f"{type(e).__name__}: {str(e)[:200]}")
                self.save(code, st)

    # ---- rebuilding the universe from saved state ---------------------------------------------------------------
    def universe(self, st):
        u = WA.Universe.from_result(st["tree"])
        for e in st.get("elicited", []):
            WA.attach_elicited(u.worlds[e["world"]], e["node"], e["text"])
        ghost = None
        ext = st.get("ext") or {}
        if ext.get("flipped"):
            ghost = WA.flip(u.worlds[ext["base"]], ext["flipped"])
            u.worlds[ghost.id] = ghost
            if ext.get("moves"):
                VP.graft_moves(ghost, ext["moves"])
            elif ext.get("tree"):                           # a trial saved before moves existed
                WA.graft_result(ghost, ext["tree"])
        return u, ghost

    def _plan(self, st):
        d = st["plan"]
        return VP.Plan([VP.Item(**i) for i in d["items"]], [tuple(x) for x in d["skipped"]], d["insufficient"], d["reason"], d["seed"], frozenset(d.get("avoid", [])))

    def _store_plan(self, st, p):
        st["plan"] = {"items": [asdict(i) for i in p.items], "skipped": [list(x) for x in p.skipped], "insufficient": p.insufficient,
                      "reason": p.reason, "seed": p.seed, "avoid": sorted(p.avoid)}

    # ---- the extension step ---------------------------------------------------------------------------------------
    def _make_extension(self, st, essay_text):
        """Add a ghost world: one thing the essay (or the person's own clarifying answer) takes for granted is changed."""
        u, _ = self.universe(st)
        minutes = max(1, round(st["seconds"] / 60))
        ghost, prompt, scn, pressure = None, "", False, ""
        client = self._client(st)
        inp = VP.scenario_inputs(u) if client else None
        if inp:                                                 # with a model: a what-if from outside the essay
            w, claim, axs = inp
            try:
                sc = VT.scenario(client, claim, [a.text for a in axs])
            except Exception:
                sc = None
            if sc:
                ghost, prompt = VP.extension_from_scenario(u, w, axs[sc["touches"]], sc["text"], minutes)
                scn, pressure = True, sc.get("pressure", "")
        if ghost is None:                                       # offline, or no acceptable what-if: change one fact the essay states
            ghost, prompt = VP.extension_prompt(u, seed=st["seed"], minutes=minutes, para=st.get("para") or {})
        if ghost is None:
            st["ext"] = {"none": prompt}
            self._make_plan(st)
            return
        st["ext"] = {"prompt": prompt, "base": ghost.base, "flipped": ghost.flipped, "started_at": None, "text": "", "tree": None, "moves": [],
                     "over_time": False, "seconds_used": 0, "scenario": scn, "pressure": pressure}
        st["status"] = "ext_intro"

    def _make_plan(self, st):
        u, ghost = self.universe(st)
        ext_w = ghost if ghost is not None and ghost.stances else None
        p = VP.plan(u, seed=st["seed"], extension=ext_w) if st.get("full") else VP.plan_short(u, seed=st["seed"], extension=ext_w)
        client = self._client(st) if p.items else None
        if client:
            try:
                VP.apply_paraphrase(p, VT.paraphrase(client, VP.slot_texts(p)))
            except Exception:
                pass
        self._store_plan(st, p)
        st.update(qi=0, step="main", status="done" if (p.insufficient or not p.items) else "viva")

    def start_extension(self, code):
        with self.lock:
            st = self.load(code)
            if st["status"] == "extension":
                return st
            if st["status"] != "ext_intro":
                raise self.error("the extension cannot be started now")
            st["status"] = "extension"
            st["ext"]["started_at"] = time.time()
            self.save(code, st)
            return st

    def submit_extension(self, code, text, essay_text):
        with self.lock:
            st = self.load(code)
            if st["status"] != "extension":
                raise self.error("no extension is waiting for text")
            ext = st["ext"]
            secs = time.time() - (ext["started_at"] or time.time())
            text = " ".join((text or "").split())[:4000]
            words = text.split()
            if len(words) > config.WV_EXT_MAX_WORDS:
                text = " ".join(words[:config.WV_EXT_MAX_WORDS])
            ext.update(text=text, seconds_used=round(secs, 1), over_time=secs > st["seconds"] + EXT_GRACE_S)
            if len(WA.toks(text)) >= 4:
                ext["moves"] = VP.ext_moves(text)
            self._make_plan(st)
            self.save(code, st)
            return st

    # ---- answering ---------------------------------------------------------------------------------------------------
    def answer(self, code, text, essay_text):
        text = " ".join((text or "").split())[:4000]
        with self.lock:
            st = self.load(code)
            if st["status"] == "clarify":
                self._answer_clarify(st, text, essay_text)
            elif st["status"] == "viva":
                self._answer_viva(st, text, essay_text)
            else:
                raise self.error("there is no question waiting for an answer")
            self.save(code, st)
            return st

    def _answer_clarify(self, st, text, essay_text):
        items = [VP.Item(**d) for d in st["clarify"]]
        it = items[st["ci"]]
        rec = st["clar_answers"].setdefault(it.id, {"answer": "", "followup": ""})
        if st["step"] == "main":
            rec["answer"] = text
            fu = VP.followup_for(it, text, essay_text)
            if fu:
                st.update(step="followup", fu_text=fu[1])
                return
            best = text
        else:
            rec["followup"] = text
            best = text if not VP.is_generic(text) else rec["answer"]
            st["step"] = "main"
        if best and not VP.is_generic(best):
            st["elicited"].append({"world": it.world, "node": it.targets[0], "text": best})
        st["ci"] += 1
        if st["ci"] >= len(items):
            self._make_extension(st, essay_text)

    def _answer_viva(self, st, text, essay_text):
        p = self._plan(st)
        it = p.items[st["qi"]]
        rec = st["answers"].setdefault(it.id, {"answer": "", "followup": ""})
        if st["step"] == "main":
            rec["answer"] = text
            if it.type == VP.ELICIT and text and not VP.is_generic(text):
                st["elicited"].append({"world": it.world, "node": it.targets[0], "text": text})
                u, _ = self.universe(st)
                if VP.refresh_flip(p, u):
                    self._store_plan(st, p)
            fu = VP.followup_for(it, text, essay_text)
            if fu:
                st.update(step="followup", fu_text=fu[1])
                return
        else:
            rec["followup"] = text
            st["step"] = "main"
        st["qi"] += 1
        if st["qi"] >= len(p.items):
            st["status"] = "done"

    # ---- what the person sees --------------------------------------------------------------------------------------
    def view(self, code):
        st = self.load(code)
        s = st["status"]
        if s in ("preparing", "failed"):
            return {"stage": "wv_prepare", "failed": s == "failed"}
        if s == "clarify":
            it = st["clarify"][st["ci"]]
            return {"stage": "wv_clarify", "warning": VT.WARNING, "note": VT.EXT_NOTE, "hint": VT.HELP_HINT, "number": st["ci"] + 1,
                    "total": len(st["clarify"]), "question": st["fu_text"] if st["step"] == "followup" else it["prompt"],
                    "followup": st["step"] == "followup"}
        if s == "ext_intro":
            return {"stage": "wv_ext_intro", "warning": VT.WARNING, "note": VT.EXT_NOTE, "hint": VT.HELP_HINT, "prompt": st["ext"]["prompt"], "seconds": st["seconds"]}
        if s == "extension":
            left = st["seconds"] - (time.time() - st["ext"]["started_at"])
            return {"stage": "wv_extension", "warning": VT.WARNING, "hint": VT.HELP_HINT, "prompt": st["ext"]["prompt"],
                    "remaining_s": max(0, round(left)), "seconds": st["seconds"], "max_words": config.WV_EXT_MAX_WORDS}
        if s == "viva":
            p = self._plan(st)
            it = p.items[st["qi"]]
            return {"stage": "wv_viva", "intro": VT.VIVA_START, "hint": VT.HELP_HINT, "number": st["qi"] + 1, "total": len(p.items),
                    "question": st["fu_text"] if st["step"] == "followup" else it.prompt, "followup": st["step"] == "followup"}
        return {"stage": "wv_done", "short": bool(st.get("plan", {}).get("insufficient"))}

    def example(self, code, n):
        """The '?' button: a worked example on a random different topic. Counts presses for the reviewer note (a neutral statistic)."""
        with self.lock:
            st = self.load(code)
            s = st["status"]
            if s == "clarify":
                kind, key = st["clarify"][st["ci"]]["ex_kind"], f"c{st['ci'] + 1}"
            elif s in ("ext_intro", "extension"):
                kind, key = "ext", "extension"
            elif s == "viva":
                it = self._plan(st).items[st["qi"]]
                kind, key = it.ex_kind or it.type, it.id
            else:
                raise self.error("there is no question to show an example for")
            n = max(0, min(int(n), 50))
            st["help"][key] = max(st["help"].get(key, 0), n + 1)
            self.save(code, st)
            ex = VT.example(kind, n, st["seed"])
            return {"topic": ex["topic"], "question": ex["question"], "answer": ex["answer"],
                    "reminder": "Your own answer should be about YOUR essay, in your own words."}

    # ---- the reviewer note ---------------------------------------------------------------------------------------------
    def status(self, code):
        try:
            st = self.load(code)
        except self.error:
            return {"status": "", "error": ""}
        return {"status": st["status"], "error": st.get("error", "")}

    def note(self, code, essay_text):
        st = self.load(code)
        if st["status"] in ("preparing", "failed", "clarify", "ext_intro", "extension"):
            return f"# Viva note\n\nNot finished yet (status: {st['status']}). {st.get('error', '')}\n"
        u, ghost = self.universe(st)
        clar = [(VP.Item(**d), (st["clar_answers"].get(d["id"]) or {}).get("answer", "")) for d in st.get("clarify", [])]
        ext = st.get("ext") or {}
        ext_md = VP.ext_section(u, ghost, clar, ext.get("prompt", ext.get("none", "")), ext.get("text", ""), bool(ext.get("over_time")), ext.get("moves") or None)
        p = self._plan(st)
        head = [f"- structure built {'with a model (zero data retention)' if st.get('llm') else 'offline, without a model'}; "
                f"questions: {'full' if st.get('full') else 'short (3 picked at random across the types)'}"]
        if ext.get("scenario") and ext.get("pressure"):
            head.append(f"- the extension was a what-if from outside the essay. The model said it puts pressure on the essay because: {ext['pressure']} (the model's claim, unchecked)")
        helped = {k: v for k, v in st.get("help", {}).items() if v}
        if helped:
            head.append("- the '?' example button was pressed: " + ", ".join(f"{k} x{v}" for k, v in helped.items()) + " (shown for context only; it says nothing about the person)")
        note = VP.note(p, st["answers"], u, essay_text, ext_md)
        i = note.find("\n## ")
        return note + "\n" + "\n".join(head) + "\n" if i < 0 else note[:i] + "\n" + "\n".join(head) + "\n" + note[i:]