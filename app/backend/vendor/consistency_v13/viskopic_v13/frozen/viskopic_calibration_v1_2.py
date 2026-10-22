from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


CALIBRATION_VERSION = "Viskopic Calibration V1.2"
DEFAULT_K = 5.0


@dataclass(frozen=True)
class CalibrationConfig:
    version: str
    k: float
    global_t: float
    source_authors: int | None = None
    source_comparisons_sha256: str | None = None


def maturity_label(reference_document_count: int) -> str:
    n = int(reference_document_count)
    if n < 6:
        return "insufficient"
    if n < 8:
        return "low_confidence"
    if n < 10:
        return "usable"
    return "mature"


def shrunk_typical_distance(
    local_t: float,
    reference_document_count: int,
    global_t: float,
    k: float = DEFAULT_K,
) -> float:
    local_t = float(local_t)
    global_t = float(global_t)
    n = int(reference_document_count)
    k = float(k)

    if not math.isfinite(local_t) or local_t <= 0:
        raise ValueError(f"local_t must be positive and finite, got {local_t}")
    if not math.isfinite(global_t) or global_t <= 0:
        raise ValueError(f"global_t must be positive and finite, got {global_t}")
    if n < 1:
        raise ValueError(f"reference_document_count must be >= 1, got {n}")
    if not math.isfinite(k) or k < 0:
        raise ValueError(f"k must be finite and >= 0, got {k}")

    value = (n * local_t + k * global_t) / (n + k)
    return max(float(value), 1e-8)


def relative_distance(
    raw_distance: float,
    shrunk_t: float,
) -> float:
    raw_distance = float(raw_distance)
    shrunk_t = max(float(shrunk_t), 1e-8)
    return raw_distance / shrunk_t


def similarity_from_relative(relative: float) -> float:
    relative = float(relative)
    score = 100.0 * math.exp(-0.5 * relative ** 2)
    return max(0.0, min(100.0, float(score)))


def score_with_shrinkage(
    raw_distance: float,
    local_t: float,
    reference_document_count: int,
    global_t: float,
    k: float = DEFAULT_K,
) -> dict[str, float | int | str]:
    t_star = shrunk_typical_distance(
        local_t=local_t,
        reference_document_count=reference_document_count,
        global_t=global_t,
        k=k,
    )
    r_star = relative_distance(raw_distance, t_star)
    score = similarity_from_relative(r_star)

    return {
        "raw_distance": float(raw_distance),
        "local_typical_genuine_distance": float(local_t),
        "global_typical_genuine_distance": float(global_t),
        "reference_document_count": int(reference_document_count),
        "shrinkage_k": float(k),
        "shrunk_typical_genuine_distance": float(t_star),
        "relative_distance": float(r_star),
        "similarity_score": float(score),
        "profile_maturity": maturity_label(reference_document_count),
    }


def load_config(path: str | Path) -> CalibrationConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Calibration config not found: {path.resolve()}\n"
            "Create it first with freeze_calibration_v1_2.py"
        )

    data = json.loads(path.read_text(encoding="utf-8"))

    version = str(data.get("version", ""))
    if version != CALIBRATION_VERSION:
        raise ValueError(
            f"Expected calibration version {CALIBRATION_VERSION!r}, "
            f"found {version!r}"
        )

    k = float(data["k"])
    global_t = float(data["global_t"])

    if k != DEFAULT_K:
        raise ValueError(
            f"V1.2 is frozen at k={DEFAULT_K:g}; config contains k={k:g}."
        )

    if not math.isfinite(global_t) or global_t <= 0:
        raise ValueError(f"Invalid global_t in config: {global_t}")

    return CalibrationConfig(
        version=version,
        k=k,
        global_t=global_t,
        source_authors=(
            int(data["source_authors"])
            if data.get("source_authors") is not None
            else None
        ),
        source_comparisons_sha256=data.get("source_comparisons_sha256"),
    )
