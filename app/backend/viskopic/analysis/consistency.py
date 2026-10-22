"""Δ — author-style consistency, using the frozen V1.3 model from VISKOPIC_PROJECT.

The model and its website adapter are vendored unchanged in `vendor/consistency_v13/` (hash-checked at
startup). This module is our side of the integration:

- loads the model once per process,
- enrols a student's baseline documents into a numeric profile artifact,
- scores a submission through the model team's website contract (`website_adapter.score_for_website`),
  which owns the eligibility rules, the quality-review gate and the derived distance diagnostics,
- turns the model's opt-in diagnostics into evidence items with spans in *our* extracted text.

What the output means, and does not mean, is fixed by the model's authors: every scored result is
experimental; the centred score is not a probability or a percentage and can be negative; the label is
"style compatible" or "stylistically unusual relative to the reference profile". Raw and relative distance
are descriptive diagnostics, not a second decision. States without a verdict (insufficient writing, quality
review needed, unsupported text, cannot score) never get a manufactured score. Statements below stick to that.
"""
from __future__ import annotations

import hashlib
import re
import statistics
import sys
import tempfile
import threading
from pathlib import Path

from ..config import get_settings
from .base import AnalysisContext, EvidenceItem, PipelineInputError, PipelineResult, ProfileRef, Span

VENDOR_DIR = Path(__file__).resolve().parents[2] / "vendor" / "consistency_v13"
VENDOR_COMMIT = "d102392"           # keep in step with vendor/consistency_v13/VENDORED.md
MODEL_VERSION = "1.3"
PIPELINE_VERSION = f"consistency-v{MODEL_VERSION}+{VENDOR_COMMIT}"
# The model hash-checks its own package but not the website adapter beside it, so we do.
ADAPTER_SHA256 = "96342d69bba75f2ef4d417d30aa6cee216e18f694df391908527085535612598"
WEBSITE_SCHEMA = "viskopic.website-consistency.v1"

# Frozen V1.3 input requirements (the model's adapter enforces these; they are repeated here to explain them).
MIN_REFERENCE_DOCUMENTS = 8
MIN_WORDS = 1650                    # usable words, per reference document and per submission

_lock = threading.Lock()
_scorer = None
_adapter = None
_builtin_ids: frozenset[str] = frozenset()


def get_scorer():
    """The V13Scorer for this process. Verifies every model asset hash (and the adapter's) on first use."""
    global _scorer, _adapter, _builtin_ids
    with _lock:
        if _scorer is None:
            adapter_path = VENDOR_DIR / "website_adapter.py"
            digest = hashlib.sha256(adapter_path.read_bytes()).hexdigest()
            if digest != ADAPTER_SHA256:
                raise RuntimeError(f"vendored website_adapter.py has SHA-256 {digest}, expected {ADAPTER_SHA256}; "
                                   "re-copy it unchanged from the model repository")
            if str(VENDOR_DIR) not in sys.path:
                sys.path.insert(0, str(VENDOR_DIR))
            from viskopic_v13 import V13Scorer
            import website_adapter

            if (website_adapter.SCHEMA, website_adapter.MIN_REFERENCES, website_adapter.MIN_USABLE_WORDS) != (
                    WEBSITE_SCHEMA, MIN_REFERENCE_DOCUMENTS, MIN_WORDS):
                raise RuntimeError("the vendored website adapter's contract differs from this integration")
            _scorer = V13Scorer()
            _adapter = website_adapter
            _builtin_ids = frozenset(p["profile_id"] for p in _scorer.list_profiles())
        return _scorer


def get_adapter():
    get_scorer()
    return _adapter


def model_profile_id(profile_id) -> str:
    """Opaque ID used inside the model's memory. Never contains a student identifier."""
    return f"vp-{profile_id}"


def text_sha256(text: str) -> str:
    """The digest a quality review is bound to. Same as the model adapter's `candidate_digest`."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def demo_profiles() -> list[dict]:
    """Research-author profiles shipped with the model, for demonstrations only.

    The model keeps every profile it has loaded in one in-process registry, including other
    institutions' enrolled students, so this must only ever return the built-in ones.
    """
    scorer = get_scorer()
    return [p for p in scorer.list_profiles() if p["profile_id"] in _builtin_ids]


# ------------------------------------------------------------------ enrolment


def enrol(profile_id, references: list[tuple[str, str]]) -> tuple[bytes, str, dict]:
    """Build a profile from (document_id, text) pairs.

    Returns (artifact_bytes, sha256, summary). The caller encrypts and stores the bytes and
    keeps the hash in the database; the model refuses to load an artifact whose hash differs.
    Raises PipelineInputError when the documents don't meet the model's requirements.
    """
    if len(references) < MIN_REFERENCE_DOCUMENTS:
        raise PipelineInputError(
            f"the model needs at least {MIN_REFERENCE_DOCUMENTS} baseline documents of {MIN_WORDS}+ words each")
    scorer = get_scorer()
    from viskopic_v13 import write_profile_artifact

    try:
        artifact = scorer.enroll_profile(
            profile_id=model_profile_id(profile_id),
            reference_documents=[{"paper_id": doc_id, "text": text} for doc_id, text in references],
        )
    except ValueError as e:  # the model's own input checks; messages contain no essay text
        raise PipelineInputError(str(e)) from e
    with tempfile.TemporaryDirectory(prefix="viskopic-profile-") as tmp:
        path = Path(tmp) / "profile.json"
        sha = write_profile_artifact(path, artifact)
        data = path.read_bytes()
    summary = {
        "model_version": MODEL_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "reference_documents": artifact["profile_papers"],
        "maturity": _maturity(artifact["profile_papers"]),
        "background_median": artifact.get("background_median"),
        "background_documents": artifact.get("background_documents"),
        "background_authors": artifact.get("background_authors"),
    }
    return data, sha, summary


def _ensure_loaded(profile: ProfileRef) -> str:
    """Load an enrolled profile into this process's model, verifying its hash."""
    scorer = get_scorer()
    if profile.source == "demonstration":
        return profile.demo_author_id
    mid = model_profile_id(profile.id)
    with scorer._profile_lock:
        loaded = mid in scorer._profiles
    if not loaded:
        if profile.load_artifact is None or not profile.artifact_sha256:
            raise RuntimeError("profile artifact is not available")
        with tempfile.TemporaryDirectory(prefix="viskopic-profile-") as tmp:
            path = Path(tmp) / "profile.json"
            path.write_bytes(profile.load_artifact())
            scorer.load_profile_artifact(path, expected_sha256=profile.artifact_sha256)
    return mid


def _maturity(n: int) -> str:
    return "mature" if n >= 10 else "usable" if n >= 8 else "insufficient"


def confidence_for(profile: ProfileRef) -> tuple[str, str]:
    """How sure: from the size of the baseline and how much of it was supervised."""
    if profile.source == "demonstration":
        return "low", "demonstration profile: a research author's papers, not this student's writing"
    n, sup = profile.reference_count, profile.supervised_count
    if n >= 10 and sup >= 2:
        return "high", f"{n} baseline documents, {sup} written under supervision"
    if n >= 10:
        return "medium", f"{n} baseline documents, {sup} written under supervision"
    return "low", f"{n} baseline documents (the model's minimum is {MIN_REFERENCE_DOCUMENTS}; 10+ is 'mature')"


# ------------------------------------------------------------------ evidence wording

GROUP_LABEL = {
    "sentence_structure": "Sentence structure",
    "vocabulary": "Vocabulary",
    "readability": "Readability",
    "punctuation": "Punctuation",
    "pronouns_and_stance": "Pronouns and stance",
    "function_words": "Function words",
    "transitions": "Transitions",
    "sentence_openings": "Sentence openings",
    "other": "Other features",
}

FEATURE_LABEL = {
    "avg_sentence_length": ("Average sentence length", "words"),
    "sentence_length_std": ("Variation in sentence length", "words"),
    "long_sentences_ratio": ("Share of long sentences", "ratio"),
    "short_sentences_ratio": ("Share of short sentences", "ratio"),
    "unique_sentence_opening_ratio": ("Variety of sentence openings", "ratio"),
    "repeated_sentence_opening_ratio": ("Repeated sentence openings", "ratio"),
    "avg_word_length": ("Average word length", "letters"),
    "word_length_std": ("Variation in word length", "letters"),
    "type_token_ratio": ("Vocabulary variety (type-token ratio)", "ratio"),
    "root_type_token_ratio": ("Vocabulary variety (root TTR)", ""),
    "corrected_type_token_ratio": ("Vocabulary variety (corrected TTR)", ""),
    "hapax_ratio": ("Words used only once", "ratio"),
    "hapax_unique_ratio": ("Words used only once, of distinct words", "ratio"),
    "dislegomena_ratio": ("Words used exactly twice", "ratio"),
    "repeated_word_ratio": ("Repeated words", "ratio"),
    "long_words_ratio": ("Share of long words", "ratio"),
    "very_long_words_ratio": ("Share of very long words", "ratio"),
    "avg_syllables_per_word": ("Syllables per word", "syllables"),
    "complex_word_ratio": ("Share of complex words", "ratio"),
    "flesch_reading_ease": ("Flesch reading ease", "points"),
    "flesch_kincaid_grade": ("Flesch–Kincaid grade", "grade"),
    "gunning_fog_index": ("Gunning fog index", "points"),
    "commas_per_100_words": ("Commas", "per 100 words"),
    "semicolons_per_100_words": ("Semicolons", "per 100 words"),
    "colons_per_100_words": ("Colons", "per 100 words"),
    "question_marks_per_100_words": ("Question marks", "per 100 words"),
    "exclamation_marks_per_100_words": ("Exclamation marks", "per 100 words"),
    "parentheses_per_100_words": ("Parentheses", "per 100 words"),
    "hyphens_per_100_words": ("Hyphens", "per 100 words"),
    "first_person_per_100_words": ("First-person pronouns", "per 100 words"),
    "second_person_per_100_words": ("Second-person pronouns", "per 100 words"),
    "third_person_per_100_words": ("Third-person pronouns", "per 100 words"),
    "contractions_per_100_words": ("Contractions", "per 100 words"),
    "transition_total_per_100_words": ("Transition words, all kinds", "per 100 words"),
    "transition_total_per_sentence": ("Transition words, all kinds", "per sentence"),
}


def feature_label(name: str) -> tuple[str, str]:
    if name in FEATURE_LABEL:
        return FEATURE_LABEL[name]
    m = re.fullmatch(r"function_(.+)_per_100_words", name)
    if m:
        return f'Use of "{m.group(1)}"', "per 100 words"
    m = re.fullmatch(r"transition_(.+)_per_100_words", name)
    if m:
        return f'Use of "{m.group(1).replace("_", " ")}"', "per 100 words"
    m = re.fullmatch(r"sentence_opening_(.+)_ratio", name)
    if m:
        return f'Sentences starting with "{m.group(1).capitalize()}"', "ratio"
    return name.replace("_", " ").capitalize(), ""


def _num(x: float, unit: str) -> str:
    if unit == "ratio":
        return f"{x * 100:.1f}%"
    return f"{x:.2f}" if abs(x) < 10 else f"{x:.1f}"


def _unit(unit: str) -> str:
    return "" if unit in ("ratio", "") else f" {unit}"


# ------------------------------------------------------------------ spans


def locate(text: str, sampled: str, anchor_words: int = 12) -> tuple[int, int, str] | None:
    """Find a sampled passage in the extracted text, tolerating whitespace differences.

    The model samples from its own normalised copy of the text, so offsets have to be found
    again in ours. Exact (whole passage, unique) first; otherwise the unique first and last
    words of the passage, which survives the model having dropped very short sentences.
    """
    tokens = sampled.split()
    if not tokens:
        return None

    def pattern(words):
        return re.compile(r"\s+".join(re.escape(w) for w in words))

    hits = [m.span() for m in pattern(tokens).finditer(text)]
    if len(hits) == 1:
        return hits[0][0], hits[0][1], "exact"
    if len(tokens) < anchor_words * 2:
        return None
    head = [m.span() for m in pattern(tokens[:anchor_words]).finditer(text)]
    tail = [m.span() for m in pattern(tokens[-anchor_words:]).finditer(text)]
    if len(head) == 1 and len(tail) == 1 and head[0][0] < tail[0][1]:
        return head[0][0], tail[0][1], "approximate"
    return None


def fmt_measure(x: float, places: int = 1) -> str:
    """A number for a statement. Tiny values read 'near zero', never a misleading '0.0' or '-0.0'."""
    if abs(x) < 0.5 * 10 ** -places:
        return "near zero"
    return f"{x:.{places}f}"


# ------------------------------------------------------------------ quality reviews → the adapter's records


def _quality_reviews(adapter, scorer, model_id: str, ctx: AnalysisContext, profile: ProfileRef):
    """Build the adapter's trusted QualityReview records from our stored reviews.

    Submission: the latest review, bound to the SHA-256 of the text that was reviewed; the adapter itself
    compares that with the text it is given, so an out-of-date review is caught there.
    Baseline: only for enrolled profiles, and only when every reference document has a current review. Those
    reviews are each bound to their document's exact text, and the profile is built from exactly those texts,
    so the combined outcome is bound to the model's own profile digest here. Demonstration profiles are
    research papers we have not reviewed, so they never get a reference record.
    """
    cand = ref = None
    c = ctx.candidate_quality
    if c is not None and c.text_sha256:
        cand = adapter.QualityReview(subject_sha256=c.text_sha256, review_id=c.review_id,
                                     language=c.language, extraction=c.extraction)
    r = ctx.reference_quality
    if r is not None and profile.source == "enrolled" and not r.stale:
        ref = adapter.QualityReview(subject_sha256=adapter.profile_digest(scorer, model_id), review_id=r.review_id,
                                    language=r.language, extraction=r.extraction)
    return cand, ref


# ------------------------------------------------------------------ the pipeline

NOT_SCORED_REASON = {
    "quality_review_required": (
        "Not scored: the submission and every baseline document need a quality review (is the extracted text "
        "English prose, and was it extracted cleanly?) before the model compares them."),
    "unsupported_text": (
        "Not scored: a quality review found non-English or mixed-language text, or a failed extraction. The "
        "current model only supports English prose."),
    "cannot_score": (
        "Not scored: the model could not build a valid comparison from this text and baseline. Check the "
        "extracted text (for example tables, lists or very short sentences)."),
}


class ConsistencyPipeline:
    name = "consistency"
    version = PIPELINE_VERSION
    category = "style"

    # Evidence selection: how unusual a feature must be, and how many to show.
    min_abs_z = 2.0
    max_feature_items = 8

    def run(self, ctx: AnalysisContext) -> PipelineResult:
        profile = ctx.profiles.get(self.name)
        if profile is None:
            raise PipelineInputError(
                f"no baseline profile yet: the student needs at least {MIN_REFERENCE_DOCUMENTS} completed "
                f"baseline documents of {MIN_WORDS}+ words each")
        if profile.source == "demonstration" and not get_settings().allow_unreviewed_consistency:
            # A demonstration profile is a research author's papers: nobody can quality-review them, so the
            # adapter would answer "review required" forever. Say so plainly instead of asking for reviews
            # that can't help.
            return PipelineResult(
                status="not_eligible", profile_id=profile.id,
                reason=("Not scored: demonstration profiles can't be quality-reviewed, so they only produce a "
                        "result when unreviewed scoring is switched on for local development "
                        "(VISKOPIC_ALLOW_UNREVIEWED_CONSISTENCY=true). Reviewing this submission won't change this."),
                details={"schema": WEBSITE_SCHEMA, "model_version": MODEL_VERSION,
                         "deployment_status": "experimental", "profile_source": profile.source,
                         "website_status": "demo_requires_unreviewed_mode"})
        scorer, adapter = get_scorer(), get_adapter()
        model_id = _ensure_loaded(profile)
        base_details = {"schema": WEBSITE_SCHEMA, "model_version": MODEL_VERSION, "deployment_status": "experimental",
                        "profile_source": profile.source}

        if ctx.reference_quality is not None and ctx.reference_quality.stale and profile.source == "enrolled":
            # The adapter blocks stale reviews even in local demo mode; our combined baseline record can't
            # reach it in a stale form, so the same rule is applied here.
            return PipelineResult(
                status="not_eligible", profile_id=profile.id,
                reason=("Not scored: a baseline document's text has changed since its quality review. "
                        "Review the current version."),
                details={**base_details, "website_status": "quality_review_required",
                         "quality": {"candidate": None, "references": "stale_review"}})

        candidate_review, reference_review = _quality_reviews(adapter, scorer, model_id, ctx, profile)
        try:
            res = adapter.score_for_website(
                scorer, ctx.text, model_id,
                permitted_profile_ids={model_id},   # ownership was resolved from our own tables
                candidate_review=candidate_review,
                reference_review=reference_review,
                allow_unreviewed_demo=get_settings().allow_unreviewed_consistency,
                include_evidence=True,              # sampled text stays in this process; only offsets are kept
            )
        except (KeyError, PermissionError) as e:
            raise RuntimeError(f"profile {model_id} is not loaded in the model") from e

        status = res["status"]
        details = {**base_details, "website_status": status, "input": res["input"], "quality": res["quality"]}
        if status not in ("scored", "scored_demo_unreviewed"):
            if status == "insufficient_evidence":
                inp = res["input"]
                reason = (f"Not scored: the submission has {inp['candidate_usable_words']:,} usable words after "
                          f"preprocessing and the baseline has {inp['reference_essays']} documents; the model "
                          f"needs {MIN_WORDS:,} usable words and {MIN_REFERENCE_DOCUMENTS} baseline documents.")
            else:
                reason = NOT_SCORED_REASON.get(status, res["message"])
            return PipelineResult(status="not_eligible", reason=reason, profile_id=profile.id, details=details)

        result = res["original_model_result"]       # includes sampled text: used below, never stored
        m = res["measurements"]
        confidence, confidence_reason = confidence_for(profile)
        evidence = self.build_evidence(ctx.text, result, m, profile, unreviewed=status == "scored_demo_unreviewed")
        windows = []
        for w in result["evidence"]["windows"]:
            span = locate(ctx.text, w["sampled_text"])
            windows.append({"window": w["window_id"], "raw_distance": w["raw_distance"],
                            "sampled_words": w["sampled_words"],
                            "span": None if span is None else {"start": span[0], "end": span[1], "status": span[2]}})
        details.update({k: result.get(k) for k in (
            "model", "profile_papers", "profile_maturity", "candidate_usable_words", "candidate_windows",
            "usable_features", "local_typical_genuine_distance", "background_documents", "background_authors",
            "interpretation_warning", "profile_artifact_sha256")})
        details.update(measurements=m, message=res["message"], confidence_reason=confidence_reason,
                       windows=windows)
        return PipelineResult(
            status="completed",
            score=m["v1_3_centered_similarity"],
            threshold=m["frozen_threshold"],
            outcome=res["model_decision"],
            confidence=confidence,
            profile_id=profile.id,
            details=details,
            evidence=evidence,
        )

    def build_evidence(self, text: str, result: dict, m: dict, profile: ProfileRef,
                       unreviewed: bool = False) -> list[EvidenceItem]:
        baseline = ("the demonstration reference profile" if profile.source == "demonstration"
                    else "the student's past writing")
        diag = result["evidence"]
        windows = diag["windows"]
        spans_by_window: dict[int, Span] = {}
        for w in windows:
            found = locate(text, w["sampled_text"])
            if found:
                spans_by_window[w["window_id"]] = Span(found[0], found[1], found[2], w["window_id"])

        items: list[EvidenceItem] = []
        score, threshold, margin = m["v1_3_centered_similarity"], m["frozen_threshold"], m["margin_to_threshold"]
        label = ("style compatible with the reference profile" if result["decision"] == "same-author-compatible"
                 else "stylistically unusual relative to the reference profile")
        side = "above" if margin > 0 else "below"
        margin_text = ("right at the threshold" if fmt_measure(margin) == "near zero"
                       else f"{fmt_measure(abs(margin))} {side} it")
        caveat = (" The language and extraction of the texts have not been reviewed, so this is an unreviewed "
                  "demonstration result." if unreviewed else "")
        items.append(EvidenceItem(
            category="style", kind="overall_score",
            statement=(f"Experimental comparison. The V1.3 centred style score is {fmt_measure(score)} against a "
                       f"frozen threshold of {fmt_measure(threshold)} ({margin_text}), so the model labels this "
                       f"submission {label}. The score compares three sampled passages with {baseline} "
                       f"({result['profile_papers']} documents); it is not a probability or a percentage and "
                       f"can be negative.{caveat}"),
            observed=score, details={"threshold": threshold, "decision": result["decision"], "margin": margin},
            spans=list(spans_by_window.values()),
        ))

        rel, raw, typical = m["relative_distance"], m["raw_distance"], m["shrunk_typical_genuine_distance"]
        items.append(EvidenceItem(
            category="style", kind="style_distance",
            statement=(f"Raw style distance from {baseline} is {fmt_measure(raw, 2)}, against a typical distance "
                       f"of {fmt_measure(typical, 2)} between the baseline's own documents, so the submission is "
                       f"{fmt_measure(rel, 2)} times the typical distance (1 means as far as the baseline documents "
                       f"typically are from each other). This is descriptive only; the label above comes from "
                       f"the centred score."),
            observed=rel, details={"raw_distance": raw, "typical_distance": typical, "relative_distance": rel},
        ))

        # Which groups of features differ most (averaged over the three passages).
        groups: dict[str, list[float]] = {}
        for w in windows:
            for g, d in w["group_distances"].items():
                if d is not None:
                    groups.setdefault(g, []).append(d)
        group_mean = {g: statistics.fmean(v) for g, v in groups.items() if v}
        total = sum(group_mean.values()) or 1.0
        for g, d in sorted(group_mean.items(), key=lambda kv: kv[1], reverse=True)[:3]:
            items.append(EvidenceItem(
                category="style", kind="feature_group",
                statement=(f"{GROUP_LABEL.get(g, g)}: a typical difference of {fmt_measure(d)} spreads from {baseline} "
                           f"(averaged over the sampled passages; 0 means identical to the baseline average)."),
                observed=d, contribution=d / total, details={"group": g},
            ))

        # Individual features that are unusual in a consistent direction across passages.
        per_feature: dict[str, list[tuple[int, dict]]] = {}
        for w in windows:
            for f in w["features"]:
                if f["used_in_distance"] and f["clipped_z_score"] is not None:
                    per_feature.setdefault(f["feature"], []).append((w["window_id"], f))
        candidates = []
        for name, rows in per_feature.items():
            zs = [f["clipped_z_score"] for _, f in rows]
            med = statistics.median(zs)
            if abs(med) < self.min_abs_z:
                continue
            same_dir = [(wid, f) for wid, f in rows if abs(f["clipped_z_score"]) >= self.min_abs_z
                        and (f["clipped_z_score"] > 0) == (med > 0)]
            if len(same_dir) < 2:
                continue
            candidates.append((abs(med), name, med, rows, same_dir))
        candidates.sort(reverse=True)
        # Some words are counted twice by the model, e.g. "although" and "while" are both function words
        # and transition words, giving two features with identical values. Show each label once.
        seen_labels: set[str] = set()
        unique = []
        for c in candidates:
            lab = feature_label(c[1])[0]
            if lab not in seen_labels:
                seen_labels.add(lab)
                unique.append(c)
        candidates = unique
        clip_total = sum(c[0] for c in candidates) or 1.0
        for absz, name, med, rows, same_dir in candidates[: self.max_feature_items]:
            f0 = rows[0][1]
            mean, sd = f0["reference_mean"], f0["reference_std"]
            observed = statistics.median(f["candidate_value"] for _, f in rows)
            label, unit = feature_label(name)
            direction = "higher" if med > 0 else "lower"
            clipped = " (capped at 4 in the score)" if absz >= 4 else ""
            items.append(EvidenceItem(
                category="style", kind="feature_deviation",
                statement=(f"{label}: {_num(observed, unit)}{_unit(unit)} in this submission against "
                           f"{_num(mean, unit)}{_unit(unit)} in {baseline} (typical spread {_num(sd, unit)}). "
                           f"That is {absz:.1f} spreads {direction}{clipped}, in {len(same_dir)} of "
                           f"{len(rows)} sampled passages."),
                observed=observed, baseline_mean=mean, baseline_sd=sd, z_score=med,
                contribution=absz / clip_total,
                spans=[spans_by_window[wid] for wid, _ in same_dir if wid in spans_by_window],
                details={"feature": name, "group": f0["group"],
                         "windows": [{"window": wid, "z": f["clipped_z_score"]} for wid, f in rows]},
            ))
        return items
