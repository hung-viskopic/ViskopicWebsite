# Vendored: Pipeline 2, essay interchangeability (frozen release v1.1)

Source: `hung-viskopic/VISKOPIC_PROJECT`, branch `model/v1-3-mvp-handoff`,
commit `9cfe33d3f9ef9c3c65ef938be49af7a1fff35d4d`,
path `services/interchangeability_p2/viskopic_p2/` (all three files, byte-for-byte).

v1.1 changed `scoring.py` only: the first 250 words are split into three equal word thirds (at least 150
words) instead of v1's sentence-aligned thirds, which needed 9 sentences and rejected 44% of university
essays. `comparisons.py` and `__init__.py` are identical to v1 (commit `e99417a`; `__init__`'s docstring still
says v1), so comparison sets stored under v1 still load. Threshold 0 is validated for school essays only.

Guide: `docs/COMBINED_PIPELINE.md` in the source repo (what it measures, thresholds, evidence, limits).

Not copied: the combined entry point `services/viskopic_combined`. Its `CombinedScorer` calls the V1.3
scorer directly, which would skip the website adapter and its quality-review gate, and its local HTTP
server lists every loaded profile. Its "either flags" rule and wording are re-implemented over our
finished runs in `viskopic/analysis/combined.py`. The V1.3 model on that branch is byte-identical to
our `vendor/consistency_v13` (commit `d102392`) apart from the website adapter, which that branch lacks.

**Do not edit, reformat or re-save anything in this folder.** `viskopic/analysis/interchangeability.py`
checks every file's SHA-256 (`FILE_SHA256`) before importing.

To update: copy the package again from a newer commit and update the commit above and
`VENDOR_COMMIT` / `FILE_SHA256` in `viskopic/analysis/interchangeability.py`.
