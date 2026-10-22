# 0006 — Run the V1.3 consistency model in-process, vendored unchanged

**Context.** The model team delivered the Δ (author-style consistency) model as a frozen, hash-checked Python
package (`VISKOPIC_PROJECT`, `model/v1-3-mvp-handoff`), with enrolment, persisted profiles and opt-in
diagnostics. Their local HTTP demo has no authentication and is not meant for production.

**Decision.** Copy the package byte-for-byte into `app/backend/vendor/consistency_v13/` and call it from our
worker through an adapter (`viskopic/analysis/consistency.py`). No separate model service.

**Why.**
- Same language and Python version as our worker; the model's tests pass on 3.13.
- Tenant isolation, encryption and the audit log stay in one place. A second service would need its own
  authentication, and essay text would cross a network boundary.
- Profiles are built by a `build_profile` job (it scores the whole 635-paper background bank, so it gets a
  20-minute lease), stored encrypted with the institution's key, and loaded after their hash is checked.

**Consequences.**
- The model's in-process profile registry is shared by all tenants in a worker. Profile IDs are opaque
  (`vp-<uuid>`), and anything that lists profiles must filter to the built-in research ones (tested).
- Loaded profiles stay in worker memory until restart. Fine for the MVP; add eviction before large pilots.
- Updating the model means re-copying the whole package and bumping the recorded commit. The hash check fails
  loudly if anything was edited.
- The model's input minimums (8 documents × 1,650 usable words; 1,650-word submissions) are stricter than the
  product rule. Most real students won't qualify until the model team validates a student configuration.
