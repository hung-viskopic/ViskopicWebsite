"""An offline stand-in for the model, so the whole flow can be tried (and tested) without a key or any spend.
It is NOT a language model: questions are templates filled from the essay's own sentences."""
import json
import re

from .llm import Budget, LLMResult

_SENT = re.compile(r"(?<=[.!?])\s+")


class FakeClient:
    def __init__(self, cost=0.0):
        self.budget = Budget(limit=1e9)
        self.cost = cost
        self.calls = 0
        self.inputs = []

    def chat(self, system, user, *, temperature=0, max_tokens=0, model=None):
        self.calls += 1
        self.inputs.append((system, user))
        self.budget.add(self.cost)
        if "proofread viva questions" in system or "proofread extension prompts" in system:
            return self._ok({"ok": True, "problem": ""})
        if "expert in argument structure" in system:
            return self._extract(user)
        if "short neutral overview of an essay" in system:
            body = re.sub(r"</?essay>", "", user).strip()
            return self._ok({"summary": " ".join(body.split()[:120])})
        if "role-playing a student" in system:
            return self._ok({"answer": "It depends on the stakes and the cost, and I would weigh both before deciding."})
        if "did not understand a viva question" in system:
            q = re.search(r"<question>\n(.*?)\n</question>", user, re.S).group(1)
            return self._ok({"question": "Put another way, " + q[0].lower() + q[1:]})
        if "second half of a viva" in system:
            return self._followup(user)
        if "List the COMMITMENTS the person" in system:
            return self._commitments(user)
        if "where one person's own statements about their essay do not fit" in system:
            return self._ok({"findings": []})
        if "how much of an essay's own thinking a person reproduced" in system:
            ans = user.split("ANSWER:", 1)[-1] if "ANSWER:" in user else user
            return self._ok({"essay_reasons": ["the first reason given", "the second reason given"],
                             "covered": ["the first reason given"] if len(ans.split()) > 5 else [],
                             "added": ["a point the essay does not make"], "note": "(fake) covers part of the essay's reasoning"})
        if "how one person's thinking moved from their ESSAY" in system:
            shifted = "instead" in user.split("ANSWER:", 1)[-1]
            return self._ok({"essay_position": "The essay says cars should be banned.", "extension_position": "The answer would allow cars with conditions.",
                             "movement": "shifts" if shifted else "holds", "change": "From a ban to a conditional allowance.",
                             "probe": "Earlier you imagined a changed situation for this point. Of the reasons you gave for your view on it, which still applied there, and which did not?" if shifted else ""})
        if "compare two things one person wrote" in system:
            return self._consistency(user)
        if "write EXTENSION prompts" in system:
            return self._extension(user)
        return self._question(user)

    @staticmethod
    def _ok(d):
        return LLMResult(json.dumps(d), "stop", 0.0, "fake", "fake")

    def _extract(self, user):
        body = re.sub(r"</?essay>", "", user).strip()
        s = [x.strip() for x in _SENT.split(body) if len(x.split()) >= 5] or ["The essay makes a point about its topic."]
        pick = lambda i: s[min(i, len(s) - 1)]                              # noqa: E731
        d = {"claims": [{"id": "c1", "text": pick(0)}, {"id": "c2", "text": pick(len(s) - 1)}],
             "reasons": [{"id": f"r{i}", "text": pick(i)} for i in (1, 2, 3)],
             "evidences": [{"id": "e1", "text": pick(4)}, {"id": "e2", "text": pick(5)}],
             "counterarguments": [{"id": "a1", "text": pick(6)}], "rebuttals": [{"id": "b1", "text": pick(7)}],
             "relationships": [{"type": "supports", "from": "r1", "to": "c1"},
                               {"type": "supports", "from": "r2", "to": "c1"},
                               {"type": "supports", "from": "r3", "to": "c2"},
                               {"type": "supports", "from": "e1", "to": "r1"},
                               {"type": "supports", "from": "e2", "to": "r2"},
                               {"type": "responds_to", "from": "b1", "to": "a1"}]}
        return self._ok(d)

    def _question(self, user):
        qtype = re.search(r"TYPE: (\w+)", user).group(1)
        tgt = re.search(r"TARGET_TEXT: (.*)", user).group(1)
        topic = " ".join(re.sub(r"\s*\(.*", "", tgt).split()[:4])
        q = {"tradeoff": f"Under what conditions would your position on {topic} beat the objection, and when would the objection win?",
             "limit": f"If {topic} turned out not to be true, how would your conclusion change?",
             "link": f"In general, what would make a rule on {topic.split()[0].lower()} fair to everyone it touches?",
             "case": f"If the number of people affected by {topic} were much larger, which of your own points would you rely on?"}[qtype]
        return self._ok({"question": q, "good_answer_covers": f"(fake) A specific account of the writer's thinking on {topic}."})

    def _followup(self, user):
        ans = re.search(r"<answer>\n(.*?)\n</answer>", user, re.S).group(1)
        if len(ans.split()) >= 25:
            return self._ok({"action": "move_on", "question": "", "why": "(fake) answer already detailed"})
        asked = user.count("Q: ")
        opts = ["What would change your mind about that point?",
                "Why that rather than the other option?",
                "Where does that point stop being true?"]
        return self._ok({"action": "follow_up", "question": opts[asked % len(opts)], "why": "(fake) answer was short"})

    def _extension(self, user):
        tgt = re.search(r"TARGET_TEXT: (.*)", user).group(1)
        topic = " ".join(re.sub(r"\s*\(.*", "", tgt).split()[:4])
        k = user.count("\n- ")
        opts = [f"Suppose the main reason behind {topic} did not apply in one important case. What would you do there, and what would you have to accept?",
                f"Imagine the people affected by {topic} were ten times fewer than you assumed. What would you change about your reasons, and why?",
                f"What if the objection to {topic} were true in its strongest form? What would you change about your position?",
                f"Consider a rule on {topic} that applies only at night. Where would you draw the line, and why?",
                f"Suppose {topic} worked only for half of those it was meant for. What would you do instead, and why?"]
        return self._ok({"question": opts[k % len(opts)], "good_answer_covers": f"(fake) A committed position on {topic} with reasons."})

    def _consistency(self, user):
        return self._ok({"extension_position": "(fake) takes a side", "viva_position": "(fake) takes a side",
                         "verdict": "consistent", "note": "(fake) positions agree"})

    def _commitments(self, user):
        out, n = [], 0
        for m in re.finditer(r"^\[([EXV][\d.]*)\] (?:PROMPT: .*? ANSWER: |QUESTION: .*? ANSWER: )?(.*)$", user, re.M):
            quote = " ".join(m.group(2).split()[:8])
            if len(quote.split()) >= 4:
                n += 1
                out.append({"id": f"k{n}", "label": m.group(1), "quote": quote, "position": "(fake) " + quote})
        return self._ok({"commitments": out[:30]})