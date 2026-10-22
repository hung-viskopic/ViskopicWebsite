"""Opt-in descriptions of the existing frozen calculation, not a new detector."""
from __future__ import annotations

import numpy as np

from .frozen import score_candidate_text_v1_3 as live


def finite_number(value):
    value = float(value)
    return value if np.isfinite(value) else None


def build_diagnostics(candidate_text, profile, candidate, columns, window_rows, sampled_text):
    normalized = " ".join(live.builder.sentence_units(live.normalise_plain_text(candidate_text)))
    base = live.mw.base
    usable = base.usable_features(profile, columns)
    window_scores = {int(row["window_id"]): row for row in window_rows}
    windows = []
    for _, row in candidate.iterrows():
        window_id = int(row["window_id"])
        text = sampled_text[window_id]
        start = normalized.find(text)
        unique = start >= 0 and normalized.find(text, start + 1) < 0
        means, stds, clipped = base.clipped_z_scores(profile, row, usable)
        features = []
        for name in usable:
            raw_z = (float(row[name]) - float(means[name])) / float(stds[name])
            features.append({"feature": name, "group": base.assign_group(name),
                             "candidate_value": finite_number(row[name]),
                             "reference_mean": finite_number(means[name]),
                             "reference_std": finite_number(stds[name]),
                             "z_score": finite_number(raw_z),
                             "clipped_z_score": finite_number(clipped[name]),
                             "used_in_distance": bool(np.isfinite(clipped[name]))})
        features.sort(key=lambda f: abs(f["clipped_z_score"] or 0), reverse=True)
        scored = window_scores[window_id]
        groups = {key.removeprefix("group_").removesuffix("_distance"): finite_number(value)
                  for key, value in scored.items() if key.startswith("group_") and key.endswith("_distance")}
        windows.append({"window_id": window_id, "center_fraction": (window_id - 0.5) / 3,
                        "sampled_text": text, "sampled_words": live.builder.wc(text),
                        "normalized_char_span": {"start": start, "end": start + len(text)} if unique else None,
                        "span_status": "unique" if unique else ("ambiguous_repeated_text" if start >= 0 else "not_located"),
                        "raw_distance": finite_number(scored["profile_distance"]),
                        "group_distances": groups, "features": features})
    return {"schema": "viskopic-v1.3-diagnostics.v1", "normalized_usable_text": normalized,
            "span_coordinate_system": "Python Unicode code-point indices into normalized_usable_text; zero-based, end-exclusive; not original/PDF offsets",
            "aggregation": "Group RMS distances averaged within each window; document raw distance is the median of window distances.",
            "interpretation": "Descriptive deviations from reference style. Not proof of AI use or causal attribution to individual words. Repeated quotes may not have a unique span.",
            "windows": windows}
