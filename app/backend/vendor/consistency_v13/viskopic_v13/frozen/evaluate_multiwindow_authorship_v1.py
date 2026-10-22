from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import evaluate_authorship as base


def load_manifest(path: Path, author: str) -> pd.DataFrame:
    d = pd.read_csv(path)

    d = d[
        (d["author"] == author)
        & (d["status"] == "ok")
    ].copy()

    if d.empty:
        raise RuntimeError(
            f"No usable windows for {author}"
        )

    required = {
        "paper_id",
        "window_id",
        "window_path",
    }

    missing = required - set(d.columns)

    if missing:
        raise ValueError(
            "Manifest missing columns: "
            + ", ".join(sorted(missing))
        )

    return d.sort_values(
        ["paper_id", "window_id"]
    ).reset_index(drop=True)


def extract_window_features(d: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    rows = []
    meta = {}

    for i, row in d.iterrows():
        path = Path(row["window_path"])

        if not path.exists():
            raise FileNotFoundError(
                f"Window file missing: {path}"
            )

        text = path.read_text(
            encoding="utf-8",
            errors="replace",
        ).strip()

        features = base.extract_features(text)

        feature_row = {
            "paper_id": row["paper_id"],
            "window_id": int(row["window_id"]),
            "window_path": path.as_posix(),
            **features,
        }

        rows.append(feature_row)

        meta[
            (
                row["paper_id"],
                int(row["window_id"]),
            )
        ] = {
            "window_path": path.as_posix(),
        }

        print(
            f"[FEATURES {i+1:03d}/{len(d):03d}] "
            f"{row['paper_id'][:65]} "
            f"window {int(row['window_id'])}"
        )

    return pd.DataFrame(rows), meta


def numeric_feature_columns(
    features: pd.DataFrame,
) -> list[str]:
    excluded = {
        "paper_id",
        "window_id",
        "window_path",
    }

    return [
        c
        for c in features.columns
        if c not in excluded
        and c not in base.EXCLUDED_FROM_DISTANCE
        and pd.api.types.is_numeric_dtype(features[c])
    ]


def profile_window_distance(
    reference_windows: pd.DataFrame,
    candidate_window: pd.Series,
    candidate_columns: list[str],
) -> tuple[float, dict[str, float], int]:
    columns = base.usable_features(
        reference_windows,
        candidate_columns,
    )

    if len(columns) < 3:
        raise ValueError(
            "Fewer than 3 usable features"
        )

    _, _, z_scores = base.clipped_z_scores(
        reference_windows,
        candidate_window,
        columns,
    )

    distance, group_distances = (
        base.group_balanced_distance(z_scores)
    )

    return (
        float(distance),
        group_distances,
        len(columns),
    )


def score_document_raw(
    reference_windows: pd.DataFrame,
    candidate_windows: pd.DataFrame,
    candidate_columns: list[str],
) -> tuple[float, list[dict], int]:
    window_rows = []
    usable_counts = []

    for _, candidate in candidate_windows.iterrows():
        distance, group_distances, usable = (
            profile_window_distance(
                reference_windows,
                candidate,
                candidate_columns,
            )
        )

        usable_counts.append(usable)

        row = {
            "paper_id": candidate["paper_id"],
            "window_id": int(candidate["window_id"]),
            "window_path": candidate["window_path"],
            "profile_distance": distance,
            "usable_features": usable,
        }

        for group, gd in group_distances.items():
            row[f"group_{group}_distance"] = gd

        window_rows.append(row)

    distances = np.asarray(
        [
            row["profile_distance"]
            for row in window_rows
        ],
        dtype=float,
    )

    document_distance = float(
        np.median(distances)
    )

    typical_usable = int(
        round(float(np.median(usable_counts)))
    )

    return (
        document_distance,
        window_rows,
        typical_usable,
    )


def reference_document_distances(
    reference_windows: pd.DataFrame,
    candidate_columns: list[str],
) -> list[float]:
    """
    Nested document-level leave-one-out calibration.

    Each reference paper is held out in full. No window from that paper
    is allowed into the profile used to score it.
    """
    distances = []

    ref_papers = sorted(
        reference_windows["paper_id"].unique()
    )

    for heldout_paper in ref_papers:
        train = reference_windows[
            reference_windows["paper_id"]
            != heldout_paper
        ].copy()

        held = reference_windows[
            reference_windows["paper_id"]
            == heldout_paper
        ].copy()

        if (
            train["paper_id"].nunique() < 2
            or held.empty
        ):
            continue

        try:
            doc_distance, _, _ = (
                score_document_raw(
                    train,
                    held,
                    candidate_columns,
                )
            )
        except Exception:
            continue

        distances.append(
            float(doc_distance)
        )

    return distances


def quantiles(values: list[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=float)

    return {
        "window_distance_min": float(np.min(arr)),
        "window_distance_q25": float(np.quantile(arr, 0.25)),
        "window_distance_median": float(np.median(arr)),
        "window_distance_q75": float(np.quantile(arr, 0.75)),
        "window_distance_max": float(np.max(arr)),
        "window_distance_iqr": float(
            np.quantile(arr, 0.75)
            - np.quantile(arr, 0.25)
        ),
        "window_distance_range": float(
            np.max(arr) - np.min(arr)
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Document-group-aware multi-window authorship evaluation. "
            "Holds out every window of a paper together."
        )
    )

    parser.add_argument(
        "--manifest",
        required=True,
    )
    parser.add_argument(
        "--author",
        required=True,
    )
    parser.add_argument(
        "--output-dir",
        default="evaluation_multiwindow_v1",
    )

    args = parser.parse_args()

    manifest = load_manifest(
        Path(args.manifest),
        args.author,
    )

    paper_counts = (
        manifest.groupby("paper_id")
        .size()
        .sort_values()
    )

    if len(paper_counts) < 5:
        raise ValueError(
            "At least 5 papers are required."
        )

    print("=" * 78)
    print("VISKOPIC MULTI-WINDOW CONSISTENCY EVALUATOR V1")
    print("=" * 78)
    print(f"Author:  {args.author}")
    print(f"Papers:  {len(paper_counts)}")
    print(f"Windows: {len(manifest)}")
    print(
        "Windows per paper: "
        f"{sorted(paper_counts.unique())}"
    )
    print()

    features, _ = extract_window_features(
        manifest
    )

    candidate_columns = numeric_feature_columns(
        features
    )

    papers = sorted(
        features["paper_id"].unique()
    )

    document_results = []
    all_window_results = []

    for i, paper in enumerate(papers, start=1):
        print()
        print(
            f"[DOCUMENT {i:02d}/{len(papers):02d}] "
            f"{paper[:95]}"
        )

        candidate = features[
            features["paper_id"] == paper
        ].copy()

        reference = features[
            features["paper_id"] != paper
        ].copy()

        reference_doc_count = int(
            reference["paper_id"].nunique()
        )

        (
            raw_doc_distance,
            window_rows,
            usable_features,
        ) = score_document_raw(
            reference,
            candidate,
            candidate_columns,
        )

        calibration = reference_document_distances(
            reference,
            candidate_columns,
        )

        if not calibration:
            raise RuntimeError(
                f"No calibration distances for {paper}"
            )

        typical = float(
            np.median(
                np.asarray(calibration, dtype=float)
            )
        )

        typical = max(typical, 1e-8)

        relative = (
            raw_doc_distance / typical
        )

        similarity = (
            100.0
            * math.exp(
                -0.5 * relative ** 2
            )
        )

        window_distances = [
            float(r["profile_distance"])
            for r in window_rows
        ]

        stability = quantiles(
            window_distances
        )

        # Candidate-window similarity using the same document-level
        # genuine calibration, useful for diagnostics only.
        for row in window_rows:
            wr = dict(row)
            wr["reference_document_count"] = reference_doc_count
            wr["typical_genuine_document_distance"] = typical
            wr["window_relative_to_document_typical"] = (
                wr["profile_distance"] / typical
            )
            wr["window_similarity_diagnostic"] = (
                100.0
                * math.exp(
                    -0.5
                    * wr["window_relative_to_document_typical"] ** 2
                )
            )
            all_window_results.append(wr)

        result = {
            "paper_id": paper,
            "candidate_windows": len(candidate),
            "reference_document_count": reference_doc_count,
            "reference_window_count": len(reference),
            "candidate_features": len(candidate_columns),
            "usable_features": usable_features,
            "profile_distance": raw_doc_distance,
            "typical_genuine_distance": typical,
            "relative_distance": relative,
            "similarity_score": similarity,
            "calibration_document_count": len(calibration),
            **stability,
        }

        document_results.append(result)

        print(
            f"    windows={len(candidate)} "
            f"refs={reference_doc_count} papers / {len(reference)} windows"
        )
        print(
            f"    D={raw_doc_distance:.3f} "
            f"typical={typical:.3f} "
            f"R={relative:.3f} "
            f"score={similarity:.1f}"
        )
        print(
            f"    window D range="
            f"{stability['window_distance_min']:.3f}"
            f"–{stability['window_distance_max']:.3f} "
            f"IQR={stability['window_distance_iqr']:.3f}"
        )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    docs = pd.DataFrame(
        document_results
    ).sort_values(
        "similarity_score",
        ascending=False,
    )

    windows = pd.DataFrame(
        all_window_results
    ).sort_values(
        ["paper_id", "window_id"]
    )

    docs_path = (
        out_dir
        / "document_results.csv"
    )
    windows_path = (
        out_dir
        / "window_results.csv"
    )

    docs.to_csv(
        docs_path,
        index=False,
    )
    windows.to_csv(
        windows_path,
        index=False,
    )

    print()
    print("=" * 78)
    print("MULTI-WINDOW AUTHOR SUMMARY")
    print("=" * 78)
    print(f"Papers tested:          {len(docs)}")
    print(
        "Mean similarity:        "
        f"{docs['similarity_score'].mean():.2f}"
    )
    print(
        "Median similarity:      "
        f"{docs['similarity_score'].median():.2f}"
    )
    print(
        "Minimum similarity:     "
        f"{docs['similarity_score'].min():.2f}"
    )
    print(
        "Maximum similarity:     "
        f"{docs['similarity_score'].max():.2f}"
    )
    print(
        "Mean relative distance: "
        f"{docs['relative_distance'].mean():.3f}"
    )
    print(
        "Median relative dist:   "
        f"{docs['relative_distance'].median():.3f}"
    )
    print(
        "Median window IQR:      "
        f"{docs['window_distance_iqr'].median():.3f}"
    )
    print(
        "Median window range:    "
        f"{docs['window_distance_range'].median():.3f}"
    )

    print()
    print("LOWEST 5")
    print(
        docs.nsmallest(
            5,
            "similarity_score",
        )[
            [
                "paper_id",
                "similarity_score",
                "relative_distance",
                "window_distance_iqr",
                "window_distance_range",
            ]
        ].to_string(index=False)
    )

    print()
    print("HIGHEST 5")
    print(
        docs.nlargest(
            5,
            "similarity_score",
        )[
            [
                "paper_id",
                "similarity_score",
                "relative_distance",
                "window_distance_iqr",
                "window_distance_range",
            ]
        ].to_string(index=False)
    )

    print()
    print(f"Document results: {docs_path.resolve()}")
    print(f"Window results:   {windows_path.resolve()}")
    print()
    print(
        "Important: all windows from the candidate paper are held out "
        "together. No candidate-paper window enters its own profile."
    )


if __name__ == "__main__":
    main()
