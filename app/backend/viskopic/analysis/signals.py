"""Document signals: facts from the file itself, recorded during extraction.

These are observations, not a verdict. Hidden text is always reported; a very short recorded
editing time is reported when it is below one minute per 100 words. Author names and other
personal metadata are never copied into statements.
"""
from __future__ import annotations

from .base import AnalysisContext, EvidenceItem, PipelineResult

HIDDEN_REASON = {
    "white_text": "white text",
    "tiny_text": "text at 2pt or smaller",
    "hidden_formatting": "text formatted as hidden",
    "invisible_render_mode": "an invisible PDF text layer",
}


class DocumentSignalsPipeline:
    name = "document_signals"
    version = "document-signals-v1"
    category = "document"

    def run(self, ctx: AnalysisContext) -> PipelineResult:
        s = ctx.signals or {}
        items: list[EvidenceItem] = []
        for h in s.get("hidden_text_spans") or []:
            what = HIDDEN_REASON.get(h.get("reason"), "hidden content")
            if h.get("chars"):
                statement = (f"The file contains {h['chars']} characters of {what}. It was removed before analysis "
                             f"and is not part of the analysed text.")
            else:
                statement = f"The file contains {what} on {h.get('pages', 'some')} page(s). It was removed before analysis."
            items.append(EvidenceItem(category="document", kind="hidden_text", statement=statement,
                                      observed=h.get("chars"), details={"reason": h.get("reason")}))
        meta = s.get("metadata") or {}
        minutes = meta.get("total_edit_minutes")
        if isinstance(minutes, (int, float)) and ctx.word_count and minutes < ctx.word_count / 100:
            items.append(EvidenceItem(
                category="document", kind="editing_time",
                statement=(f"The file records {minutes} minutes of editing for {ctx.word_count} words. Text pasted in "
                           f"from elsewhere often shows a short editing time, but so does a file re-saved or "
                           f"exported from another editor."),
                observed=float(minutes), details={"words": ctx.word_count},
            ))
        return PipelineResult(status="completed", outcome="signals_found" if items else "no_signals",
                              evidence=items, details={"hidden_text_chars": s.get("hidden_text_chars", 0)})


class NotIntegratedPipeline:
    """Placeholder for a pipeline that isn't available yet, so the report says so explicitly."""

    def __init__(self, name: str, category: str, reason: str):
        self.name, self.category, self.reason = name, category, reason
        self.version = f"{name}-not-integrated"

    def run(self, ctx: AnalysisContext) -> PipelineResult:
        return PipelineResult(status="unavailable", reason=self.reason)


def registered_pipelines():
    from .consistency import ConsistencyPipeline

    return [
        DocumentSignalsPipeline(),
        ConsistencyPipeline(),
        NotIntegratedPipeline("language", "regeneration",
                              "The language (β, regeneration) pipeline has not been built yet."),
        NotIntegratedPipeline("authenticity", "semantic",
                              "The authenticity (α, semantic) model is still research code with no runnable scorer."),
    ]
