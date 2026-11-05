"""Research only: how the V1.3 consistency model behaves with FEWER than 8 baseline essays.

The frozen V1.3 model and the website both require 8 reference essays. This script asks what happens with 4, 5,
6 or 7 instead, on the BAWE test sets written by bawe_prepare.py (8 baselines + 1 submission per student):

  for each student and each k, several random choices of k of their 8 baselines ->
      a profile built the way the model builds one (same features, same leave-one-out typical distance with the
      model's own shrinkage for small profiles, same background centring), then
      the student's own submission ("genuine") and every other student's submission ("impostor") are scored
      against it with the frozen threshold.

It reports, per k: how many genuine essays are wrongly labelled "stylistically unusual" (false flags of honest
students, the error that matters most), how many impostors are wrongly labelled "compatible", and the AUC.

Nothing in the vendored model is edited. The script reuses the model's own functions; to avoid recomputing the
same features thousands of times it extracts each essay's features once and repeats the model's scoring steps
(runtime.V13Scorer.score_text and enrollment.FrozenBackgroundBank.summarize) on the cached features. At k=8 it
checks that it reproduces scores.csv from bawe_evaluate.py. This is NOT a validation of a smaller minimum: the
threshold was calibrated on 8+ essay profiles, and 21 students is a small sample. Every number is experimental.

Usage (local Python 3.10+ with numpy and pandas, from the repo root; nothing leaves the computer):
    python app/tools/bawe_k_sweep.py D:/data/bawe_sets --ks 4 5 6 --draws 4
Writes k_sweep_scores.csv and k_sweep_summary.txt into the sets folder (numbers only, no essay text).
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import itertools
import pickle
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parents[1]
VENDOR_DIR = next((p for p in (_HERE / "backend" / "vendor" / "consistency_v13", _HERE / "vendor" / "consistency_v13")
                   if p.is_dir()), _HERE / "backend" / "vendor" / "consistency_v13")


def load():
    sys.path.insert(0, str(VENDOR_DIR))
    import numpy as np
    import pandas as pd
    from viskopic_v13 import V13Scorer           # verifies every frozen source and asset hash
    from viskopic_v13 import enrollment
    from viskopic_v13.frozen import score_candidate_text_v1_3 as live
    with contextlib.redirect_stdout(io.StringIO()):
        scorer = V13Scorer()
        bank = enrollment.get_bank(scorer)
    return np, pd, scorer, bank, live, enrollment


def features(live, text: str):
    """The model's own window sampling and feature extraction (as in score_text / enroll_profile)."""
    with tempfile.TemporaryDirectory(prefix="k-sweep-") as tmp:
        path = Path(tmp) / "doc.txt"
        path.write_text(text, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            manifest, words = live.build_candidate_windows(path, Path(tmp) / "windows")
            frame, _ = live.mw.extract_window_features(manifest)
    return frame, words


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sets_dir", type=Path)
    ap.add_argument("--ks", type=int, nargs="+", default=[4, 5, 6])
    ap.add_argument("--draws", type=int, default=4, help="random baseline subsets per student and k")
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--cache", type=Path, default=None, help="feature cache (numbers only); default: temp dir")
    ap.add_argument("--check", action="store_true", help="also run k=8 and compare with scores.csv")
    ap.add_argument("--max-seconds", type=float, default=0, help="stop after this long and save progress")
    args = ap.parse_args()

    np, pd, scorer, bank, live, enrollment = load()
    folders = sorted(p for p in args.sets_dir.glob("student_*") if p.is_dir())
    students = {}
    for f in folders:
        b, s = sorted(f.glob("baseline_*.txt")), sorted(f.glob("submission_*.txt"))
        if len(b) >= 8 and s:
            students[f.name.removeprefix("student_")] = (b[:8], s[0])

    cache_path = args.cache or Path(tempfile.gettempdir()) / "viskopic_k_sweep_features.pkl"
    cache = pickle.loads(cache_path.read_bytes()) if cache_path.exists() else {}
    started = time.time()
    for sid, (bases, sub) in students.items():
        for p in [*bases, sub]:
            if args.max_seconds and time.time() - started > args.max_seconds:
                cache_path.write_bytes(pickle.dumps(cache))
                print(f"stopping for now: features for {len(cache)} essays cached; run again to continue", flush=True)
                return 2
            if p.name not in cache:
                frame, words = features(live, p.read_text(encoding="utf-8"))
                cache[p.name] = (frame, words)
        cache_path.write_bytes(pickle.dumps(cache))
    print(f"features ready for {len(students)} students ({time.time() - started:.0f}s)", flush=True)

    def reference(paths):
        frames = []
        for p in paths:
            f = cache[p.name][0].copy()
            f["paper_id"] = p.stem
            frames.append(f)
        return pd.concat(frames, ignore_index=True)

    def centre(ref):
        """Background median of V1.2 similarity for this profile (FrozenBackgroundBank.summarize)."""
        with contextlib.redirect_stdout(io.StringIO()):
            return bank.summarize(scorer, ref, None)["background_median"]

    def score(ref, n, center, cand):
        """runtime.V13Scorer.score_text on cached features."""
        columns = live.mw.numeric_feature_columns(pd.concat([ref, cand], ignore_index=True))
        raw, _, _ = live.mw.score_document_raw(ref, cand, columns)
        local_t = float(np.median(np.asarray(live.mw.reference_document_distances(ref, columns), dtype=float)))
        v12 = live.cal.score_with_shrinkage(raw_distance=raw, local_t=local_t, reference_document_count=n,
                                           global_t=scorer.v12.global_t, k=scorer.v12.k)
        return float(v12["similarity_score"] - center)

    rng = random.Random(args.seed)
    plan = []
    for k in sorted(set(args.ks + ([8] if args.check else []))):
        for sid, (bases, _) in students.items():
            subsets = [tuple(range(8))] if k == 8 else list(itertools.combinations(range(8), k))
            for d, combo in enumerate(rng.sample(subsets, min(args.draws, len(subsets))) if k < 8 else subsets):
                plan.append((k, sid, d, combo))

    out_csv = args.sets_dir / "k_sweep_scores.csv"
    done = set()
    rows = []
    if out_csv.exists():
        with open(out_csv, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        done = {(int(r["k"]), r["profile_student"], int(r["draw"])) for r in rows}
    fields = ["k", "profile_student", "draw", "baselines_used", "candidate_student", "kind", "centred_score",
              "threshold", "decision"]
    thr = scorer.threshold
    for i, (k, sid, d, combo) in enumerate(plan, 1):
        if (k, sid, d) in done:
            continue
        if args.max_seconds and time.time() - started > args.max_seconds:
            print(f"stopping for now at {i - 1}/{len(plan)} profiles; run again to continue", flush=True)
            break
        bases = students[sid][0]
        ref = reference([bases[j] for j in combo])
        c = centre(ref)
        for cid, (_, sub) in students.items():
            s = score(ref, k, c, cache[sub.name][0])
            rows.append({"k": k, "profile_student": sid, "draw": d, "baselines_used": " ".join(bases[j].stem[-5:] for j in combo),
                         "candidate_student": cid, "kind": "genuine" if cid == sid else "impostor",
                         "centred_score": round(s, 3), "threshold": round(thr, 3),
                         "decision": "same-author-compatible" if s >= thr else "stylistically-unusual"})
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f"[{i}/{len(plan)}] k={k} student {sid} draw {d} ({time.time() - started:.0f}s)", flush=True)

    # ---- summary
    def auc(g, im):
        return sum((x > y) + 0.5 * (x == y) for x in g for y in im) / (len(g) * len(im)) if g and im else float("nan")

    lines = ["V1.3 consistency on BAWE with fewer baseline essays (research only; threshold calibrated for 8+)",
             f"Students: {len(students)}, frozen threshold {thr:.3f}, random subsets per student and k: {args.draws}",
             "",
             f"{'k':>2}  {'profiles':>8}  {'honest flagged':>16}  {'impostor passed':>16}  {'AUC':>5}  "
             f"{'median own score':>16}"]
    for k in sorted({int(r["k"]) for r in rows}):
        rk = [r for r in rows if int(r["k"]) == k]
        g = [float(r["centred_score"]) for r in rk if r["kind"] == "genuine"]
        im = [float(r["centred_score"]) for r in rk if r["kind"] == "impostor"]
        gf = sum(x < thr for x in g)
        ip = sum(x >= thr for x in im)
        lines.append(f"{k:>2}  {len(g):>8}  {gf:>5}/{len(g):<4} ({100 * gf / len(g):3.0f}%)  "
                     f"{ip:>5}/{len(im):<5}({100 * ip / len(im):3.0f}%)  {auc(g, im):.3f}  {statistics.median(g):>16.2f}")
    if args.check and (args.sets_dir / "scores.csv").exists():
        with open(args.sets_dir / "scores.csv", encoding="utf-8") as fh:
            ref_rows = {(r["profile_student"], r["candidate_student"]): float(r["centred_score"])
                        for r in csv.DictReader(fh) if r.get("centred_score")}
        diffs = [abs(float(r["centred_score"]) - ref_rows[(r["profile_student"], r["candidate_student"])])
                 for r in rows if int(r["k"]) == 8 and (r["profile_student"], r["candidate_student"]) in ref_rows]
        lines += ["", f"Check against bawe_evaluate.py at k=8: {len(diffs)} comparisons, largest difference "
                      f"{max(diffs) if diffs else float('nan'):.4f}"]
    lines += ["", "honest flagged  = a student's own submission labelled 'stylistically unusual' (want low)",
              "impostor passed = another student's submission labelled 'compatible' (want low)",
              "The model itself labels profiles under 6 essays 'insufficient' and 6-7 'low confidence'."]
    text = "\n".join(lines)
    (args.sets_dir / "k_sweep_summary.txt").write_text(text + "\n", encoding="utf-8")
    print("\n" + text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
