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

## Addendum: Visq 2 replaces Visq 1 (2026-11-04)

The team chose Visq 2's world-tree flow over Visq 1. We now vendor the Visq 2 modules (`worldviva`, `worldalg`,
`vivaplan`, `vivatext`, `treealgo` and their helpers) from the same commit and drop Visq 1's (`aroa`, `questions`,
`followups`). Visq 2 asks clarifying questions, gives a timed typed extension (a what-if from outside the essay) and
then a short viva whose questions depend on the earlier answers.

- **No question is approved in advance in a Visq 2 viva.** This widens the follow-up exception above to the whole
  Visq 2 flow. Instead: every question is frozen as it is released and shown in the report with its answer; the
  student is told on the intro page that an automated system chooses the next question and does not mark answers;
  the reviewer note is a reading aid that compares wording, never a score. The reviewer's own-questions flow stays
  for anyone who wants approved questions.
- **Typed answers only.** Visq 2's extension is typed and its note compares wording; spoken answers would add a
  transcription wait before every step.
- **Visq's state is ours to store.** `WorldViva` keeps a JSON file per person; we subclass it so the state lives in
  memory for one call and is stored encrypted and versioned in `viva_visq2_states`. Steps that may call a model run
  in the worker; if a step is late, the API applies it with Visq's offline heuristics so the student is never stuck.
- Known limits from the model team at freeze: the real-model path was tried by hand on a few essays; the note's
  markers see wording, not meaning, so a correct answer in other words can read "not evident".

## Addendum: Pipeline 2 v1.1 (commit 9cfe33d)

v1 split the first 250 words into sentence-aligned thirds and needed 9 sentences, so long-sentence university
writing was often unscorable (a BAWE history essay had 4 sentences in its first 250 words). The model team measured
v1 rejecting 44% of university essays and 20% of school essays but about 2% of AI essays, and released v1.1: three
equal word thirds, at least 150 words, validated on a fresh pre-registered PERSUADE sample (coverage 100%, AUROC
0.786 vs 0.780).

- **Re-vendored `scoring.py` only**; `comparisons.py` and `__init__.py` are unchanged, so stored comparison sets
  still load. Earlier runs keep their `p2-interchangeability-v1+e99417a` version; new runs are `v1.1+9cfe33d`.
- **Threshold 0 is validated for school essays only** (9.7% of human essays flagged). For university essays it is
  experimental (6 of 24 human BAWE essays fell below 0 in a small GPT-only check). Every AI comparison result carries
  a `threshold_validation` evidence item saying so, and the combined report adds a coverage note whenever P2 ran.
- An essay under 150 words is *not eligible*, with the reason in plain words.

## Addendum: preview Visq 2's questions before sending

A teammate asked to see the AI's questions before an invitation goes out. Visq 2 fixes only its clarifying
questions when it reads the essay; the what-if is built from the student's clarifying answers, and the viva plan from
the what-if answer. We chose a preview that keeps the flow adaptive rather than a fixed, approved set (which would
change how Visq works).

- **Prepare questions** runs Visq's prepare step for the case (`visq2_preview` job, `viva_visq2_previews`, state and
  draft encrypted). On a copy of the state we call Visq's own `_make_extension` and `_make_plan` as if no clarifying
  answers were given, to show a draft what-if and draft viva questions. The vendored code is not edited.
- **The clarifying questions are exact**: sending hands the prepared state to the invitation unchanged (no
  `visq2_prepare` job), and a new link for an unstarted viva carries the same state. The what-if and viva questions
  are labelled *Draft* and may differ in the viva.
- The preview is bound to the set size and what-if time; changing either means preparing again. A preview is used
  once. The report says whether the questions were previewed.
- The API still accepts a Visq 2 invitation without a preview (Visq then reads the essay in the worker as before);
  the web form always prepares first.
