from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import build_multiwindow_corpus_v1_fixed as builder
from . import evaluate_multiwindow_authorship_v1 as mw
from . import viskopic_calibration_v1_2 as cal
from .score_candidate_pdf_v1_2 import load_profile_manifest


WINDOWS = 3
WINDOW_WORDS = 550
MIN_CLEAN_WORDS = 1650
MIN_PROFILE_PAPERS = 8
EPS = 1e-8


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalise_plain_text(text: str) -> list[str]:
    text = text.replace("\u00ad", "").replace("\u200b", "").replace("\ufeff", "")
    blocks = re.split(r"\n\s*\n", text)
    paragraphs = []
    for block in blocks:
        cleaned = re.sub(r"[ \t\r\n]+", " ", block).strip()
        if builder.wc(cleaned) >= 4:
            paragraphs.append(cleaned)
    return paragraphs


def build_candidate_windows(text_path: Path, output_dir: Path) -> tuple[pd.DataFrame, int]:
    text = text_path.read_text(encoding="utf-8", errors="replace")
    paragraphs = normalise_plain_text(text)
    units = builder.sentence_units(paragraphs)
    cumulative, total_words = builder.cumulative_word_positions(units)

    if total_words < MIN_CLEAN_WORDS:
        raise ValueError(
            f"Candidate has only {total_words} usable words; frozen V1.3 requires "
            f"at least {MIN_CLEAN_WORDS}."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    fractions = [(index + 0.5) / WINDOWS for index in range(WINDOWS)]
    for window_id, fraction in enumerate(fractions, start=1):
        window_text = builder.make_centered_window(
            units=units,
            cumulative=cumulative,
            total_words=total_words,
            center_fraction=fraction,
            target_words=WINDOW_WORDS,
        )
        words = builder.wc(window_text)
        if words < int(WINDOW_WORDS * 0.65):
            raise RuntimeError(f"Candidate window {window_id} is too short: {words} words.")
        window_path = output_dir / f"{text_path.stem}__WINDOW_{window_id:02d}.txt"
        window_path.write_text(window_text, encoding="utf-8")
        rows.append(
            {
                "author": "candidate",
                "paper_id": text_path.stem,
                "window_id": window_id,
                "window_path": window_path.as_posix(),
                "window_words": words,
                "center_fraction": fraction,
                "status": "ok",
            }
        )
    return pd.DataFrame(rows), total_words


def verify_frozen_sources(v13_cfg: dict, comparisons_path: Path, v12_path: Path) -> None:
    expected_comparisons = v13_cfg["source_files"]["development_comparisons_sha256"]
    expected_v12 = v13_cfg["source_files"]["base_calibration_sha256"]
    actual_comparisons = sha256_file(comparisons_path)
    actual_v12 = sha256_file(v12_path)
    if actual_comparisons != expected_comparisons:
        raise RuntimeError(
            "Development comparison hash does not match the frozen V1.3 specification."
        )
    if actual_v12 != expected_v12:
        raise RuntimeError("V1.2 calibration hash does not match the frozen V1.3 specification.")


def add_v12_similarity(comparisons: pd.DataFrame, config: cal.CalibrationConfig) -> pd.DataFrame:
    required = {
        "candidate_author",
        "candidate_paper",
        "profile_author",
        "profile_distance",
        "typical_genuine_distance",
        "reference_document_count",
    }
    missing = required - set(comparisons.columns)
    if missing:
        raise RuntimeError("Development comparisons missing columns: " + ", ".join(sorted(missing)))

    result = comparisons.copy()
    raw = pd.to_numeric(result["profile_distance"], errors="raise").to_numpy(float)
    local_t = pd.to_numeric(result["typical_genuine_distance"], errors="raise").to_numpy(float)
    n_docs = pd.to_numeric(result["reference_document_count"], errors="raise").to_numpy(float)
    shrunk_t = (n_docs * local_t + config.k * config.global_t) / (n_docs + config.k)
    relative = raw / np.maximum(shrunk_t, EPS)
    result["v1_2_similarity"] = 100.0 * np.exp(-0.5 * relative**2)
    return result


def frozen_background_median(
    comparisons: pd.DataFrame,
    target_author: str,
) -> tuple[float, int, int]:
    rows = comparisons.loc[
        comparisons["profile_author"].astype(str).eq(target_author)
        & ~comparisons["candidate_author"].astype(str).eq(target_author)
    ].copy()
    rows = rows.drop_duplicates(["candidate_author", "candidate_paper"])
    values = pd.to_numeric(rows["v1_2_similarity"], errors="coerce").dropna()
    if len(values) < 100:
        raise RuntimeError(
            f"Only {len(values)} frozen background documents were available for {target_author}."
        )
    return (
        float(values.median()),
        int(len(values)),
        int(rows["candidate_author"].nunique()),
    )


def discover_profiles(root: Path, valid_authors: set[str]) -> pd.DataFrame:
    candidates = sorted(root.glob("*_multiwindow_v1_qc.csv"))
    if not candidates:
        candidates = sorted(root.glob("*_multiwindow_v1.csv"))
    rows = []
    for path in candidates:
        try:
            author, manifest = load_profile_manifest(path)
        except Exception:
            continue
        if author not in valid_authors:
            continue
        rows.append(
            {
                "author": author,
                "complete_profile_papers": int(manifest["paper_id"].nunique()),
                "eligible_for_demo": int(manifest["paper_id"].nunique()) >= MIN_PROFILE_PAPERS,
                "manifest": path.as_posix(),
            }
        )
    return pd.DataFrame(rows).sort_values(["eligible_for_demo", "author"], ascending=[False, True])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score a plain-text candidate against one frozen Viskopic V1.3 author profile."
    )
    parser.add_argument("--profile-manifest")
    parser.add_argument("--candidate-text")
    parser.add_argument(
        "--profile-root",
        default="data/large_corpus/multiwindow_manifests_v1",
        help="Used only by --list-profiles.",
    )
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--v12-config", default="calibration_v1_2.json")
    parser.add_argument("--v13-config", default="calibration_v1_3_background_centering.json")
    parser.add_argument(
        "--comparisons",
        default="evaluation_results_large_corpus_v1_min5/all_comparisons.csv",
    )
    parser.add_argument("--output-dir", default="reports/tom_v1_3_live_demo")
    parser.add_argument("--allow-low-confidence", action="store_true")
    args = parser.parse_args()

    v12_path = Path(args.v12_config)
    v13_path = Path(args.v13_config)
    comparisons_path = Path(args.comparisons)
    v13_cfg = json.loads(v13_path.read_text(encoding="utf-8"))
    v12_cfg = cal.load_config(v12_path)
    verify_frozen_sources(v13_cfg, comparisons_path, v12_path)
    comparisons = add_v12_similarity(pd.read_csv(comparisons_path), v12_cfg)
    valid_authors = set(comparisons["profile_author"].astype(str).unique())

    if args.list_profiles:
        profiles = discover_profiles(Path(args.profile_root), valid_authors)
        if profiles.empty:
            raise RuntimeError(
                "No matching development-profile manifests were found. Check --profile-root."
            )
        print(profiles.to_string(index=False))
        return

    if not args.profile_manifest or not args.candidate_text:
        parser.error("--profile-manifest and --candidate-text are required unless --list-profiles is used.")

    profile_path = Path(args.profile_manifest)
    candidate_path = Path(args.candidate_text)
    if not candidate_path.exists():
        raise FileNotFoundError(candidate_path)
    author, profile_manifest = load_profile_manifest(profile_path)
    profile_papers = int(profile_manifest["paper_id"].nunique())
    if author not in valid_authors:
        raise ValueError(
            f"Profile {author!r} is not one of the frozen 58 development profiles, so its "
            "background median is unavailable."
        )
    if profile_papers < MIN_PROFILE_PAPERS and not args.allow_low_confidence:
        raise ValueError(
            f"Profile has only {profile_papers} complete papers; the live demo requires at "
            f"least {MIN_PROFILE_PAPERS}."
        )

    output_dir = Path(args.output_dir)
    candidate_manifest, candidate_words = build_candidate_windows(
        candidate_path, output_dir / "candidate_windows"
    )
    profile_features, _ = mw.extract_window_features(profile_manifest)
    candidate_features, _ = mw.extract_window_features(candidate_manifest)
    combined = pd.concat([profile_features, candidate_features], ignore_index=True)
    feature_columns = mw.numeric_feature_columns(combined)
    raw_distance, window_rows, usable_features = mw.score_document_raw(
        profile_features, candidate_features, feature_columns
    )
    calibration_distances = mw.reference_document_distances(profile_features, feature_columns)
    if not calibration_distances:
        raise RuntimeError("No genuine profile calibration distances could be computed.")
    local_t = float(np.median(np.asarray(calibration_distances, dtype=float)))
    v12 = cal.score_with_shrinkage(
        raw_distance=raw_distance,
        local_t=local_t,
        reference_document_count=profile_papers,
        global_t=v12_cfg.global_t,
        k=v12_cfg.k,
    )
    background_median, background_docs, background_authors = frozen_background_median(
        comparisons, author
    )
    v13_score = float(v12["similarity_score"] - background_median)
    threshold = float(v13_cfg["operating_threshold"]["frozen_threshold"])
    compatible = bool(v13_score >= threshold)

    result = {
        "model": v13_cfg["version"],
        "profile_author": author,
        "profile_manifest": profile_path.as_posix(),
        "profile_papers": profile_papers,
        "profile_maturity": cal.maturity_label(profile_papers),
        "candidate_text": candidate_path.as_posix(),
        "candidate_usable_words": candidate_words,
        "candidate_windows": int(len(candidate_features)),
        "usable_features": int(usable_features),
        "raw_distance": float(raw_distance),
        "local_typical_genuine_distance": local_t,
        "shrunk_typical_genuine_distance": float(v12["shrunk_typical_genuine_distance"]),
        "v1_2_similarity": float(v12["similarity_score"]),
        "background_median_v1_2_similarity": background_median,
        "background_documents": background_docs,
        "background_authors": background_authors,
        "v1_3_centered_similarity": v13_score,
        "frozen_threshold": threshold,
        "decision": "same-author-compatible" if compatible else "stylistically-unusual",
        "interpretation_warning": v13_cfg["interpretation_warning"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "candidate_score_v1_3.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    pd.DataFrame([result]).to_csv(output_dir / "candidate_score_v1_3.csv", index=False)
    pd.DataFrame(window_rows).to_csv(output_dir / "candidate_window_distances_v1_3.csv", index=False)
    candidate_manifest.to_csv(output_dir / "candidate_manifest_v1_3.csv", index=False)

    print("=" * 90)
    print("VISKOPIC V1.3 — LIVE CANDIDATE-TEXT SCORE")
    print("=" * 90)
    print(f"Profile:              {author}")
    print(f"Reference papers:     {profile_papers} ({result['profile_maturity']})")
    print(f"Candidate words:      {candidate_words}")
    print(f"Candidate windows:    {len(candidate_features)} x approximately {WINDOW_WORDS} words")
    print(f"Raw distance D:       {raw_distance:.4f}")
    print(f"V1.2 similarity:      {v12['similarity_score']:.4f}")
    print(f"Background median:    {background_median:.4f} ({background_docs} docs)")
    print(f"V1.3 centered score:  {v13_score:.4f}")
    print(f"Frozen threshold:     {threshold:.4f}")
    print(f"Decision:             {result['decision']}")
    print()
    print(v13_cfg["interpretation_warning"])
    print(f"Detailed output: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
