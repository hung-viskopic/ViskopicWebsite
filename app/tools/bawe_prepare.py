"""Turn the BAWE corpus into ready-to-upload test sets for the Viskopic website.

BAWE (British Academic Written English) is ~2,800 assessed student assignments in TEI XML,
from the Oxford Text Archive (resource 2539). Files are named <student><letter>.xml, e.g.
0001a.xml and 0001b.xml are two assignments by student 0001.

What this does:
  1. Reads every .xml file in the BAWE folder and extracts the essay prose only (headings,
     paragraphs and list items from the body; drops tables, figures, formulas, notes and the
     bibliography).
  2. Groups assignments by student and counts words.
  3. For each student with enough long assignments, writes a folder of .txt files:
         baseline_01_<id>.txt ... baseline_NN_<id>.txt   upload with role "baseline"
         submission_<id>.txt                              upload with role "submission"
         impostor_submission_<id>.txt                     another student's essay, a negative test
  4. Writes summary.csv describing every student, eligible or not.

Usage (Windows PowerShell, from the repo root):
    python app\\tools\\bawe_prepare.py D:\\data\\BAWE\\CORPUS_ByDisc  D:\\data\\bawe_sets

The output contains real (anonymised) student writing: keep it outside the repo and only use it
on the local development stack. Check the BAWE licence before any non-research use.
"""
from __future__ import annotations

import argparse
import csv
import random
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

# V1.3 needs 8 baseline documents and a submission, each with >=1,650 usable words after its own
# preprocessing (it drops very short sentences, so raw counts run a little higher). The default
# minimum leaves a margin; the website reports the exact usable count either way.
DEFAULT_MIN_WORDS = 1800
DEFAULT_BASELINE = 8

DROP = {"table", "figure", "formula", "note", "listBibl", "bibl", "graphic", "figDesc", "quote"}
KEEP_BLOCKS = {"head", "p", "item"}
FILE_ID = re.compile(r"^(\d+)([a-zA-Z]+)$")


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def block_text(el: ET.Element) -> str:
    parts = [el.text or ""]
    for child in el:
        if local(child.tag) not in DROP:
            parts.append(block_text(child))
        parts.append(child.tail or "")
    return " ".join("".join(parts).split())


def extract(path: Path) -> str:
    """Essay prose from a BAWE TEI file, one block per line."""
    raw = path.read_bytes()
    raw = re.sub(rb"<!DOCTYPE[^>]*>", b"", raw, count=1)          # BAWE files reference an external DTD
    raw = re.sub(rb"&(?!amp;|lt;|gt;|quot;|apos;|#)\w+;", b" ", raw)  # entities declared only in that DTD
    root = ET.fromstring(raw)
    body = next((e for e in root.iter() if local(e.tag) == "body"), None)
    if body is None:
        return ""
    blocks: list[str] = []

    def walk(el: ET.Element) -> None:
        name = local(el.tag)
        if name in DROP:
            return
        if name in KEEP_BLOCKS:
            t = block_text(el)
            if t:
                blocks.append(t)
            return
        for child in el:
            walk(child)

    walk(body)
    return "\n\n".join(blocks)


def words(text: str) -> int:
    return len(re.findall(r"[A-Za-z][A-Za-z'-]*", text))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bawe_dir", type=Path, help="folder containing the BAWE .xml files (searched recursively)")
    ap.add_argument("out_dir", type=Path, help="where to write the test sets")
    ap.add_argument("--min-words", type=int, default=DEFAULT_MIN_WORDS)
    ap.add_argument("--baseline", type=int, default=DEFAULT_BASELINE, help="baseline essays per student")
    ap.add_argument("--seed", type=int, default=7, help="for picking impostor essays")
    args = ap.parse_args()

    files = sorted(args.bawe_dir.rglob("*.xml"))
    if not files:
        print(f"No .xml files under {args.bawe_dir}", file=sys.stderr)
        return 1

    by_student: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    skipped: list[Path] = []
    seen: dict[str, Path] = {}
    duplicates = 0
    for f in files:
        m = FILE_ID.match(f.stem)
        if not m:
            skipped.append(f)
            continue
        if f.stem in seen:              # the same assignment in another copy of the corpus
            duplicates += 1
            continue
        seen[f.stem] = f
        try:
            text = extract(f)
        except ET.ParseError as e:
            print(f"  skip {f.name}: {e}", file=sys.stderr)
            continue
        by_student[m.group(1)].append((f.stem, text, words(text)))
    if skipped:
        print(f"Ignored {len(skipped)} files whose names don't look like <student><letter>.xml, e.g. "
              + ", ".join(str(p) for p in skipped[:3]))
    if duplicates:
        folders = sorted({str(p.parent) for p in seen.values()})
        print(f"Skipped {duplicates} duplicate copies of the same assignment; used the copies in: "
              + ", ".join(folders[:3]) + (" ..." if len(folders) > 3 else ""))

    need = args.baseline + 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stale = [d for d in args.out_dir.glob("student_*") if d.is_dir()]
    if stale:
        print(f"{args.out_dir} already has {len(stale)} student folders from an earlier run: "
              "delete them first so old and new sets don't mix.", file=sys.stderr)
        return 1
    rows, eligible = [], []
    for sid, docs in sorted(by_student.items()):
        docs.sort(key=lambda d: d[0])
        long_docs = [d for d in docs if d[2] >= args.min_words]
        ok = len(long_docs) >= need
        rows.append({"student": sid, "assignments": len(docs), "long_assignments": len(long_docs),
                     "words": " ".join(str(d[2]) for d in docs), "eligible": ok})
        if ok:
            eligible.append((sid, long_docs))

    with open(args.out_dir / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    pool = [(sid, d) for sid, docs in by_student.items() for d in docs if d[2] >= args.min_words]
    rng = random.Random(args.seed)
    for sid, long_docs in eligible:
        folder = args.out_dir / f"student_{sid}"
        folder.mkdir(exist_ok=True)
        *base, sub = long_docs[: need]          # the last eligible assignment is the submission
        for i, (doc_id, text, _) in enumerate(base, 1):
            (folder / f"baseline_{i:02d}_{doc_id}.txt").write_text(text, encoding="utf-8")
        (folder / f"submission_{sub[0]}.txt").write_text(sub[1], encoding="utf-8")
        others = [d for s, d in pool if s != sid]
        if others:
            imp = rng.choice(others)
            (folder / f"impostor_submission_{imp[0]}.txt").write_text(imp[1], encoding="utf-8")

    counts = sorted((r["long_assignments"] for r in rows), reverse=True)
    print(f"{len(files)} files, {len(by_student)} students.")
    print(f"Most long (>= {args.min_words} words) assignments by one student: {counts[:10]}")
    print(f"Students with {need}+ long assignments: {len(eligible)}"
          + (f" -> {', '.join(s for s, _ in eligible)}" if eligible else ""))
    print(f"Wrote {args.out_dir / 'summary.csv'}")
    if not eligible:
        print("No student meets the V1.3 requirement. See summary.csv for the closest ones.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
