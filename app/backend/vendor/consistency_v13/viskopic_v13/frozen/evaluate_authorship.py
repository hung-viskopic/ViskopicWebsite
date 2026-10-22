from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .extract_features_v2_new import extract_features


# ---------------------------------------------------------------------------
# Scoring configuration
# This matches the updated group-balanced Viskopic scorer.
# ---------------------------------------------------------------------------

EXCLUDED_FROM_DISTANCE = {
    "word_count",
    "sentence_count",
    "paragraph_count",
    "character_count",
    "character_count_no_spaces",
    "unique_word_count",
    "sentence_length_min",
    "sentence_length_max",
    "avg_paragraph_length",
    "paragraph_length_std",
}

FEATURE_GROUPS = {
    "sentence_structure": [
        "avg_sentence_length",
        "sentence_length_std",
        "long_sentences_ratio",
        "short_sentences_ratio",
        "unique_sentence_opening_ratio",
        "repeated_sentence_opening_ratio",
    ],
    "vocabulary": [
        "avg_word_length",
        "word_length_std",
        "type_token_ratio",
        "root_type_token_ratio",
        "corrected_type_token_ratio",
        "hapax_ratio",
        "hapax_unique_ratio",
        "dislegomena_ratio",
        "repeated_word_ratio",
        "long_words_ratio",
        "very_long_words_ratio",
    ],
    "readability": [
        "avg_syllables_per_word",
        "complex_word_ratio",
        "flesch_reading_ease",
        "flesch_kincaid_grade",
        "gunning_fog_index",
    ],
    "punctuation": [
        "commas_per_100_words",
        "semicolons_per_100_words",
        "colons_per_100_words",
        "question_marks_per_100_words",
        "exclamation_marks_per_100_words",
        "parentheses_per_100_words",
        "hyphens_per_100_words",
    ],
    "pronouns_and_stance": [
        "first_person_per_100_words",
        "second_person_per_100_words",
        "third_person_per_100_words",
        "contractions_per_100_words",
    ],
}


@dataclass(frozen=True)
class Document:
    author: str
    path: Path
    source_split: str
    content_hash: str

    @property
    def document_id(self) -> str:
        return f"{self.author}::{self.path.name}::{self.content_hash[:10]}"


def assign_group(feature: str) -> str:
    for group, features in FEATURE_GROUPS.items():
        if feature in features:
            return group

    if feature.startswith("function_"):
        return "function_words"

    if feature.startswith("transition_"):
        return "transitions"

    if feature.startswith("sentence_opening_"):
        return "sentence_openings"

    return "other"


def numeric_feature_columns(profile: pd.DataFrame) -> list[str]:
    return [
        column
        for column in profile.columns
        if column != "essay"
        and column not in EXCLUDED_FROM_DISTANCE
        and pd.api.types.is_numeric_dtype(profile[column])
    ]


def usable_features(
    profile: pd.DataFrame,
    columns: list[str],
) -> list[str]:
    """
    Remove constant and near-constant features.

    Small author profiles can otherwise create enormous z-scores from a
    standard deviation that is nearly zero.
    """
    stds = profile[columns].std(ddof=1)
    means = profile[columns].mean()

    usable: list[str] = []

    for column in columns:
        std = stds[column]
        mean = means[column]

        if pd.isna(std) or std <= 0:
            continue

        absolute_floor = 0.02
        relative_floor = max(abs(mean) * 0.05, absolute_floor)

        if std < relative_floor:
            continue

        usable.append(column)

    return usable


def clipped_z_scores(
    reference: pd.DataFrame,
    sample: pd.Series,
    columns: list[str],
) -> tuple[pd.Series, pd.Series, pd.Series]:
    means = reference[columns].mean()
    stds = reference[columns].std(ddof=1)

    z_scores = ((sample[columns] - means) / stds).astype(float)
    z_scores = z_scores.mask(~np.isfinite(z_scores), np.nan)
    z_scores = z_scores.clip(lower=-4.0, upper=4.0)

    return means, stds, z_scores


def group_balanced_distance(
    z_scores: pd.Series,
) -> tuple[float, dict[str, float]]:
    """
    Calculate RMS distance inside each feature group, then average groups.

    This stops large groups such as function words from winning solely
    because they contain more columns.
    """
    group_distances: dict[str, float] = {}

    groups = sorted({assign_group(feature) for feature in z_scores.index})

    for group in groups:
        group_features = [
            feature
            for feature in z_scores.index
            if assign_group(feature) == group
        ]

        values = z_scores[group_features].dropna()

        if values.empty:
            continue

        group_distances[group] = float(
            np.sqrt(np.mean(np.square(values)))
        )

    if not group_distances:
        raise ValueError("No valid feature groups were available.")

    overall_distance = float(np.mean(list(group_distances.values())))

    return overall_distance, group_distances


def leave_one_out_distances(
    profile: pd.DataFrame,
    candidate_columns: list[str],
) -> np.ndarray:
    distances: list[float] = []

    for index in profile.index:
        training = profile.drop(index=index)
        held_out = profile.loc[index]

        columns = usable_features(training, candidate_columns)

        if len(columns) < 3:
            continue

        _, _, z_scores = clipped_z_scores(
            training,
            held_out,
            columns,
        )

        try:
            distance, _ = group_balanced_distance(z_scores)
        except ValueError:
            continue

        distances.append(distance)

    return np.asarray(distances, dtype=float)


def similarity_score(
    new_distance: float,
    reference_distances: np.ndarray,
) -> tuple[float, float, float]:
    if len(reference_distances) == 0:
        return float("nan"), float("nan"), float("nan")

    typical_distance = max(
        float(np.median(reference_distances)),
        1e-8,
    )

    relative_distance = new_distance / typical_distance

    score = 100.0 * math.exp(
        -0.5 * relative_distance ** 2
    )

    return (
        max(0.0, min(100.0, score)),
        relative_distance,
        typical_distance,
    )


def score_against_profile(
    reference: pd.DataFrame,
    sample: pd.Series,
) -> dict[str, float | int]:
    candidate_columns = numeric_feature_columns(reference)
    columns = usable_features(reference, candidate_columns)

    if len(columns) < 3:
        raise ValueError(
            "Fewer than three usable features were available."
        )

    _, _, z_scores = clipped_z_scores(
        reference,
        sample,
        columns,
    )

    profile_distance, group_distances = group_balanced_distance(
        z_scores
    )

    reference_distances = leave_one_out_distances(
        reference,
        candidate_columns,
    )

    score, relative_distance, typical_distance = similarity_score(
        profile_distance,
        reference_distances,
    )

    if math.isnan(score):
        raise ValueError(
            "The profile could not produce leave-one-out calibration."
        )

    result: dict[str, float | int] = {
        "similarity_score": score,
        "profile_distance": profile_distance,
        "typical_genuine_distance": typical_distance,
        "relative_distance": relative_distance,
        "candidate_features": len(candidate_columns),
        "usable_features": len(columns),
    }

    for group, distance in group_distances.items():
        result[f"group_{group}_distance"] = distance
        result[f"group_{group}_similarity"] = (
            100.0 * math.exp(-0.5 * distance ** 2)
        )

    return result


# ---------------------------------------------------------------------------
# Corpus discovery and validation
# ---------------------------------------------------------------------------

def normalised_text_hash(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    normalised = re.sub(r"\s+", " ", text.lower()).strip()

    return hashlib.sha256(
        normalised.encode("utf-8")
    ).hexdigest()


def collect_txt_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []

    return sorted(
        path
        for path in folder.glob("*.txt")
        if path.is_file()
    )


def parse_extra_test(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            "Use AUTHOR=PATH, for example "
            "author_001=data/hung_test_essay.txt"
        )

    author, raw_path = value.split("=", 1)
    author = author.strip()
    path = Path(raw_path.strip())

    if not author or not raw_path.strip():
        raise argparse.ArgumentTypeError(
            "Both AUTHOR and PATH are required."
        )

    return author, path


def discover_documents(
    profile_root: Path,
    test_root: Path,
    loose_author: str | None,
    extra_tests: list[tuple[str, Path]],
) -> tuple[list[Document], list[str]]:
    warnings: list[str] = []
    raw_documents: list[Document] = []

    author_names: set[str] = set()

    if profile_root.exists():
        author_names.update(
            folder.name
            for folder in profile_root.iterdir()
            if folder.is_dir() and folder.name.startswith("author_")
        )

    if test_root.exists():
        author_names.update(
            folder.name
            for folder in test_root.iterdir()
            if folder.is_dir() and folder.name.startswith("author_")
        )

    for author in sorted(author_names):
        for path in collect_txt_files(profile_root / author):
            raw_documents.append(
                Document(
                    author=author,
                    path=path,
                    source_split="profile_folder",
                    content_hash=normalised_text_hash(path),
                )
            )

        for path in collect_txt_files(test_root / author):
            raw_documents.append(
                Document(
                    author=author,
                    path=path,
                    source_split="test_folder",
                    content_hash=normalised_text_hash(path),
                )
            )

    loose_files = collect_txt_files(profile_root)

    if loose_files:
        if loose_author:
            warnings.append(
                f"Assigned {len(loose_files)} loose profile TXT files "
                f"to {loose_author}."
            )

            for path in loose_files:
                raw_documents.append(
                    Document(
                        author=loose_author,
                        path=path,
                        source_split="loose_profile_file",
                        content_hash=normalised_text_hash(path),
                    )
                )
        else:
            warnings.append(
                f"Ignored {len(loose_files)} loose TXT files directly "
                f"inside {profile_root}; use --loose-author."
            )

    for author, path in extra_tests:
        if not path.exists():
            raise FileNotFoundError(
                f"Extra test file not found: {path.resolve()}"
            )

        raw_documents.append(
            Document(
                author=author,
                path=path,
                source_split="extra_test",
                content_hash=normalised_text_hash(path),
            )
        )

    # Remove aliases/copies inside the same author's folders.
    unique_documents: list[Document] = []
    seen_within_author: dict[tuple[str, str], Document] = {}

    for document in raw_documents:
        key = (document.author, document.content_hash)

        if key in seen_within_author:
            existing = seen_within_author[key]
            warnings.append(
                "Skipped duplicate copy within "
                f"{document.author}: {document.path.name} duplicates "
                f"{existing.path.name}."
            )
            continue

        seen_within_author[key] = document
        unique_documents.append(document)

    return unique_documents, warnings


def cross_author_duplicates(
    documents: Iterable[Document],
) -> dict[str, list[Document]]:
    by_hash: dict[str, list[Document]] = {}

    for document in documents:
        by_hash.setdefault(document.content_hash, []).append(document)

    return {
        digest: items
        for digest, items in by_hash.items()
        if len({item.author for item in items}) > 1
    }


def build_feature_cache(
    documents: list[Document],
) -> dict[str, pd.Series]:
    cache: dict[str, pd.Series] = {}

    for number, document in enumerate(documents, start=1):
        text = document.path.read_text(
            encoding="utf-8",
            errors="replace",
        ).strip()

        if len(text) < 100:
            raise ValueError(
                f"Document is too short: {document.path.resolve()}"
            )

        features = extract_features(text)
        cache[document.document_id] = pd.Series(
            features,
            dtype=float,
        )

        print(
            f"[FEATURES {number:03d}/{len(documents):03d}] "
            f"{document.author} / {document.path.name}"
        )

    return cache


def profile_dataframe(
    documents: list[Document],
    feature_cache: dict[str, pd.Series],
) -> pd.DataFrame:
    rows: list[dict[str, float | str]] = []

    for document in documents:
        row: dict[str, float | str] = {
            "essay": document.document_id
        }
        row.update(feature_cache[document.document_id].to_dict())
        rows.append(row)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Evaluation metrics
# ---------------------------------------------------------------------------

def roc_auc_from_ranks(
    labels: np.ndarray,
    scores: np.ndarray,
) -> float:
    positives = labels == 1
    negatives = labels == 0

    n_positive = int(positives.sum())
    n_negative = int(negatives.sum())

    if n_positive == 0 or n_negative == 0:
        return float("nan")

    ranks = pd.Series(scores).rank(
        method="average",
        ascending=True,
    ).to_numpy()

    positive_rank_sum = float(ranks[positives].sum())

    auc = (
        positive_rank_sum
        - n_positive * (n_positive + 1) / 2
    ) / (n_positive * n_negative)

    return float(auc)


def threshold_statistics(
    labels: np.ndarray,
    scores: np.ndarray,
    threshold: float,
) -> dict[str, float | int]:
    predictions = (scores >= threshold).astype(int)

    tp = int(np.sum((predictions == 1) & (labels == 1)))
    tn = int(np.sum((predictions == 0) & (labels == 0)))
    fp = int(np.sum((predictions == 1) & (labels == 0)))
    fn = int(np.sum((predictions == 0) & (labels == 1)))

    tpr = tp / (tp + fn) if tp + fn else 0.0
    tnr = tn / (tn + fp) if tn + fp else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    fnr = fn / (fn + tp) if fn + tp else 0.0

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tpr
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )

    return {
        "threshold": float(threshold),
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "true_positive_rate": tpr,
        "true_negative_rate": tnr,
        "false_positive_rate": fpr,
        "false_negative_rate": fnr,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": (tp + tn) / len(labels),
        "balanced_accuracy": (tpr + tnr) / 2,
    }


def threshold_curve(
    labels: np.ndarray,
    scores: np.ndarray,
) -> pd.DataFrame:
    thresholds = sorted(
        set(float(score) for score in scores),
        reverse=True,
    )

    if thresholds:
        thresholds = [
            math.nextafter(thresholds[0], math.inf),
            *thresholds,
            math.nextafter(thresholds[-1], -math.inf),
        ]

    rows = [
        threshold_statistics(labels, scores, threshold)
        for threshold in thresholds
    ]

    return pd.DataFrame(rows)


def calculate_metrics(
    comparisons: pd.DataFrame,
) -> tuple[dict, pd.DataFrame]:
    labels = comparisons["same_author"].astype(int).to_numpy()
    scores = comparisons["similarity_score"].astype(float).to_numpy()

    curve = threshold_curve(labels, scores)

    if curve.empty:
        raise ValueError("No thresholds could be evaluated.")

    curve["eer_gap"] = (
        curve["false_positive_rate"]
        - curve["false_negative_rate"]
    ).abs()

    eer_row = curve.loc[curve["eer_gap"].idxmin()]
    best_row = curve.loc[curve["balanced_accuracy"].idxmax()]

    auc = roc_auc_from_ranks(labels, scores)

    metrics = {
        "authors_evaluated": int(
            comparisons["candidate_author"].nunique()
        ),
        "candidate_documents": int(
            comparisons["candidate_document_id"].nunique()
        ),
        "same_author_comparisons": int(
            comparisons["same_author"].sum()
        ),
        "different_author_comparisons": int(
            (comparisons["same_author"] == 0).sum()
        ),
        "roc_auc": auc,
        "equal_error_rate": float(
            (
                eer_row["false_positive_rate"]
                + eer_row["false_negative_rate"]
            )
            / 2
        ),
        "eer_similarity_threshold": float(eer_row["threshold"]),
        "best_balanced_accuracy": float(
            best_row["balanced_accuracy"]
        ),
        "best_similarity_threshold": float(best_row["threshold"]),
        "best_threshold_statistics": {
            key: (
                int(value)
                if key in {
                    "true_positive",
                    "true_negative",
                    "false_positive",
                    "false_negative",
                }
                else float(value)
            )
            for key, value in best_row.drop(
                labels=["eer_gap"],
                errors="ignore",
            ).to_dict().items()
        },
    }

    return metrics, curve


def build_rankings(
    comparisons: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict] = []

    for document_id, group in comparisons.groupby(
        "candidate_document_id"
    ):
        ordered = group.sort_values(
            ["similarity_score", "profile_author"],
            ascending=[False, True],
        ).reset_index(drop=True)

        same_rows = ordered.index[
            ordered["same_author"] == 1
        ].tolist()

        if not same_rows:
            continue

        true_rank = int(same_rows[0] + 1)
        top_row = ordered.iloc[0]
        candidate_row = ordered.iloc[same_rows[0]]

        rows.append(
            {
                "candidate_document_id": document_id,
                "candidate_author": candidate_row["candidate_author"],
                "candidate_file": candidate_row["candidate_file"],
                "true_author_rank": true_rank,
                "top_profile_author": top_row["profile_author"],
                "top_similarity_score": top_row["similarity_score"],
                "true_author_similarity_score": (
                    candidate_row["similarity_score"]
                ),
                "top1_correct": int(true_rank == 1),
                "reciprocal_rank": 1.0 / true_rank,
            }
        )

    return pd.DataFrame(rows)


def author_summary(
    comparisons: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict] = []

    for author in sorted(comparisons["candidate_author"].unique()):
        same = comparisons[
            (comparisons["candidate_author"] == author)
            & (comparisons["same_author"] == 1)
        ]["similarity_score"]

        different = comparisons[
            (comparisons["candidate_author"] == author)
            & (comparisons["same_author"] == 0)
        ]["similarity_score"]

        rows.append(
            {
                "author": author,
                "documents_tested": int(len(same)),
                "same_score_mean": float(same.mean()),
                "same_score_median": float(same.median()),
                "same_score_min": float(same.min()),
                "same_score_max": float(same.max()),
                "different_score_mean": float(different.mean()),
                "different_score_median": float(different.median()),
                "different_score_max": float(different.max()),
                "mean_separation": float(
                    same.mean() - different.mean()
                ),
            }
        )

    return pd.DataFrame(rows)


def pair_score_matrix(
    comparisons: pd.DataFrame,
) -> pd.DataFrame:
    matrix = comparisons.pivot_table(
        index="candidate_author",
        columns="profile_author",
        values="similarity_score",
        aggfunc="mean",
    )

    return matrix.sort_index().sort_index(axis=1)


def save_plots(
    comparisons: pd.DataFrame,
    threshold_curve_df: pd.DataFrame,
    pair_matrix: pd.DataFrame,
    output_dir: Path,
) -> list[str]:
    saved: list[str] = []

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print(
            "Warning: matplotlib is not installed; CSV and JSON "
            "reports were created without graphs."
        )
        return saved

    same_scores = comparisons.loc[
        comparisons["same_author"] == 1,
        "similarity_score",
    ]
    different_scores = comparisons.loc[
        comparisons["same_author"] == 0,
        "similarity_score",
    ]

    figure = plt.figure(figsize=(9, 6))
    plt.hist(
        [same_scores, different_scores],
        bins=15,
        label=["Same author", "Different author"],
        alpha=0.75,
    )
    plt.xlabel("Similarity score")
    plt.ylabel("Number of comparisons")
    plt.title("Same-author and different-author score distributions")
    plt.legend()
    plt.tight_layout()

    path = output_dir / "score_distributions.png"
    figure.savefig(path, dpi=180)
    plt.close(figure)
    saved.append(path.name)

    roc = threshold_curve_df.sort_values(
        "false_positive_rate"
    )

    figure = plt.figure(figsize=(7, 7))
    plt.plot(
        roc["false_positive_rate"],
        roc["true_positive_rate"],
        label="Viskopic",
    )
    plt.plot([0, 1], [0, 1], linestyle="--", label="Chance")
    plt.xlabel("False-positive rate")
    plt.ylabel("True-positive rate")
    plt.title("Authorship verification ROC curve")
    plt.legend()
    plt.tight_layout()

    path = output_dir / "roc_curve.png"
    figure.savefig(path, dpi=180)
    plt.close(figure)
    saved.append(path.name)

    figure = plt.figure(
        figsize=(
            max(7, len(pair_matrix.columns) * 1.2),
            max(6, len(pair_matrix.index) * 0.9),
        )
    )
    image = plt.imshow(
        pair_matrix.to_numpy(dtype=float),
        aspect="auto",
    )
    plt.colorbar(image, label="Mean similarity score")
    plt.xticks(
        range(len(pair_matrix.columns)),
        pair_matrix.columns,
        rotation=45,
        ha="right",
    )
    plt.yticks(
        range(len(pair_matrix.index)),
        pair_matrix.index,
    )
    plt.xlabel("Profile author")
    plt.ylabel("Candidate author")
    plt.title("Mean candidate-to-profile similarity")
    plt.tight_layout()

    path = output_dir / "author_pair_heatmap.png"
    figure.savefig(path, dpi=180)
    plt.close(figure)
    saved.append(path.name)

    return saved


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run multi-author leave-one-out authorship verification "
            "using the Viskopic Version 2 stylometric scorer."
        )
    )

    parser.add_argument(
        "--profile-root",
        default="data/human_essays",
        help="Folder containing author_00n profile folders.",
    )

    parser.add_argument(
        "--test-root",
        default="data/test_essays",
        help="Folder containing author_00n held-out test folders.",
    )

    parser.add_argument(
        "--output-dir",
        default="evaluation_results",
        help="Directory for all evaluation reports.",
    )

    parser.add_argument(
        "--minimum-profile-documents",
        type=int,
        default=5,
        help=(
            "Minimum number of documents in every reference profile. "
            "Leave-one-out therefore requires at least this value + 1 "
            "documents for a same-author test."
        ),
    )

    parser.add_argument(
        "--loose-author",
        default="author_001",
        help=(
            "Assign TXT files directly inside --profile-root to this "
            "author. Use an empty value to ignore loose files."
        ),
    )

    parser.add_argument(
        "--extra-test",
        action="append",
        default=[],
        type=parse_extra_test,
        metavar="AUTHOR=PATH",
        help=(
            "Add a test file outside --test-root. May be repeated. "
            "Example: --extra-test "
            "author_001=data/hung_test_essay.txt"
        ),
    )

    parser.add_argument(
        "--exclude-author",
        action="append",
        default=[],
        help=(
            "Author alias to exclude from this run. May be repeated, "
            "for example --exclude-author author_003."
        ),
    )

    parser.add_argument(
        "--allow-cross-author-duplicates",
        action="store_true",
        help=(
            "Continue even if identical text exists under two author "
            "labels. This is not recommended."
        ),
    )

    args = parser.parse_args()

    profile_root = Path(args.profile_root)
    test_root = Path(args.test_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    loose_author = args.loose_author.strip() or None

    documents, discovery_warnings = discover_documents(
        profile_root=profile_root,
        test_root=test_root,
        loose_author=loose_author,
        extra_tests=args.extra_test,
    )

    excluded_authors = set(args.exclude_author)
    if excluded_authors:
        documents = [
            document
            for document in documents
            if document.author not in excluded_authors
        ]
        print(
            "[INFO] Excluded authors: "
            + ", ".join(sorted(excluded_authors))
        )

    for warning in discovery_warnings:
        print(f"[WARNING] {warning}")

    if not documents:
        raise ValueError("No corpus TXT files were found.")

    duplicate_groups = cross_author_duplicates(documents)

    if duplicate_groups:
        duplicate_rows = []

        for digest, items in duplicate_groups.items():
            for item in items:
                duplicate_rows.append(
                    {
                        "content_hash": digest,
                        "author": item.author,
                        "file": str(item.path),
                    }
                )

        duplicate_report = output_dir / "cross_author_duplicates.csv"
        pd.DataFrame(duplicate_rows).to_csv(
            duplicate_report,
            index=False,
        )

        message = (
            "Identical text was found under different author labels. "
            f"Review {duplicate_report.resolve()}."
        )

        if not args.allow_cross_author_duplicates:
            raise ValueError(
                message
                + " Fix the labels or pass "
                "--allow-cross-author-duplicates to override."
            )

        print(f"[WARNING] {message}")

    by_author: dict[str, list[Document]] = {}

    for document in documents:
        by_author.setdefault(document.author, []).append(document)

    eligible_authors = {
        author: sorted(items, key=lambda item: item.document_id)
        for author, items in by_author.items()
        if len(items) >= args.minimum_profile_documents + 1
    }

    skipped_authors = {
        author: len(items)
        for author, items in by_author.items()
        if len(items) < args.minimum_profile_documents + 1
    }

    for author, count in sorted(skipped_authors.items()):
        print(
            f"[SKIP] {author}: {count} unique documents; "
            f"need at least "
            f"{args.minimum_profile_documents + 1}."
        )

    if len(eligible_authors) < 2:
        raise ValueError(
            "At least two eligible authors are required. "
            "Each needs enough unique documents for leave-one-out."
        )

    eligible_documents = [
        document
        for author_documents in eligible_authors.values()
        for document in author_documents
    ]

    print("\nCorpus selected")
    print(f"Authors: {len(eligible_authors)}")
    print(f"Unique documents: {len(eligible_documents)}")

    for author, items in sorted(eligible_authors.items()):
        print(f"  {author}: {len(items)} documents")

    feature_cache = build_feature_cache(eligible_documents)

    full_profiles = {
        author: profile_dataframe(items, feature_cache)
        for author, items in eligible_authors.items()
    }

    comparison_rows: list[dict] = []
    total_comparisons = (
        len(eligible_documents) * len(eligible_authors)
    )
    completed = 0

    for candidate_author, candidate_documents in sorted(
        eligible_authors.items()
    ):
        for candidate in candidate_documents:
            sample = feature_cache[candidate.document_id]

            for profile_author, profile_documents in sorted(
                eligible_authors.items()
            ):
                completed += 1

                if profile_author == candidate_author:
                    reference_documents = [
                        item
                        for item in profile_documents
                        if item.document_id != candidate.document_id
                    ]
                else:
                    reference_documents = profile_documents

                if (
                    len(reference_documents)
                    < args.minimum_profile_documents
                ):
                    print(
                        f"[SKIP COMPARISON] {candidate.document_id} "
                        f"against {profile_author}: only "
                        f"{len(reference_documents)} profile documents."
                    )
                    continue

                reference = profile_dataframe(
                    reference_documents,
                    feature_cache,
                )

                try:
                    result = score_against_profile(
                        reference,
                        sample,
                    )
                except Exception as exc:
                    print(
                        f"[ERROR] Candidate {candidate.path.name} "
                        f"against {profile_author}: {exc}"
                    )
                    continue

                row = {
                    "candidate_document_id": candidate.document_id,
                    "candidate_author": candidate_author,
                    "candidate_file": candidate.path.name,
                    "candidate_path": str(candidate.path),
                    "candidate_source_split": candidate.source_split,
                    "profile_author": profile_author,
                    "same_author": int(
                        profile_author == candidate_author
                    ),
                    "reference_document_count": len(
                        reference_documents
                    ),
                }
                row.update(result)
                comparison_rows.append(row)

                print(
                    f"[SCORE {completed:04d}/{total_comparisons:04d}] "
                    f"{candidate_author} / {candidate.path.name} "
                    f"vs {profile_author}: "
                    f"{result['similarity_score']:.1f}"
                )

    comparisons = pd.DataFrame(comparison_rows)

    if comparisons.empty:
        raise ValueError("No valid comparisons were produced.")

    expected_profiles = len(eligible_authors)
    complete_candidates = (
        comparisons.groupby("candidate_document_id")[
            "profile_author"
        ].nunique()
    )
    complete_candidate_ids = complete_candidates[
        complete_candidates == expected_profiles
    ].index

    incomplete_count = int(
        comparisons["candidate_document_id"].nunique()
        - len(complete_candidate_ids)
    )

    if incomplete_count:
        print(
            f"[WARNING] Excluding {incomplete_count} candidate "
            "documents from final metrics because not every author "
            "profile produced a score."
        )

        comparisons = comparisons[
            comparisons["candidate_document_id"].isin(
                complete_candidate_ids
            )
        ].copy()

    comparisons_path = output_dir / "all_comparisons.csv"
    comparisons.to_csv(comparisons_path, index=False)

    metrics, curve = calculate_metrics(comparisons)
    rankings = build_rankings(comparisons)

    if not rankings.empty:
        metrics["top1_author_identification_accuracy"] = float(
            rankings["top1_correct"].mean()
        )
        metrics["mean_reciprocal_rank"] = float(
            rankings["reciprocal_rank"].mean()
        )
        metrics["mean_true_author_rank"] = float(
            rankings["true_author_rank"].mean()
        )

    same_scores = comparisons.loc[
        comparisons["same_author"] == 1,
        "similarity_score",
    ]
    different_scores = comparisons.loc[
        comparisons["same_author"] == 0,
        "similarity_score",
    ]

    metrics["same_author_score_summary"] = {
        "mean": float(same_scores.mean()),
        "median": float(same_scores.median()),
        "minimum": float(same_scores.min()),
        "maximum": float(same_scores.max()),
        "standard_deviation": float(same_scores.std(ddof=1)),
    }

    metrics["different_author_score_summary"] = {
        "mean": float(different_scores.mean()),
        "median": float(different_scores.median()),
        "minimum": float(different_scores.min()),
        "maximum": float(different_scores.max()),
        "standard_deviation": float(
            different_scores.std(ddof=1)
        ),
    }

    metrics["eligible_author_document_counts"] = {
        author: len(items)
        for author, items in sorted(eligible_authors.items())
    }
    metrics["skipped_author_document_counts"] = skipped_authors
    metrics["important_note"] = (
        "Thresholds are estimated from this small experimental "
        "dataset and are not production decision thresholds."
    )

    curve.to_csv(
        output_dir / "threshold_curve.csv",
        index=False,
    )

    rankings.to_csv(
        output_dir / "document_rankings.csv",
        index=False,
    )

    summary = author_summary(comparisons)
    summary.to_csv(
        output_dir / "author_summary.csv",
        index=False,
    )

    pair_matrix = pair_score_matrix(comparisons)
    pair_matrix.to_csv(
        output_dir / "author_pair_mean_scores.csv"
    )

    plot_files = save_plots(
        comparisons,
        curve,
        pair_matrix,
        output_dir,
    )
    metrics["plot_files"] = plot_files

    metrics_path = output_dir / "metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, indent=2),
        encoding="utf-8",
    )

    print("\n" + "=" * 68)
    print("VISKOPIC MULTI-AUTHOR EVALUATION")
    print("=" * 68)
    print(f"Authors evaluated: {metrics['authors_evaluated']}")
    print(
        "Candidate documents: "
        f"{metrics['candidate_documents']}"
    )
    print(
        "Same-author comparisons: "
        f"{metrics['same_author_comparisons']}"
    )
    print(
        "Different-author comparisons: "
        f"{metrics['different_author_comparisons']}"
    )
    print(f"ROC-AUC: {metrics['roc_auc']:.3f}")
    print(
        "Equal-error rate: "
        f"{metrics['equal_error_rate']:.3f}"
    )
    print(
        "EER similarity threshold: "
        f"{metrics['eer_similarity_threshold']:.2f}"
    )
    print(
        "Best balanced accuracy: "
        f"{metrics['best_balanced_accuracy']:.3f}"
    )
    print(
        "Best similarity threshold: "
        f"{metrics['best_similarity_threshold']:.2f}"
    )

    if "top1_author_identification_accuracy" in metrics:
        print(
            "Top-1 author identification accuracy: "
            f"{metrics['top1_author_identification_accuracy']:.3f}"
        )
        print(
            "Mean true-author rank: "
            f"{metrics['mean_true_author_rank']:.2f}"
        )

    print("\nSame-author score")
    print(
        "  Mean / median: "
        f"{metrics['same_author_score_summary']['mean']:.1f} / "
        f"{metrics['same_author_score_summary']['median']:.1f}"
    )

    print("Different-author score")
    print(
        "  Mean / median: "
        f"{metrics['different_author_score_summary']['mean']:.1f} / "
        f"{metrics['different_author_score_summary']['median']:.1f}"
    )

    print("\nReports")
    print(f"  {comparisons_path.resolve()}")
    print(f"  {metrics_path.resolve()}")
    print(f"  {(output_dir / 'author_summary.csv').resolve()}")
    print(f"  {(output_dir / 'document_rankings.csv').resolve()}")
    print(f"  {(output_dir / 'author_pair_mean_scores.csv').resolve()}")

    for plot_file in plot_files:
        print(f"  {(output_dir / plot_file).resolve()}")

    print(
        "\nImportant: this evaluates separation in the current corpus. "
        "It does not prove authorship and must not be used as an "
        "automatic misconduct decision."
    )


if __name__ == "__main__":
    main()
