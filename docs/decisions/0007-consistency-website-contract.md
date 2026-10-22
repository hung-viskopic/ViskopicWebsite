# 0007 — Score Δ through the model team's website contract, with server-side quality reviews

**Context.** The model team added a website adapter to `model/v1-3-mvp-handoff` (PR #4, commit `d102392`).
The V1.3 model itself is unchanged byte-for-byte. The adapter fixes the integration contract
(`viskopic.website-consistency.v1`): explicit states without a verdict (insufficient writing, quality review
needed, unsupported text, cannot score), every scored result labelled experimental, raw distance and
distance ÷ typical distance reported separately as descriptive diagnostics, and a quality gate: it only scores
when trusted server-side records say the submission and every reference text are English prose that was
extracted cleanly. It ships a record format, not a language detector.

**Decision.**
- Vendor `website_adapter.py` unchanged next to the model and call `score_for_website` from our Δ adapter
  instead of `scorer.score_text`. The model does not hash-check this file, so we pin its SHA-256 and refuse to
  start if it differs (the repo's `.gitattributes` keeps Git from converting its line endings).
- Quality reviews are a human step for now: reviewers record language and extraction per document
  (`document_quality_reviews`, append-only). The server computes the SHA-256 of the stored text when the
  review is recorded; nothing comes from the browser except the reviewer's two answers.
- The submission's latest review goes to the adapter with the digest it was bound to; the adapter itself
  detects a changed text.
- The baseline's record is combined from the latest review of each reference document: worst outcome wins,
  missing means unreviewed, and a review of an older text makes it stale (blocked, as the adapter blocks stale
  records even in demo mode). Because each review is bound to its document's exact text and the profile is
  built from exactly those texts, the combined outcome is bound to the model's own profile digest at scoring.
- `VISKOPIC_ALLOW_UNREVIEWED_CONSISTENCY` maps to the adapter's `allow_unreviewed_demo` server option. Off by
  default; on only in local `.env`. Demonstration profiles (research papers we can't review) only score with it.
- States without a verdict are stored as `not_eligible` runs with `details.website_status`, never as a score.
  A case waiting only for reviews is `awaiting_review`.

**Why.** It keeps the model team's guarantees intact (frozen threshold, no manufactured zeros, honest labels)
while our tenant isolation, encryption and audit stay in one process (see 0006). Binding reviews to text
digests means a review can't silently cover a different text.

**Consequences.**
- Reviewers have one more step per document before a real result appears. An automated language/extraction
  check could pre-fill reviews later, but must record its outcome honestly (including "uncertain").
- The comparison bank is still "not fully language audited" (the adapter reports this); that is model-team work.
- The adapter's output includes sampled essay text in `original_model_result`; we use it only to locate spans
  and never store it (tested).
