# 0010 A readable staff app: cases first, plain words, evidence beside the essay

Date: 2026-11-11

## Decision

**The home page is a list of cases grouped by what a person does next**, not a dashboard of counts. A new
`GET /cases` (course-scoped through `accessible_course_ids`, RLS as usual) returns each case with its latest
Writing style, AI comparison and combined runs, its latest viva, and a `next_step`: review the evidence, check
texts, rate a viva, a viva problem, waiting (viva, baseline or analysis), failed, or done (no flag, viva rated,
withdrawn). It returns ids, labels and outcomes only, never essay text or template statements.

**Checking texts is its own focused page** (`/check/{case}`). `GET /cases/{id}/texts` lists the submission and the
baseline essays long enough to count, with their current quality review. The page shows each text as stored and
saves the same server-bound `document_quality_reviews` row as before; nothing in the browser can create a pass.

**The student page and the Students list use the model's own rule** (8 past essays of at least 1,650 usable words).
`baseline_summary` now counts only essays that meet the word minimum and reports how many are too short. The
product rule (3 essays, 1,500 words) is unchanged as a product rule; the screen shows what the model will accept.

**Evidence is shown as a plain heading and two numbers** (this essay vs the student's usual value and spread),
with the model's template statement word for word under "Exact finding". The heading is the model's own label
where the statement starts with one, otherwise a fixed heading per evidence kind. Scores, thresholds, model labels,
the combined report text and the descriptive distances move to a folded "Technical details" block; they are still
shown, never as a percentage. Every scored result keeps its "Experimental" label.

**Spoken viva answers and webcam recording are hidden from the form for now.** Every new invitation is typed
(`answer_format: "typed"`, `camera: false`). The spoken path stays in the backend and on the student page, so old
vivas and reports still work.

**No browser dialogs.** Withdrawing a viva and inviting another owner use inline forms with a required reason
or email instead of `prompt()`/`alert()`.

## Not changed

Rating values (`consistent`, `unclear`, `concern`) and their labels, case statuses, evidence statements and every
API used by the old screens. Closing a case with no action and a setup checklist were designed but not built.
