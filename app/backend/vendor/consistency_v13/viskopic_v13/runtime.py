from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from pathlib import Path

import numpy as np
import pandas as pd

from .frozen import score_candidate_text_v1_3 as live


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class V13Scorer:
    """The supplied live V1.3 scorer using exported numeric reference profiles.

    Supports exported profiles and versioned newly enrolled profiles using the
    full frozen bank. The host controls ownership, storage and worker loading.
    """

    def __init__(self, assets_dir=None):
        self.assets_dir = Path(assets_dir) if assets_dir else Path(__file__).parent / "assets"
        ledger_path = self.assets_dir / "asset_manifest.json"
        if not ledger_path.is_file():
            raise FileNotFoundError("Model assets are missing. Run export_v1_3_mvp.py in the original project first.")
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if ledger.get("schema") != "viskopic-v1.3-mvp-assets.v1":
            raise ValueError("Unexpected asset format")
        for name, expected in ledger["asset_hashes"].items():
            if Path(name).name != name or _hash(self.assets_dir / name) != expected:
                raise RuntimeError("Model asset integrity check failed")
        provenance = json.loads((Path(__file__).parent / "frozen/source_provenance.json").read_text())
        for name, entry in provenance.items():
            if _hash(Path(__file__).parent / "frozen" / name) != entry["packaged_sha256"]:
                raise RuntimeError("Packaged scoring source changed: " + name)
        self.v13 = json.loads((self.assets_dir / "calibration_v1_3_background_centering.json").read_text(encoding="utf-8"))
        self.v12 = live.cal.load_config(self.assets_dir / "calibration_v1_2.json")
        data = json.loads((self.assets_dir / "profiles.json").read_text(encoding="utf-8"))
        self._profiles = data["profiles"]
        self.threshold = float(self.v13["operating_threshold"]["frozen_threshold"])
        if _hash(self.assets_dir / "calibration_v1_2.json") != self.v13["source_files"]["base_calibration_sha256"]:
            raise RuntimeError("V1.2 configuration differs from frozen V1.3 lineage")
        if data["development_comparisons_sha256"] != self.v13["source_files"]["development_comparisons_sha256"]:
            raise RuntimeError("Background summary differs from frozen comparison lineage")
        self._frames = {author: pd.DataFrame(**row["feature_frame"]) for author, row in self._profiles.items()}
        self._profile_lock = threading.RLock()
        self._background_banks = {}

    def enroll_profile(self, profile_id, reference_documents, *, background_dir=None, background_author_id=None):
        from .enrollment import enroll_profile
        return enroll_profile(self, profile_id, reference_documents,
                              background_dir=background_dir, background_author_id=background_author_id)

    def load_profile_artifact(self, path, *, expected_sha256, background_dir=None):
        from .enrollment import load_profile_artifact
        return load_profile_artifact(self, path, expected_sha256, background_dir=background_dir)

    def list_profiles(self):
        with self._profile_lock:
            return [{"profile_id": author, "reference_papers": row["profile_papers"],
                     "maturity": live.cal.maturity_label(row["profile_papers"])}
                    for author, row in sorted(self._profiles.items())]

    def score_text(self, candidate_text: str, profile_id: str, *, include_evidence: bool = False):
        if not isinstance(include_evidence, bool):
            raise ValueError("include_evidence must be a boolean")
        if not isinstance(profile_id, str):
            raise ValueError("profile_id must be a string")
        with self._profile_lock:
            if profile_id not in self._profiles:
                raise KeyError(f"Unknown profile {profile_id!r}. Use list_profiles() or enroll and load the intended profile first.")
            profile_row = self._profiles[profile_id]
            profile = self._frames[profile_id].copy()
        if not isinstance(candidate_text, str) or not candidate_text.strip():
            raise ValueError("candidate_text must be a non-empty string")
        if profile_row.get("reference_documents"):
            from .enrollment import usable_text_hash
            if usable_text_hash(candidate_text) in {r["usable_text_sha256"] for r in profile_row["reference_documents"]}:
                raise ValueError("Candidate duplicates an enrolled reference; use a separate held-out document")
        with tempfile.TemporaryDirectory(prefix="viskopic-v13-") as tmp:
            path = Path(tmp) / "candidate.txt"
            path.write_text(candidate_text, encoding="utf-8")
            manifest, words = live.build_candidate_windows(path, Path(tmp) / "windows")
            candidate, _ = live.mw.extract_window_features(manifest)
            columns = live.mw.numeric_feature_columns(pd.concat([profile, candidate], ignore_index=True))
            raw, window_rows, usable = live.mw.score_document_raw(profile, candidate, columns)
            distances = live.mw.reference_document_distances(profile, columns)
            if include_evidence:
                from .diagnostics import build_diagnostics
                sampled = {int(row["window_id"]): Path(row["window_path"]).read_text(encoding="utf-8").strip()
                           for _, row in manifest.iterrows()}
                evidence = build_diagnostics(candidate_text, profile, candidate, columns, window_rows, sampled)
        if not distances:
            raise RuntimeError("No genuine profile calibration distances could be computed")
        local_t = float(np.median(np.asarray(distances, dtype=float)))
        count = profile_row["profile_papers"]
        v12 = live.cal.score_with_shrinkage(raw_distance=raw, local_t=local_t,
                                          reference_document_count=count,
                                          global_t=self.v12.global_t, k=self.v12.k)
        center = profile_row["background_median"]
        score = float(v12["similarity_score"] - center)
        result = {"model_version": "1.3", "model": self.v13["version"], "profile_id": profile_id,
                "profile_papers": count, "profile_maturity": live.cal.maturity_label(count),
                "candidate_usable_words": words, "candidate_windows": int(len(candidate)), "usable_features": int(usable),
                "raw_distance": float(raw), "local_typical_genuine_distance": local_t,
                "shrunk_typical_genuine_distance": float(v12["shrunk_typical_genuine_distance"]),
                "v1_2_similarity": float(v12["similarity_score"]),
                "background_median_v1_2_similarity": center,
                "background_documents": profile_row["background_documents"],
                "background_authors": profile_row["background_authors"],
                "v1_3_centered_similarity": score, "frozen_threshold": self.threshold,
                "decision": "same-author-compatible" if score >= self.threshold else "stylistically-unusual",
                "interpretation_warning": self.v13["interpretation_warning"]}
        if profile_row.get("artifact_sha256"):
            result.update(profile_origin="enrolled", profile_artifact_sha256=profile_row["artifact_sha256"],
                          background_features_sha256=profile_row["background_features_sha256"])
        if include_evidence:
            result["evidence"] = evidence
        return result
