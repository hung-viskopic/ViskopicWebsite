"""The pipeline contract.

Every analysis pipeline (Δ consistency, β language, α authenticity, document signals)
implements `Pipeline.run(ctx) -> PipelineResult`. A result is never a bare score: it carries
evidence items (template statements with the observed value against the student's own
baseline, character spans to highlight, and a contribution), plus the pipeline's version and
the threshold it used so the result can be reproduced.

Pipelines only ever see ids, pseudonyms and text. They never see student names or numbers.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

Category = Literal["style", "semantic", "regeneration", "document"]
RunStatus = Literal["completed", "not_eligible", "unavailable", "failed"]


@dataclass(frozen=True)
class ProfileRef:
    """The student's current baseline profile for one pipeline (row from baseline_profiles)."""

    id: UUID
    version: int
    source: Literal["enrolled", "demonstration"]
    demo_author_id: str | None
    artifact_sha256: str | None
    reference_count: int
    supervised_count: int
    # Decrypts and returns the stored artifact bytes. Only called when the pipeline needs it.
    load_artifact: Callable[[], bytes] | None = None


QualityLanguage = Literal["english", "non_english", "mixed", "uncertain", "unreviewed"]
QualityExtraction = Literal["pass", "fail", "unreviewed"]


@dataclass(frozen=True)
class QualityRecord:
    """A reviewer's language/extraction check, read from `document_quality_reviews` (never from a browser flag).

    For the submission it is the latest review of that document, bound to the SHA-256 of the exact text that
    was reviewed (`text_sha256`), so the model's adapter can tell when the text has changed since.
    For the baseline it is the combined outcome over every reference document of the profile; `stale` is set
    when any of those reviews was of a different text than the one now stored.
    """

    review_id: str
    language: QualityLanguage
    extraction: QualityExtraction
    text_sha256: str | None = None
    stale: bool = False


@dataclass(frozen=True)
class AnalysisContext:
    institution_id: UUID
    case_id: UUID
    document_id: UUID
    student_pseudonym: str
    text: str                                   # extracted text of the submission (hidden text already removed)
    word_count: int
    signals: dict | None                        # document_signals row, if any
    profiles: dict[str, ProfileRef] = field(default_factory=dict)   # pipeline name -> ready profile
    candidate_quality: QualityRecord | None = None   # review of this submission's text, if any
    reference_quality: QualityRecord | None = None   # combined review of the profile's baseline documents


@dataclass
class Span:
    start: int
    end: int
    status: Literal["exact", "approximate"]
    window: int | None = None

    def as_json(self) -> dict:
        return {"start": self.start, "end": self.end, "status": self.status, "window": self.window}


@dataclass
class EvidenceItem:
    category: Category
    kind: str
    statement: str                              # generated from a template, never free LLM text
    observed: float | None = None
    baseline_mean: float | None = None
    baseline_sd: float | None = None
    z_score: float | None = None
    contribution: float | None = None
    spans: list[Span] = field(default_factory=list)
    details: dict = field(default_factory=dict)  # numbers and labels only, never essay text


@dataclass
class PipelineResult:
    status: RunStatus
    score: float | None = None
    threshold: float | None = None
    outcome: str | None = None
    confidence: Literal["low", "medium", "high"] | None = None
    reason: str | None = None
    profile_id: UUID | None = None
    details: dict = field(default_factory=dict)
    evidence: list[EvidenceItem] = field(default_factory=list)


class Pipeline(Protocol):
    name: str
    version: str
    category: Category

    def run(self, ctx: AnalysisContext) -> PipelineResult: ...


class PipelineInputError(ValueError):
    """The input can't be analysed by this pipeline (too short, no profile, ...).

    Recorded as a 'not_eligible' run with the message as the reason. Messages must not
    contain essay text.
    """
