# 0009 Adopt the model team's Visq viva engine and Pipeline 2

Date: 2026-11-04

## Decision

**Viva questions come from Visq.** The model team's viva engine (`VISKOPIC_PROJECT`, `feature/authenticity-pipeline`,
commit `3d631d8`) replaces our own OpenAI question drafting. We vendor only its Visq 1 core (argument extraction,
question planning, writing and checks, follow-up decisions) byte-for-byte and hash-check it. Our viva stays the
delivery and supervision layer: token links, server-released questions and deadlines, one session at a time, spoken
and webcam recording, transcription, encryption, signals, audit and ratings. We did not adopt Visq's pilot portal
(localhost server, JSON files, in-memory sessions) or the Visq 2 world-tree flow, which was hand-tested on only a
few essays.

**Follow-ups are asked without prior approval.** Decision 0008 said a reviewer approves every question. Follow-ups
can't be approved in advance because they depend on the answer, so this is the one exception. In its place: at most
one per main question; Visq's code checks and proofreading call; the same time limits; a "follow-up" label on the
student page and in the report; the decision and Visq's reason stored for the reviewer (encrypted, append-only);
the student told on the intro page that a follow-up may come and that answers are read only to choose one. If the
decision isn't ready within `viva_followup_wait_seconds`, or anything fails, the viva moves on: a student is never
left waiting on a model.

**Pipeline 2 fills the β slot.** The interchangeability scorer (`feat/combined-p1-p2`, commit `e99417a`) is vendored
unchanged. Its comparison essays are generated once per assignment from the assignment prompt only, so no student
writing leaves the backend for it, and it needs no baseline, which covers the many students below V1.3's 8-essay
minimum. GPT family only for now (no GPU host for Qwen/Gemma); the report says that setup is weaker.

**The combined report is ours, the rule is theirs.** Their `CombinedScorer` calls V1.3 directly and would skip the
website adapter's quality-review gate, and their local HTTP server lists every loaded profile across tenants. We
apply their "either flags" rule and wording to finished runs instead, and store the result as a `combined` run.

## Consequences

- Student essays and answers go to OpenRouter (zero data retention) when external AI is on. Same rule as before:
  synthetic test essays only until a DPIA and DPA cover it.
- A single worker can delay follow-ups behind a long job; viva jobs are claimed first, and a second worker helps.
- Pipeline 2's threshold of 0 is provisional (about 1 in 10 human essays fall below it in the model team's tests);
  results are labelled experimental and only route a case to a person.

## Addendum: Visq on OpenAI

`VISKOPIC_VISQ_PROVIDER=openai` runs Visq's unchanged prompts on OpenAI's Responses API with the existing OpenAI
key (default model `gpt-5.4-mini-2026-03-17`, responses not stored, reasoning off, no temperature). It exists so the
team can test with one provider. Two differences from the default, both shown in the recorded engine label: Visq
was tuned on `openai/gpt-oss-120b`, so questions may read differently; and OpenAI may keep request logs for abuse
monitoring unless the organisation has zero data retention, whereas the OpenRouter path refuses any provider that
keeps data. Synthetic essays only, as before.
