# Vendored: V1.3 author-style consistency model (Δ)

Source: `hung-viskopic/VISKOPIC_PROJECT`, branch `model/v1-3-mvp-handoff`,
commit `d10239206d8d283083c564ce04a98d8898c33f87` (merge of PR #4, the V1.3 website adapter).

| Here | Source path |
|---|---|
| `viskopic_v13/` | `services/consistency_v13/viskopic_v13/` (unchanged since `0a78771`) |
| `website_adapter.py` | `services/consistency_v13/website_adapter.py` (new in PR #4) |

**Do not edit, reformat or re-save anything in this folder.** The model checks the
SHA-256 of its frozen sources and assets at startup and refuses to run if a single
byte changes (line endings included; `.gitattributes` here keeps Git from converting
them). The model does not check `website_adapter.py` itself, so our adapter records
its SHA-256 (`ADAPTER_SHA256` in `viskopic/analysis/consistency.py`) and refuses to
start if it differs; the repo-root `.gitattributes` marks it `-text` for the same reason.

To update, copy the whole `viskopic_v13` package and `website_adapter.py` again from a
newer commit, change the commit above and `VENDOR_COMMIT`/`ADAPTER_SHA256` in
`viskopic/analysis/consistency.py`.

What it does, its limits and its input minimums are documented in the source repo:
`services/consistency_v13/README.md`, `docs/V1_3_WEBSITE_ADAPTER.md`,
`docs/V1_3_PROFILE_ENROLLMENT.md` and `docs/MVP_FEEDBACK_RESPONSE.md`.
