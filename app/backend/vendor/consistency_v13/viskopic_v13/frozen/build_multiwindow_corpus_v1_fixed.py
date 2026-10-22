from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path

from . import clean_and_sample_text_v2_1 as cleaner


def wc(text: str) -> int:
    return cleaner.words(text)


def sentence_units(paragraphs: list[str]) -> list[str]:
    units: list[str] = []

    for paragraph in paragraphs:
        parts = re.split(
            r'(?<=[.!?])\s+(?=[A-Z0-9"“‘(])',
            paragraph.strip(),
        )

        for part in parts:
            part = part.strip()
            if wc(part) >= 4:
                units.append(part)

    return units


def cumulative_word_positions(
    units: list[str],
) -> tuple[list[int], int]:
    cumulative: list[int] = []
    total = 0

    for unit in units:
        total += wc(unit)
        cumulative.append(total)

    return cumulative, total


def make_centered_window(
    units: list[str],
    cumulative: list[int],
    total_words: int,
    center_fraction: float,
    target_words: int,
) -> str:
    """
    Create one contiguous, sentence-aligned window centered near a
    specified relative position in the cleaned document.
    """
    if not units:
        return ""

    target_pos = center_fraction * total_words

    center_idx = 0
    for i, cumulative_words in enumerate(cumulative):
        if cumulative_words >= target_pos:
            center_idx = i
            break

    selected = [center_idx]
    current_words = wc(units[center_idx])

    left = center_idx - 1
    right = center_idx + 1

    while current_words < target_words and (
        left >= 0 or right < len(units)
    ):
        candidates: list[tuple[int, int]] = []

        if left >= 0:
            candidates.append((left, wc(units[left])))

        if right < len(units):
            candidates.append((right, wc(units[right])))

        best_idx, best_wc = min(
            candidates,
            key=lambda item: (
                abs(target_words - (current_words + item[1])),
                abs(item[0] - center_idx),
            ),
        )

        selected.append(best_idx)
        current_words += best_wc

        if best_idx == left:
            left -= 1
        else:
            right += 1

    selected.sort()

    text = " ".join(
        units[i]
        for i in selected
    ).strip()

    return cleaner.trim_sample_to_target(
        text,
        target=target_words,
        tolerance=0.08,
    )


def clean_full_pdf(
    pdf_path: Path,
) -> tuple[list[str], dict]:
    """
    Reuse the exact public cleaning primitives from
    clean_and_sample_text_v2_1.py.

    Important:
    - extract_pdf -> page text
    - repeated_margins -> repeated header/footer detection
    - page_to_paragraphs -> line-wrap repair / paragraph reconstruction
    - reject_reason -> conservative paragraph filtering
    - clean_text -> light inline cleanup
    """
    pages, backend = cleaner.extract_pdf(pdf_path)

    repeated = cleaner.repeated_margins(pages)

    paragraphs: list[str] = []

    for page in pages:
        paragraphs.extend(
            cleaner.page_to_paragraphs(
                page,
                repeated,
            )
        )

    eligible: list[str] = []
    drops = Counter()
    in_references = False

    for paragraph in paragraphs:
        if cleaner.REF_HEADING_RE.match(
            paragraph.strip()
        ):
            in_references = True
            drops["reference_heading"] += 1
            continue

        reason = cleaner.reject_reason(
            paragraph,
            in_references,
        )

        if reason:
            drops[reason] += 1
            continue

        cleaned = cleaner.clean_text(
            paragraph
        ).strip()

        if wc(cleaned) >= 4:
            eligible.append(cleaned)

    meta = {
        "extractor": backend,
        "pages": len(pages),
        "raw_words": sum(
            wc(page)
            for page in pages
        ),
        "raw_paragraphs": len(paragraphs),
        "clean_words": sum(
            wc(paragraph)
            for paragraph in eligible
        ),
        "kept_paragraphs": len(eligible),
        "repeated_header_footer_lines": len(repeated),
        "drop_reasons": ";".join(
            f"{key}={value}"
            for key, value in drops.most_common()
        ),
    }

    return eligible, meta


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build multiple clean stylometric windows per paper. "
            "Uses clean_and_sample_text_v2_1 cleaning primitives, "
            "but preserves multiple locations instead of producing "
            "one representative sample."
        )
    )

    parser.add_argument(
        "--author-folder",
        required=True,
    )
    parser.add_argument(
        "--author-alias",
        required=True,
    )
    parser.add_argument(
        "--windows",
        type=int,
        default=5,
    )
    parser.add_argument(
        "--window-words",
        type=int,
        default=600,
    )
    parser.add_argument(
        "--output-root",
        default="data/multiwindow_v1",
    )
    parser.add_argument(
        "--manifest-root",
        default="data/multiwindow_manifests_v1",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
    )

    args = parser.parse_args()

    if args.windows < 2:
        raise ValueError(
            "--windows must be at least 2"
        )

    if args.window_words < 300:
        raise ValueError(
            "--window-words should be at least 300"
        )

    source = Path(args.author_folder)

    if not source.exists():
        raise FileNotFoundError(source)

    pdfs = sorted(
        source.glob("*.pdf")
    )

    if not pdfs:
        raise RuntimeError(
            f"No PDFs found in {source}"
        )

    out_dir = (
        Path(args.output_root)
        / args.author_alias
    )

    out_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if args.clear:
        for txt in out_dir.glob("*.txt"):
            txt.unlink()

    rows: list[dict] = []

    print("=" * 78)
    print(
        "VISKOPIC MULTI-WINDOW CORPUS BUILDER V1 — FIXED"
    )
    print("=" * 78)
    print(f"Author:       {args.author_alias}")
    print(f"Papers:       {len(pdfs)}")
    print(
        f"Windows:      {args.windows} per paper"
    )
    print(
        f"Window words: {args.window_words}"
    )
    print(f"Output:       {out_dir}")
    print()

    for paper_index, pdf in enumerate(
        pdfs,
        start=1,
    ):
        print(
            f"[{paper_index:02d}/{len(pdfs):02d}] "
            f"{pdf.name[:100]}"
        )

        try:
            paragraphs, meta = clean_full_pdf(
                pdf
            )

            units = sentence_units(
                paragraphs
            )

            (
                cumulative,
                total_words,
            ) = cumulative_word_positions(
                units
            )

            minimum_required = max(
                800,
                args.window_words,
            )

            if total_words < minimum_required:
                print(
                    "    SKIP: only "
                    f"{total_words} usable clean words"
                )

                rows.append(
                    {
                        "author": args.author_alias,
                        "paper_id": pdf.stem,
                        "pdf_file": pdf.name,
                        "status": "too_little_clean_text",
                        **meta,
                    }
                )
                continue

            fractions = [
                (i + 0.5) / args.windows
                for i in range(args.windows)
            ]

            window_word_counts: list[int] = []

            for window_idx, fraction in enumerate(
                fractions,
                start=1,
            ):
                text = make_centered_window(
                    units=units,
                    cumulative=cumulative,
                    total_words=total_words,
                    center_fraction=fraction,
                    target_words=args.window_words,
                )

                words_in_window = wc(text)

                if words_in_window < max(
                    300,
                    int(args.window_words * 0.65),
                ):
                    continue

                out_name = (
                    f"{pdf.stem}"
                    f"__WINDOW_{window_idx:02d}.txt"
                )

                out_path = (
                    out_dir
                    / out_name
                )

                out_path.write_text(
                    text,
                    encoding="utf-8",
                )

                window_word_counts.append(
                    words_in_window
                )

                rows.append(
                    {
                        "author": args.author_alias,
                        "paper_id": pdf.stem,
                        "pdf_file": pdf.name,
                        "window_id": window_idx,
                        "center_fraction": fraction,
                        "window_file": out_name,
                        "window_path": out_path.as_posix(),
                        "window_words": words_in_window,
                        "status": "ok",
                        **meta,
                    }
                )

            print(
                f"    clean={meta['clean_words']} "
                f"windows={len(window_word_counts)} "
                f"window_words={window_word_counts}"
            )

        except Exception as exc:
            print(
                "    ERROR: "
                f"{type(exc).__name__}: {exc}"
            )

            rows.append(
                {
                    "author": args.author_alias,
                    "paper_id": pdf.stem,
                    "pdf_file": pdf.name,
                    "status": "error",
                    "error": (
                        f"{type(exc).__name__}: {exc}"
                    ),
                }
            )

    manifest_path = (
        Path(args.manifest_root)
        / (
            f"{args.author_alias}"
            "_multiwindow_v1.csv"
        )
    )

    manifest_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "author",
        "paper_id",
        "pdf_file",
        "window_id",
        "center_fraction",
        "window_file",
        "window_path",
        "window_words",
        "status",
        "extractor",
        "pages",
        "raw_words",
        "raw_paragraphs",
        "clean_words",
        "kept_paragraphs",
        "repeated_header_footer_lines",
        "drop_reasons",
        "error",
    ]

    with manifest_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    ok_rows = [
        row
        for row in rows
        if row.get("status") == "ok"
    ]

    paper_counts: dict[str, int] = {}

    for row in ok_rows:
        paper_id = row["paper_id"]
        paper_counts[paper_id] = (
            paper_counts.get(
                paper_id,
                0,
            )
            + 1
        )

    print()
    print("=" * 78)
    print(
        "MULTI-WINDOW BUILD COMPLETE"
    )
    print("=" * 78)
    print(
        "Papers with windows: "
        f"{len(paper_counts)}"
    )
    print(
        "Total windows:       "
        f"{len(ok_rows)}"
    )
    print(
        "Windows per paper:   "
        f"{sorted(set(paper_counts.values()))}"
    )
    print(
        f"Manifest: {manifest_path.resolve()}"
    )


if __name__ == "__main__":
    main()
