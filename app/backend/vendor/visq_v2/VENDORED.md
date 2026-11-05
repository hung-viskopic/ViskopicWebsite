# Vendored: Visq viva engine (Visq 2, the world-tree flow)

Source: `hung-viskopic/VISKOPIC_PROJECT`, branch `feature/authenticity-pipeline`,
commit `3d631d8e605bc73732e5f27dac2eac8a21687f9f` ("visq 2.0 is live"),
path `src/viskopic_algorithms/viva/visq/`.

Only the modules the Visq 2 world-tree flow needs are copied, each byte-for-byte:

| File | What it does |
|---|---|
| `__init__.py` | `VERSION` (2), recorded with every viva |
| `config.py` | model, zero-data-retention provider setting, limits (extension word cap) |
| `llm.py` | `LLMResult`, `Budget`, `ask_json` (our own clients implement the same `chat()` interface) |
| `prompts.py`, `text.py`, `analysis.py` | prompts and essay-text helpers the tree builder uses |
| `treealgo.py` | reads the essay as a tree of points (heuristics, plus the model when available) |
| `worldalg.py` | worlds, assumptions, what falls if an assumption changes |
| `vivaplan.py` | clarifying questions, the extension, question types and sampling, the reviewer note |
| `vivatext.py` | plain wording, paraphrase, what-ifs, the worked examples |
| `worldviva.py` | the flow itself (`WorldViva`): clarify → extension → viva → done |

Not copied: Visq 1 (`aroa.py`, `questions.py`, `followups.py`, `engine.py`), the pilot portal (`webapp.py`,
`pilot.py`, `static/`), the terminal session, reports and the research folder. Our viva pages, token links,
server timing, encryption and audit replace them.

**Do not edit, reformat or re-save anything in this folder.** `viskopic/viva/visq.py` checks the SHA-256 of every
file here (`FILE_SHA256`) before importing, and refuses to run if a file differs or one is added or missing.
`WorldViva` keeps its state in a JSON file per person; our integration subclasses it so the state stays in memory
for one call and is stored encrypted in `viva_visq2_states`. Visq's own OpenRouter client (it reads a `.env` file)
is never used.

To update: copy the same files again from a newer commit, then update the commit above and `VENDOR_COMMIT` /
`FILE_SHA256` in `viskopic/viva/visq.py`. Never mix files from two commits.
