from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from . import build_multiwindow_corpus_v1_fixed as builder
from . import evaluate_multiwindow_authorship_v1 as mw
from . import viskopic_calibration_v1_2 as cal


WINDOWS = 3
WINDOW_WORDS = 550
MIN_CLEAN_WORDS = 1650


def load_profile_manifest(path: Path) -> tuple[str, pd.DataFrame]:
    d = pd.read_csv(path)

    required = {"author", "paper_id", "window_id", "window_path", "status"}
    missing = required - set(d.columns)
    if missing:
        raise ValueError(f"Profile manifest missing columns: {sorted(missing)}")

    d = d[d["status"].eq("ok")].copy()
    authors = sorted(d["author"].dropna().astype(str).unique())
    if len(authors) != 1:
        raise ValueError(f"Expected exactly one profile author, found {authors}")

    author = authors[0]
    counts = d.groupby("paper_id")["window_id"].nunique()
    good_papers = counts[counts.eq(WINDOWS)].index
    d = d[d["paper_id"].isin(good_papers)].copy()

    if d.empty:
        raise RuntimeError("No complete 3-window profile papers found.")

    return author, d.sort_values(["paper_id", "window_id"]).reset_index(drop=True)


def build_candidate_windows(pdf: Path, output_dir: Path) -> tuple[pd.DataFrame, dict]:
    paragraphs, meta = builder.clean_full_pdf(pdf)
    units = builder.sentence_units(paragraphs)
    cumulative, total_words = builder.cumulative_word_positions(units)

    if total_words < MIN_CLEAN_WORDS:
        raise ValueError(
            f"Candidate has only {total_words} usable clean words; "
            f"V1.2 requires at least {MIN_CLEAN_WORDS}."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []

    fractions = [(i + 0.5) / WINDOWS for i in range(WINDOWS)]

    for window_idx, fraction in enumerate(fractions, start=1):
        text = builder.make_centered_window(
            units=units,
            cumulative=cumulative,
            total_words=total_words,
            center_fraction=fraction,
            target_words=WINDOW_WORDS,
        )

        words_in_window = builder.wc(text)
        if words_in_window < int(WINDOW_WORDS * 0.65):
            raise RuntimeError(
                f"Candidate window {window_idx} is too short: {words_in_window} words."
            )

        path = output_dir / f"{pdf.stem}__WINDOW_{window_idx:02d}.txt"
        path.write_text(text, encoding="utf-8")

        rows.append(
            {
                "author": "candidate",
                "paper_id": pdf.stem,
                "window_id": window_idx,
                "window_path": path.as_posix(),
                "window_words": words_in_window,
                "center_fraction": fraction,
                "status": "ok",
            }
        )

    return pd.DataFrame(rows), {**meta, "clean_words_for_windows": total_words}


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "Score a new PDF against one frozen 3x550 Viskopic author profile "
            "using Calibration V1.2 (k=5 shrinkage)."
        )
    )
    p.add_argument("--profile-manifest", required=True)
    p.add_argument("--candidate-pdf", required=True)
    p.add_argument("--calibration-config", default="calibration_v1_2.json")
    p.add_argument("--output-dir", default="candidate_score_v1_2")
    p.add_argument(
        "--allow-immature",
        action="store_true",
        help="Allow profiles with <6 reference papers for diagnostics only.",
    )
    args = p.parse_args()

    config = cal.load_config(args.calibration_config)
    author, profile_manifest = load_profile_manifest(Path(args.profile_manifest))
    profile_papers = int(profile_manifest["paper_id"].nunique())
    maturity = cal.maturity_label(profile_papers)

    if profile_papers < 6 and not args.allow_immature:
        raise ValueError(
            f"Profile has only {profile_papers} complete papers and is labelled "
            "'insufficient'. Use --allow-immature only for diagnostics."
        )

    candidate_pdf = Path(args.candidate_pdf)
    if not candidate_pdf.exists():
        raise FileNotFoundError(candidate_pdf)

    out = Path(args.output_dir)
    candidate_manifest, cleaning_meta = build_candidate_windows(
        candidate_pdf,
        out / "candidate_windows",
    )

    print("=" * 90)
    print("VISKOPIC CANDIDATE PDF SCORER — CALIBRATION V1.2")
    print("=" * 90)
    print(f"Profile author:  {author}")
    print(f"Profile papers:  {profile_papers} ({maturity})")
    print(f"Candidate PDF:   {candidate_pdf.name}")
    print(f"Candidate clean: {cleaning_meta['clean_words_for_windows']} words")
    print(f"Calibration:     k={config.k:g}, global T={config.global_t:.6f}")
    print()

    profile_features, _ = mw.extract_window_features(profile_manifest)
    candidate_features, _ = mw.extract_window_features(candidate_manifest)

    combined = pd.concat([profile_features, candidate_features], ignore_index=True)
    candidate_columns = mw.numeric_feature_columns(combined)

    raw_distance, window_rows, usable_features = mw.score_document_raw(
        profile_features,
        candidate_features,
        candidate_columns,
    )

    calibration_distances = mw.reference_document_distances(
        profile_features,
        candidate_columns,
    )
    if not calibration_distances:
        raise RuntimeError("Could not compute genuine profile calibration distances.")

    local_t = float(np.median(np.asarray(calibration_distances, dtype=float)))
    score = cal.score_with_shrinkage(
        raw_distance=raw_distance,
        local_t=local_t,
        reference_document_count=profile_papers,
        global_t=config.global_t,
        k=config.k,
    )

    score_row = {
        "profile_author": author,
        "candidate_pdf": candidate_pdf.name,
        "candidate_windows": len(candidate_features),
        "candidate_clean_words": cleaning_meta["clean_words_for_windows"],
        "usable_features": usable_features,
        "calibration_document_count": len(calibration_distances),
        **score,
    }

    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([score_row]).to_csv(out / "candidate_score_v1_2.csv", index=False)
    pd.DataFrame(window_rows).to_csv(out / "candidate_window_distances_v1_2.csv", index=False)
    candidate_manifest.to_csv(out / "candidate_manifest_v1_2.csv", index=False)

    print("RESULT")
    print(f"Raw D:          {score['raw_distance']:.4f}")
    print(f"Local T:        {score['local_typical_genuine_distance']:.4f}")
    print(f"Shrunk T*:      {score['shrunk_typical_genuine_distance']:.4f}")
    print(f"Relative R*:    {score['relative_distance']:.4f}")
    print(f"Similarity:     {score['similarity_score']:.2f}/100")
    print(f"Profile maturity: {score['profile_maturity']}")
    print()
    print("Important: similarity is an experimental consistency measure, not proof of authorship or AI use.")
    print(f"Results: {out.resolve()}")


if __name__ == "__main__":
    main()
