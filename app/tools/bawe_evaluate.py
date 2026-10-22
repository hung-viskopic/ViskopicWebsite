"""Batch-test the V1.3 consistency model on BAWE students, without the website.

Uses the test sets written by bawe_prepare.py (one folder per student with 8 baseline essays and a
submission). For every student it:
  1. builds the student's profile from their 8 baseline essays (same model code the website vendors),
  2. scores the student's own submission                       -> a "genuine" comparison,
  3. scores every OTHER student's submission against the profile -> "impostor" comparisons.

Then it reports how often each kind lands above the frozen threshold (labelled "style compatible"):
  - genuine accepted   = same writer correctly labelled compatible     (want high)
  - impostor accepted  = different writer wrongly labelled compatible  (want low; false-match rate)
and how well the score separates the two (AUC: 1.0 perfect, 0.5 no better than chance).

Scoring goes through the model team's website adapter in its explicit unreviewed/local mode, exactly
like the website with VISKOPIC_ALLOW_UNREVIEWED_CONSISTENCY=true. Every number is experimental.

Usage, easiest: inside the worker container, which already has the model, numpy and pandas
(PowerShell, from the app folder; the stack doesn't need to be running):
    docker compose run --rm --no-deps -v D:\\data\\bawe_sets:/sets -v ${PWD}\\tools:/app/tools worker python /app/tools/bawe_evaluate.py /sets --limit 3

Or with a local Python 3.10+ that has numpy and pandas (from the repo root):
    python app\\tools\\bawe_evaluate.py D:\\data\\bawe_sets
    python app\\tools\\bawe_evaluate.py D:\\data\\bawe_sets --limit 5      # quick trial on 5 students

Writes scores.csv (one row per comparison) and summary.txt into the sets folder. Neither contains any
essay text. Each profile and each comparison runs the full model: expect roughly 15-30 minutes for 21
students (try --limit 3 first).
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import statistics
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parents[1]
# app/backend/vendor in the repo; /app/vendor inside the worker container (tools mounted at /app/tools).
VENDOR_DIR = next((p for p in (_HERE / "backend" / "vendor" / "consistency_v13", _HERE / "vendor" / "consistency_v13")
                   if p.is_dir()), _HERE / "backend" / "vendor" / "consistency_v13")
ADAPTER_SHA256 = "96342d69bba75f2ef4d417d30aa6cee216e18f694df391908527085535612598"  # as in consistency.py


def load_model():
    digest = hashlib.sha256((VENDOR_DIR / "website_adapter.py").read_bytes()).hexdigest()
    if digest != ADAPTER_SHA256:
        sys.exit(f"vendored website_adapter.py has SHA-256 {digest}, expected {ADAPTER_SHA256}")
    sys.path.insert(0, str(VENDOR_DIR))
    from viskopic_v13 import V13Scorer, write_profile_artifact  # verifies every model asset hash
    import website_adapter

    with contextlib.redirect_stdout(io.StringIO()):
        scorer = V13Scorer()
    return scorer, website_adapter, write_profile_artifact


def read_sets(sets_dir: Path) -> dict[str, dict]:
    students = {}
    for folder in sorted(sets_dir.glob("student_*")):
        baselines = sorted(folder.glob("baseline_*.txt"))
        subs = sorted(folder.glob("submission_*.txt"))       # excludes impostor_submission_*
        if len(baselines) >= 8 and subs:
            students[folder.name.removeprefix("student_")] = {"baselines": baselines, "submission": subs[0]}
    return students


def auc(genuine: list[float], impostor: list[float]) -> float | None:
    """Probability a genuine score beats an impostor score (ties count half)."""
    if not genuine or not impostor:
        return None
    wins = sum((g > i) + 0.5 * (g == i) for g in genuine for i in impostor)
    return wins / (len(genuine) * len(impostor))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sets_dir", type=Path, help="output folder of bawe_prepare.py")
    ap.add_argument("--limit", type=int, default=0, help="only the first N students (0 = all)")
    args = ap.parse_args()

    students = read_sets(args.sets_dir)
    if args.limit:
        students = dict(list(students.items())[: args.limit])
    if len(students) < 2:
        print(f"Need at least 2 student folders in {args.sets_dir}; run bawe_prepare.py first.", file=sys.stderr)
        return 1

    print(f"Loading the V1.3 model (verifying asset hashes) ...")
    scorer, adapter, write_profile_artifact = load_model()
    texts = {sid: s["submission"].read_text(encoding="utf-8") for sid, s in students.items()}

    rows: list[dict] = []
    started = time.time()
    for n, (sid, s) in enumerate(students.items(), 1):
        print(f"[{n}/{len(students)}] student {sid}: building profile ...", flush=True)
        profile_id = f"bawe-eval-{sid}"
        refs = [{"paper_id": p.stem, "text": p.read_text(encoding="utf-8")} for p in s["baselines"]]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                artifact = scorer.enroll_profile(profile_id=profile_id, reference_documents=refs)
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "profile.json"
                    sha = write_profile_artifact(path, artifact)
                    scorer.load_profile_artifact(path, expected_sha256=sha)
        except ValueError as e:
            print(f"    skipped: profile not usable by the model ({e})")
            rows.append({"profile_student": sid, "candidate_student": "", "kind": "profile_failed",
                         "status": str(e)})
            continue

        for cid, text in texts.items():
            kind = "genuine" if cid == sid else "impostor"
            with contextlib.redirect_stdout(io.StringIO()):
                res = adapter.score_for_website(scorer, text, profile_id, permitted_profile_ids={profile_id},
                                                allow_unreviewed_demo=True)
            row = {"profile_student": sid, "candidate_student": cid, "kind": kind, "status": res["status"]}
            m = res["measurements"]
            if m:
                row.update(centred_score=round(m["v1_3_centered_similarity"], 3),
                           threshold=round(m["frozen_threshold"], 3),
                           margin=round(m["margin_to_threshold"], 3),
                           raw_distance=round(m["raw_distance"], 4),
                           relative_distance=round(m["relative_distance"], 4),
                           decision=res["model_decision"])
            rows.append(row)
        own = next((r for r in rows if r["profile_student"] == sid and r["kind"] == "genuine"), {})
        print(f"    own submission: {own.get('decision', own.get('status'))} "
              f"(score {own.get('centred_score', '-')}); elapsed {time.time() - started:.0f}s", flush=True)

    fields = ["profile_student", "candidate_student", "kind", "status", "centred_score", "threshold", "margin",
              "raw_distance", "relative_distance", "decision"]
    with open(args.sets_dir / "scores.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    scored = [r for r in rows if "centred_score" in r]
    gen = [r for r in scored if r["kind"] == "genuine"]
    imp = [r for r in scored if r["kind"] == "impostor"]
    not_scored = [r for r in rows if r["kind"] in ("genuine", "impostor") and "centred_score" not in r]
    failed = [r for r in rows if r["kind"] == "profile_failed"]

    def rate(rs):
        return f"{sum(r['decision'] == 'same-author-compatible' for r in rs)}/{len(rs)}" + (
            f" ({100 * sum(r['decision'] == 'same-author-compatible' for r in rs) / len(rs):.0f}%)" if rs else "")

    per_student = []
    for sid in students:
        g = [r["centred_score"] for r in gen if r["profile_student"] == sid]
        i = [r["centred_score"] for r in imp if r["profile_student"] == sid]
        a = auc(g, i)
        if a is not None:
            beaten = sum(x >= g[0] for x in i)
            per_student.append(f"  {sid}: own {g[0]:7.2f} | impostors median {statistics.median(i):7.2f}, "
                               f"max {max(i):7.2f} | {beaten} of {len(i)} impostors score at or above own")

    gs, is_ = [r["centred_score"] for r in gen], [r["centred_score"] for r in imp]
    lines = [
        "V1.3 consistency on BAWE (experimental, unreviewed texts)",
        f"Students: {len(students)} ({len(failed)} profiles failed), comparisons scored: {len(scored)}, "
        f"not scored: {len(not_scored)}",
        f"Frozen threshold: {scored[0]['threshold'] if scored else '-'}",
        "",
        f"Genuine accepted (same writer -> compatible):     {rate(gen)}",
        f"Impostor accepted (different writer -> compatible): {rate(imp)}   <- false-match rate",
        f"AUC, all comparisons pooled:  {auc(gs, is_):.3f}" if gs and is_ else "AUC: -",
        "",
        "Per student (centred scores; higher = more similar):",
        *per_student,
    ]
    if not_scored:
        lines += ["", "Not scored: " + ", ".join(sorted({r['status'] for r in not_scored}))]
    text = "\n".join(lines)
    (args.sets_dir / "summary.txt").write_text(text + "\n", encoding="utf-8")
    print("\n" + text)
    print(f"\nWrote {args.sets_dir / 'scores.csv'} and {args.sets_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
