"""New-profile preparation with the full frozen bank and unchanged V1.3 rules.

The host owns authorization and persistence. Reference text is temporary; the
returned profile stores numeric features and provenance, never the raw essays.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from .frozen import score_candidate_text_v1_3 as live

PROFILE_SCHEMA = "viskopic-v1.3-enrolled-profile.v1"
META_COLUMNS = {"paper_id", "window_id", "window_path"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")


def usable_text_hash(text):
    normalized = " ".join(live.builder.sentence_units(live.normalise_plain_text(text)))
    return digest(normalized.encode("utf-8"))


def identity_keys(value):
    value = str(value).casefold()
    return {value, *re.findall(r"w\d{8,}", value)}


def feature_fingerprint(frame):
    ordered = frame.sort_values("window_id")
    columns = sorted(set(ordered.columns) - META_COLUMNS)
    data = [[float(value) if value != 0 else 0.0 for value in row]
            for row in ordered[columns].to_numpy(dtype=float)]
    return digest(canonical_bytes({"columns": columns, "data": data}))


def validate_frame(frame, expected_columns):
    if frame.columns.duplicated().any() or set(frame.columns) != set(expected_columns):
        raise ValueError("Profile feature columns differ from the frozen representation")
    numeric = [c for c in expected_columns if c not in META_COLUMNS]
    if not all(pd.api.types.is_numeric_dtype(frame[c]) for c in numeric):
        raise ValueError("Feature values must be numeric")
    if not np.isfinite(frame[numeric].to_numpy(dtype=float)).all():
        raise ValueError("Feature values must be finite")
    if frame.empty or frame[list(META_COLUMNS)].isna().any().any():
        raise ValueError("Profile metadata is missing")
    for paper, windows in frame.groupby("paper_id"):
        if not isinstance(paper, str) or not paper or len(windows) != 3 or set(windows.window_id) != {1, 2, 3}:
            raise ValueError("Each reference paper requires exactly windows 1, 2, 3")


class FrozenBackgroundBank:
    def __init__(self, model, directory=None):
        self.directory = Path(directory).resolve() if directory else Path(__file__).parent / "background"
        root = self.directory
        ledger = json.loads((root / "background_manifest.json").read_bytes())
        raw_registry = (root / "background_registry.csv").read_bytes()
        raw_features = (root / "background_features.json").read_bytes()
        spec = model.v13["background_bank"]
        if ledger.get("schema") != "viskopic-v1.3-background-manifest.v1":
            raise ValueError("Unexpected background manifest format")
        if digest(raw_registry) != spec["registry_sha256"] or ledger.get("registry_sha256") != spec["registry_sha256"]:
            raise RuntimeError("Background registry integrity check failed")
        if digest(raw_features) != ledger.get("background_features_sha256"):
            raise RuntimeError("Background feature integrity check failed")
        if ledger.get("v13_config_sha256") != digest((model.assets_dir / "calibration_v1_3_background_centering.json").read_bytes()):
            raise RuntimeError("Background configuration lineage differs")
        provenance = json.loads((Path(live.__file__).parent / "source_provenance.json").read_bytes())
        if ledger.get("frozen_feature_sources") != {n: row["packaged_sha256"] for n, row in provenance.items()}:
            raise RuntimeError("Background feature-source lineage differs")
        registry = list(csv.DictReader(io.StringIO(raw_registry.decode("utf-8-sig"))))
        identities = [(r["background_author"], r["background_paper"]) for r in registry]
        self.authors = {a for a, _ in identities}
        if len(identities) != spec["documents"] or len(set(identities)) != len(identities) or len(self.authors) != spec["authors"]:
            raise RuntimeError("Background membership/counts differ from frozen V1.3")
        if ledger.get("authors") != spec["authors"] or ledger.get("documents") != spec["documents"] or ledger.get("windows") != 3 * spec["documents"]:
            raise RuntimeError("Background manifest counts differ")
        payload = json.loads(raw_features)
        if payload.get("schema") != "viskopic-v1.3-background-features.v1" or payload.get("registry_sha256") != spec["registry_sha256"]:
            raise RuntimeError("Background feature registry lineage differs")
        if payload.get("development_comparisons_sha256") != model.v13["source_files"]["development_comparisons_sha256"]:
            raise RuntimeError("Background comparison lineage differs")
        documents = payload["documents"]
        actual = [(d["author"], d["paper_id"]) for d in documents]
        if len(actual) != len(identities) or set(actual) != set(identities):
            raise RuntimeError("Background features do not have exact frozen membership")
        self.features_sha256 = digest(raw_features)
        self.registry_sha256 = digest(raw_registry)
        self.columns = list(next(iter(model._frames.values())).columns)
        self.documents, self.identity_authors, self.fingerprint_authors = [], {}, {}
        for doc in documents:
            frame = pd.DataFrame(**doc["feature_frame"])
            validate_frame(frame, self.columns)
            if set(frame.paper_id) != {doc["paper_id"]}:
                raise RuntimeError("Background feature metadata differs from its document")
            self.documents.append((doc["author"], doc["paper_id"], frame))
            for key in identity_keys(doc["paper_id"]):
                self.identity_authors.setdefault(key, set()).add(doc["author"])
            self.fingerprint_authors.setdefault(feature_fingerprint(frame), set()).add(doc["author"])

    def check_excluded_author(self, author):
        if author is not None and (not isinstance(author, str) or author not in self.authors):
            raise ValueError("background_author_id must be a real frozen-bank author or None")

    def check_reference_overlap(self, paper_id, frame, excluded_author):
        matches = set(self.fingerprint_authors.get(feature_fingerprint(frame), set()))
        for key in identity_keys(paper_id):
            matches.update(self.identity_authors.get(key, set()))
        if matches - ({excluded_author} if excluded_author else set()):
            raise ValueError("Reference overlaps the frozen background bank; verify identity and explicitly exclude the target bank author if appropriate")

    def summarize(self, model, reference, excluded_author=None):
        self.check_excluded_author(excluded_author)
        columns = live.mw.numeric_feature_columns(reference)
        genuine = live.mw.reference_document_distances(reference, columns)
        if len(genuine) != reference.paper_id.nunique():
            raise ValueError("Every reference paper must have a valid leave-one-paper-out distance")
        local_t = float(np.median(genuine))
        n = int(reference.paper_id.nunique())
        # Validate the typical distance before the full background calculation.
        shrunk = live.cal.shrunk_typical_distance(local_t, n, model.v12.global_t, model.v12.k)
        scores, authors = [], set()
        for author, _, candidate in self.documents:
            if author == excluded_author:
                continue
            raw, _, _ = live.mw.score_document_raw(reference, candidate, columns)
            score = live.cal.score_with_shrinkage(raw, local_t, n, model.v12.global_t, model.v12.k)
            scores.append(float(score["similarity_score"]))
            authors.add(author)
        if len(scores) < 100:
            raise ValueError("At least 100 background documents are required")
        return {"background_median": float(np.median(scores)), "background_documents": len(scores),
                "background_authors": len(authors), "local_typical_genuine_distance": local_t,
                "shrunk_typical_genuine_distance": shrunk}


def get_bank(model, directory=None):
    key = str(Path(directory).resolve()) if directory else "bundled"
    with model._profile_lock:
        if key not in model._background_banks:
            model._background_banks[key] = FrozenBackgroundBank(model, directory)
        return model._background_banks[key]


def source_lineage(model):
    result = {name: digest((model.assets_dir / name).read_bytes()) for name in
              ("asset_manifest.json", "calibration_v1_2.json", "calibration_v1_3_background_centering.json")}
    # Source text normalization keeps this adapter-version check portable across
    # Windows/Linux checkouts while the original frozen assets remain byte-exact.
    result["enrollment_implementation_sha256"] = digest(Path(__file__).read_text(encoding="utf-8").encode("utf-8"))
    return result


def validate_profile_id(profile_id):
    if not isinstance(profile_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", profile_id):
        raise ValueError("profile_id must be an opaque 1-128 character ID using letters, digits, '.', '_', ':' or '-'")


def enroll_profile(model, profile_id, reference_documents, *, background_dir=None, background_author_id=None):
    validate_profile_id(profile_id)
    with model._profile_lock:
        if profile_id in model._profiles:
            raise ValueError("Profile ID already exists; use a new immutable profile/version ID")
    if not isinstance(reference_documents, (list, tuple)) or len(reference_documents) < live.MIN_PROFILE_PAPERS:
        raise ValueError(f"Frozen V1.3 enrollment requires at least {live.MIN_PROFILE_PAPERS} distinct reference papers")
    prepared, identities, hashes = [], set(), set()
    for row in reference_documents:
        if not isinstance(row, dict) or not isinstance(row.get("paper_id"), str) or not row["paper_id"].strip():
            raise ValueError("Each reference requires a non-empty paper_id")
        paper = row["paper_id"].strip()
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Each reference requires non-empty plain text")
        keys = identity_keys(paper)
        text_hash = usable_text_hash(text)
        if keys & identities or text_hash in hashes:
            raise ValueError("Duplicate reference identity or normalized text")
        identities.update(keys)
        hashes.add(text_hash)
        prepared.append((paper, text, text_hash))
    bank = get_bank(model, background_dir)
    bank.check_excluded_author(background_author_id)
    frames, metadata = [], []
    with tempfile.TemporaryDirectory(prefix="viskopic-v13-enroll-") as tmp:
        for index, (paper, text, text_hash) in enumerate(prepared):
            path = Path(tmp) / f"reference_{index}.txt"
            path.write_text(text, encoding="utf-8")
            manifest, words = live.build_candidate_windows(path, Path(tmp) / f"windows_{index}")
            manifest["paper_id"] = paper
            frame, _ = live.mw.extract_window_features(manifest)
            frame["window_path"] = [f"profile:{profile_id}/{index}/{wid}" for wid in frame.window_id]
            validate_frame(frame, bank.columns)
            bank.check_reference_overlap(paper, frame, background_author_id)
            frames.append(frame)
            metadata.append({"paper_id": paper, "usable_words": words,
                             "source_text_sha256": digest(text.encode("utf-8")),
                             "usable_text_sha256": text_hash, "feature_sha256": feature_fingerprint(frame)})
    reference = pd.concat(frames, ignore_index=True)
    summary = bank.summarize(model, reference, background_author_id)
    return {"schema": PROFILE_SCHEMA, "model_version": "1.3", "profile_id": profile_id,
            "profile_papers": len(metadata), "reference_documents": metadata,
            "feature_frame": reference.to_dict(orient="split"), **summary,
            "background_features_sha256": bank.features_sha256, "background_registry_sha256": bank.registry_sha256,
            "background_author_excluded": background_author_id, "source_lineage": source_lineage(model),
            "requirements": {"minimum_reference_papers": live.MIN_PROFILE_PAPERS,
                             "minimum_usable_words_per_reference": live.MIN_CLEAN_WORDS,
                             "candidate_minimum_usable_words": live.MIN_CLEAN_WORDS,
                             "reference_sampling": "frozen plain-text three-window sampler"}}


def write_profile_artifact(path, artifact):
    """Write once; store the returned digest in the host's trusted database."""
    path = Path(path)
    data = canonical_bytes(artifact)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
    return digest(data)


def load_profile_artifact(model, path, expected_sha256, *, background_dir=None):
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("Provide the expected artifact SHA-256 from trusted host storage")
    data = Path(path).read_bytes()
    if digest(data) != expected_sha256:
        raise RuntimeError("Enrolled profile artifact integrity check failed")
    artifact = json.loads(data)
    if artifact.get("schema") != PROFILE_SCHEMA or artifact.get("model_version") != "1.3":
        raise ValueError("Unexpected enrolled-profile format")
    profile_id = artifact.get("profile_id")
    validate_profile_id(profile_id)
    if artifact.get("source_lineage") != source_lineage(model):
        raise RuntimeError("Enrolled profile model/calibration lineage differs")
    required = {"minimum_reference_papers": live.MIN_PROFILE_PAPERS,
                "minimum_usable_words_per_reference": live.MIN_CLEAN_WORDS,
                "candidate_minimum_usable_words": live.MIN_CLEAN_WORDS,
                "reference_sampling": "frozen plain-text three-window sampler"}
    if artifact.get("requirements") != required:
        raise ValueError("Enrolled profile input requirements differ from frozen V1.3")
    bank = get_bank(model, background_dir)
    if artifact.get("background_features_sha256") != bank.features_sha256 or artifact.get("background_registry_sha256") != bank.registry_sha256:
        raise RuntimeError("Enrolled profile background lineage differs")
    excluded = artifact.get("background_author_excluded")
    bank.check_excluded_author(excluded)
    metadata = artifact["reference_documents"]
    if not isinstance(metadata, list) or len(metadata) < live.MIN_PROFILE_PAPERS or artifact["profile_papers"] != len(metadata):
        raise ValueError("Enrolled profile requires at least eight distinct papers")
    frame = pd.DataFrame(**artifact["feature_frame"])
    validate_frame(frame, bank.columns)
    if set(frame.paper_id) != {r["paper_id"] for r in metadata} or frame.paper_id.nunique() != len(metadata):
        raise ValueError("Enrolled reference identities do not match their features")
    hashes, identities = set(), set()
    for row in metadata:
        keys = identity_keys(row["paper_id"])
        if keys & identities:
            raise ValueError("Duplicate enrolled reference identity")
        identities.update(keys)
        for name in ("usable_text_sha256", "source_text_sha256", "feature_sha256"):
            if not isinstance(row.get(name), str) or not re.fullmatch(r"[0-9a-f]{64}", row[name]):
                raise ValueError("Enrolled reference provenance is missing")
        if row["usable_text_sha256"] in hashes or row["usable_words"] < live.MIN_CLEAN_WORDS:
            raise ValueError("Duplicate or short enrolled references")
        hashes.add(row["usable_text_sha256"])
        paper_frame = frame[frame.paper_id == row["paper_id"]]
        if feature_fingerprint(paper_frame) != row["feature_sha256"]:
            raise ValueError("Enrolled reference feature fingerprint differs")
        bank.check_reference_overlap(row["paper_id"], paper_frame, excluded)
    expected_docs = sum(author != excluded for author, _, _ in bank.documents)
    expected_authors = len(bank.authors - ({excluded} if excluded else set()))
    median = artifact["background_median"]
    if not isinstance(median, (float, int)) or not np.isfinite(median) or not 0 <= median <= 100:
        raise ValueError("Invalid enrolled background median")
    if artifact["background_documents"] != expected_docs or artifact["background_authors"] != expected_authors:
        raise ValueError("Enrolled background counts differ from the full frozen bank")
    columns = live.mw.numeric_feature_columns(frame)
    distances = live.mw.reference_document_distances(frame, columns)
    if len(distances) != len(metadata) or not np.isclose(np.median(distances), artifact["local_typical_genuine_distance"], rtol=1e-12, atol=1e-12):
        raise ValueError("Enrolled genuine calibration does not reproduce")
    with model._profile_lock:
        if profile_id in model._profiles:
            if model._profiles[profile_id].get("artifact_sha256") == expected_sha256:
                return profile_id
            raise ValueError("Profile ID already exists; do not overwrite another profile or version")
        model._frames[profile_id] = frame
        model._profiles[profile_id] = {**artifact, "artifact_sha256": expected_sha256}
    return profile_id
