# Vendored: Visq viva engine (Visq 1 question writing and follow-ups)

Source: `hung-viskopic/VISKOPIC_PROJECT`, branch `feature/authenticity-pipeline`,
commit `3d631d8e605bc73732e5f27dac2eac8a21687f9f` ("visq 2.0 is live"),
path `src/viskopic_algorithms/viva/visq/`.

Only the modules Visq 1 needs are copied, each byte-for-byte:

| File | What we use it for |
|---|---|
| `__init__.py` | `PROMPT_VERSION` (q1.20), recorded with every question set and follow-up decision |
| `config.py` | model, zero-data-retention provider setting, limits |
| `llm.py` | `LLMResult`, `Budget`, `ask_json` (our own OpenRouter client implements the same `chat()` interface) |
| `prompts.py` | the extraction, question, proofreading and follow-up prompts |
| `text.py` | normalising, length check, passage location, copy check |
| `aroa.py` | the essay's argument structure (claims, reasons, evidence, objections) |
| `questions.py` | planning targets and writing and checking the main questions |
| `followups.py` | deciding one follow-up per answer |
| `fake.py` | the offline stand-in model, for tests only |

Not copied: the pilot portal (`webapp.py`, `pilot.py`, `static/`), the terminal session, Visq 2's
world-tree flow, the extension step, the consistency analysis, the reports and the research folder.
Our viva pages, token links, recordings, encryption and audit replace them.

**Do not edit, reformat or re-save anything in this folder.** `viskopic/viva/visq.py` checks the
SHA-256 of every file here (`FILE_SHA256`) before importing and refuses to run if one differs.
Visq's own extraction cache would write essay-derived text to disk; our integration always calls it
with `cache=False`, and never uses Visq's own OpenRouter client (it reads a `.env` file).

To update: copy the same files again from a newer commit, then update the commit above and
`VENDOR_COMMIT` / `FILE_SHA256` in `viskopic/viva/visq.py`. Never mix files from two commits.
