from __future__ import annotations

import argparse
import csv
import math
import re
from collections import Counter
from pathlib import Path


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
ORCID_RE = re.compile(r"\b(?:https?://orcid\.org/)?\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b", re.I)
REF_HEADING_RE = re.compile(r"^\s*(references|bibliography|works cited|literature cited)\s*$", re.I)
SECTION_HEADING_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\s+)?(introduction|background|methods?|methodology|results?|discussion|"
    r"conclusions?|references|bibliography|acknowledg(?:e)?ments?|appendix|supplementary materials?)\s*$",
    re.I,
)
ROADMAP_RE = re.compile(r"\bsection\s+\d+\b", re.I)
LIST_RE = re.compile(r"(?:^|\s)(?:\(?\d{1,2}[.)]|[a-z][.)])\s+", re.I)
MATH_RE = re.compile(r"[=<>≤≥∑∫√∞→←↔∶±×÷∈∉⊂⊆∪∩∀∃¬∧∨λμσπδθφψω]")
VAR_EXPR_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*\s*(?:\([^)]{1,40}\))?\s*(?:=|∶=|→)\s*")
INLINE_CITE_RE = re.compile(r"\([A-Z][A-Za-z'’\-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z'’\-]+)?(?:\s+et\s+al\.)?,?\s+\d{4}[a-z]?\)")
BRACKET_CITE_RE = re.compile(r"\[(?:\d{1,3}(?:\s*[,;\-]\s*\d{1,3})*)\]")

AFFILIATION_HINTS = (
    "university", "institute", "department", "faculty", "school of", "college",
    "centre for", "center for", "corresponding author", "correspondence", "orcid",
)


def words(text: str) -> int:
    return len(re.findall(r"\b[\w’'\-]+\b", text, re.UNICODE))


def sentences(text: str) -> int:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“‘(])", text)
    return sum(words(p) >= 4 for p in parts)


def extract_pdf(path: Path):
    try:
        import fitz
        doc = fitz.open(path)
        pages = [page.get_text("text") or "" for page in doc]
        doc.close()
        return pages, "pymupdf"
    except Exception as e1:
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages]
            return pages, "pypdf"
        except Exception as e2:
            raise RuntimeError(f"PyMuPDF failed: {e1}; pypdf failed: {e2}")


def normalize_line(line: str) -> str:
    line = line.replace("\u00ad", "").replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"[ \t]+", " ", line).strip()


def repeated_margins(pages: list[str]) -> set[str]:
    counts = Counter()
    for page in pages:
        seen = set()
        for raw in page.splitlines():
            line = normalize_line(raw)
            if 2 <= len(line.split()) <= 14 and len(line) <= 120:
                seen.add(line.lower())
        counts.update(seen)
    threshold = max(3, math.ceil(len(pages) * 0.25))
    return {line for line, count in counts.items() if count >= threshold}


def page_to_paragraphs(page: str, repeated: set[str]) -> list[str]:
    # PDF line-wrap repair: unaccept-\nable -> unacceptable
    page = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", page)
    result, current = [], []

    def flush():
        nonlocal current
        if current:
            text = re.sub(r"\s+", " ", " ".join(current)).strip()
            if text:
                result.append(text)
            current = []

    for raw in page.splitlines():
        line = normalize_line(raw)
        if not line:
            flush()
            continue
        if line.lower() in repeated or re.fullmatch(r"(?:page\s+)?\d+", line, re.I):
            continue
        if SECTION_HEADING_RE.match(line) or (len(line.split()) <= 12 and line.isupper()):
            flush()
            result.append(line)
            continue
        current.append(line)
        if len(" ".join(current).split()) >= 70 and re.search(r'[.!?][\"\')\]]?$', line):
            flush()
    flush()
    return result


def clean_text(text: str) -> str:
    text = EMAIL_RE.sub(" ", text)
    text = URL_RE.sub(" ", text)
    text = ORCID_RE.sub(" ", text)
    text = re.sub(r"\bFootnote\s+\d+\s+continued\b", " ", text, flags=re.I)
    text = re.sub(r"(?<=[.!?;:])\d{1,2}(?=\s+[A-Z])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def is_reference_like(text: str) -> bool:
    """
    Conservative bibliography detector.

    Normal academic body prose may contain several year citations, so
    multiple years alone are NOT enough to reject a paragraph.
    """
    wc = words(text)

    if wc < 5:
        return False

    # Long prose paragraphs are almost never individual reference entries.
    if wc > 140:
        return False

    year_hits = len(re.findall(r"\b(?:19|20)\d{2}[a-z]?\b", text))
    doi_hits = len(re.findall(r"\b10\.\d{4,9}/\S+", text))
    url_hits = len(URL_RE.findall(text))

    journal_hits = len(
        re.findall(
            r"\b(vol\.?|volume|issue|pp\.?|pages?|journal|press|publisher|"
            r"proceedings|eds?\.?)\b",
            text,
            re.I,
        )
    )

    author_year_start = bool(
        re.match(
            r"^\s*[A-Z][A-Za-z'’\-]+"
            r"(?:,\s*[A-Z][A-Za-z'’\-]*\.?|\s+[A-Z]\.?)?"
            r".{0,45}\(?\b(?:19|20)\d{2}[a-z]?\b\)?",
            text,
        )
    )

    punctuation_hits = sum(
        text.count(ch)
        for ch in [",", ";", ":", "(", ")"]
    )
    punct_dense = wc <= 100 and punctuation_hits >= 8

    if doi_hits >= 1 and wc <= 120:
        return True

    if author_year_start and (
        journal_hits >= 1
        or url_hits >= 1
        or punct_dense
    ):
        return True

    if (
        year_hits >= 2
        and journal_hits >= 2
        and wc <= 100
    ):
        return True

    return False


def reject_reason(text: str, in_references: bool) -> str | None:
    t = text.strip()
    low = t.lower()
    wc = words(t)

    if in_references:
        return "references_section"
    if REF_HEADING_RE.match(t):
        return "reference_heading"
    if re.fullmatch(r"abstract", t, re.I):
        return "abstract_heading"
    if re.match(r"^\s*(keywords?|key words)\s*[:\-]", t, re.I):
        return "keywords"
    if SECTION_HEADING_RE.match(t):
        return "section_heading"
    if EMAIL_RE.search(t) or ORCID_RE.search(t):
        return "contact_or_affiliation"
    if sum(h in low for h in AFFILIATION_HINTS) >= 2 and wc < 100:
        return "contact_or_affiliation"
    if is_reference_like(t):
        return "reference_like"
    if wc < 35:
        return "too_short"
    if len(ROADMAP_RE.findall(t)) >= 3:
        return "section_roadmap"
    if len(LIST_RE.findall(t)) >= 4:
        return "list_heavy"
    math_symbols = len(MATH_RE.findall(t)) + 2 * len(VAR_EXPR_RE.findall(t))
    if math_symbols >= 5 or math_symbols / max(len(t), 1) >= 0.0025:
        return "math_heavy"
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.32 and wc < 120:
        return "uppercase_or_table_like"
    citation_density = 100 * (len(INLINE_CITE_RE.findall(t)) + len(BRACKET_CITE_RE.findall(t))) / max(wc, 1)
    if citation_density >= 2.2:
        return "citation_heavy"

    cleaned = clean_text(t)
    if words(cleaned) < 35:
        return "too_short_after_cleaning"
    if sentences(cleaned) < 2 and words(cleaned) < 90:
        return "not_continuous_prose"
    return None


def split_thirds(paragraphs: list[str]):
    counts = [words(p) for p in paragraphs]
    total = sum(counts)
    regions = [[], [], []]
    cumulative = 0
    for p, count in zip(paragraphs, counts):
        frac = (cumulative + count / 2) / max(total, 1)
        idx = 0 if frac < 1/3 else 1 if frac < 2/3 else 2
        regions[idx].append(p)
        cumulative += count
    return regions


def central_chunk(paragraphs: list[str], target: int) -> list[str]:
    if not paragraphs:
        return []
    counts = [words(p) for p in paragraphs]
    if sum(counts) <= target:
        return list(paragraphs)

    midpoint = sum(counts) / 2
    c = 0
    centre = 0
    for i, n in enumerate(counts):
        c += n
        if c >= midpoint:
            centre = i
            break

    chosen = [centre]
    nwords = counts[centre]
    left, right = centre - 1, centre + 1
    while nwords < target and (left >= 0 or right < len(paragraphs)):
        candidates = []
        if left >= 0:
            candidates.append(left)
        if right < len(paragraphs):
            candidates.append(right)
        idx = min(candidates, key=lambda i: (abs(target - (nwords + counts[i])), i))
        chosen.append(idx)
        nwords += counts[idx]
        if idx == left:
            left -= 1
        else:
            right += 1
    return [paragraphs[i] for i in sorted(chosen)]


def trim_sample_to_target(text: str, target: int, tolerance: float = 0.05) -> str:
    """
    Keep document samples close to the requested length.

    For target=1500, tolerance=0.05 gives a desired band of roughly
    1425-1575 words. Prefer ending at a sentence boundary.
    """
    current = words(text)
    upper = int(target * (1 + tolerance))
    lower = int(target * (1 - tolerance))

    if current <= upper:
        return text.strip()

    sentence_parts = re.split(
        r'(?<=[.!?])\s+(?=[A-Z0-9"“‘(])',
        text.strip(),
    )

    chosen = []
    count = 0

    for sentence in sentence_parts:
        sw = words(sentence)
        if sw == 0:
            continue

        if count >= lower and count + sw > upper:
            break

        chosen.append(sentence)
        count += sw

        if count >= target:
            break

    result = " ".join(chosen).strip()

    # Fallback when PDF sentence segmentation is poor.
    if words(result) < lower:
        tokens = text.split()
        result = " ".join(tokens[:target]).strip()

    return result


def balanced_sample(eligible: list[str], target: int):
    regions = split_thirds(eligible)
    per = max(target // 3, 1)
    pieces = [central_chunk(region, per) for region in regions]
    chosen = [p for piece in pieces for p in piece]

    # Top up if a third is sparse.
    selected = set(chosen)
    if sum(words(p) for p in chosen) < target:
        for p in eligible:
            if p not in selected:
                chosen.append(p)
                selected.add(p)
                if sum(words(x) for x in chosen) >= target:
                    break

    order = {}
    for i, p in enumerate(eligible):
        order.setdefault(p, i)
    chosen = sorted(dict.fromkeys(chosen), key=lambda p: order.get(p, 10**9))
    sample = "\n\n".join(chosen).strip()
    sample = trim_sample_to_target(sample, target=target, tolerance=0.05)
    return sample, {
        "sample_words": words(sample),
        "sample_paragraphs": len(chosen),
        "early_words": sum(words(p) for p in pieces[0]),
        "middle_words": sum(words(p) for p in pieces[1]),
        "late_words": sum(words(p) for p in pieces[2]),
    }


def process_pdf(pdf: Path, out: Path, target: int):
    pages, backend = extract_pdf(pdf)
    repeated = repeated_margins(pages)
    paragraphs = []
    for page in pages:
        paragraphs.extend(page_to_paragraphs(page, repeated))

    eligible, drops = [], Counter()
    in_refs = False
    for p in paragraphs:
        if REF_HEADING_RE.match(p.strip()):
            in_refs = True
            drops["reference_heading"] += 1
            continue
        reason = reject_reason(p, in_refs)
        if reason:
            drops[reason] += 1
        else:
            eligible.append(clean_text(p))

    sample, meta = balanced_sample(eligible, target)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sample, encoding="utf-8")

    raw_words = sum(words(p) for p in pages)
    clean_words = sum(words(p) for p in eligible)
    status = "ok" if meta["sample_words"] >= min(900, int(target * 0.65)) else "too_little_clean_prose"

    return {
        "pdf_file": pdf.name,
        "pdf_path": pdf.as_posix(),
        "output_txt": out.as_posix(),
        "extractor": backend,
        "pages": len(pages),
        "raw_words": raw_words,
        "raw_paragraphs": len(paragraphs),
        "kept_paragraphs": len(eligible),
        "clean_words_available": clean_words,
        **meta,
        "repeated_header_footer_lines": len(repeated),
        "status": status,
        "drop_reasons": ";".join(f"{k}={v}" for k, v in drops.most_common()),
    }


def main():
    ap = argparse.ArgumentParser(description="Build clean representative prose samples from author PDFs.")
    ap.add_argument("--author-folder", required=True)
    ap.add_argument("--author-alias", required=True)
    ap.add_argument("--output-root", default="data/clean_samples_v2_1")
    ap.add_argument("--manifest-root", default="data/clean_sample_manifests_v2_1")
    ap.add_argument("--target-words", type=int, default=1500)
    ap.add_argument("--clear", action="store_true")
    args = ap.parse_args()

    source = Path(args.author_folder)
    pdfs = sorted(source.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"No PDFs found in {source}")

    outdir = Path(args.output_root) / args.author_alias
    outdir.mkdir(parents=True, exist_ok=True)
    if args.clear:
        for p in outdir.glob("*.txt"):
            p.unlink()

    print("=" * 78)
    print("VISKOPIC CLEAN PROSE SAMPLER V2.1")
    print("=" * 78)
    print(f"Author:       {args.author_alias}")
    print(f"PDFs:         {len(pdfs)}")
    print(f"Target words: {args.target_words}")
    print(f"Output:       {outdir}")
    print()

    rows = []
    for i, pdf in enumerate(pdfs, 1):
        out = outdir / f"{pdf.stem}_CLEAN_V2_1.txt"
        print(f"[{i:02d}/{len(pdfs):02d}] {pdf.name[:100]}")
        try:
            row = process_pdf(pdf, out, args.target_words)
            rows.append(row)
            print(
                f"    raw={row['raw_words']} clean={row['clean_words_available']} "
                f"sample={row['sample_words']} status={row['status']}"
            )
        except Exception as exc:
            rows.append({
                "pdf_file": pdf.name,
                "pdf_path": pdf.as_posix(),
                "output_txt": out.as_posix(),
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })
            print(f"    ERROR: {type(exc).__name__}: {exc}")

    manifest = Path(args.manifest_root) / f"{args.author_alias}_clean_v2_1.csv"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "pdf_file", "pdf_path", "output_txt", "extractor", "pages", "raw_words",
        "raw_paragraphs", "kept_paragraphs", "clean_words_available", "sample_words",
        "sample_paragraphs", "early_words", "middle_words", "late_words",
        "repeated_header_footer_lines", "status", "drop_reasons", "error",
    ]
    with manifest.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print()
    print("=" * 78)
    print("CLEAN SAMPLING COMPLETE")
    print("=" * 78)
    print("OK:", sum(r.get("status") == "ok" for r in rows))
    print("Too little clean prose:", sum(r.get("status") == "too_little_clean_prose" for r in rows))
    print("Errors:", sum(r.get("status") == "error" for r in rows))
    print("Manifest:", manifest.resolve())
    print("Original PDFs and existing TXT samples were not modified.")


if __name__ == "__main__":
    main()
