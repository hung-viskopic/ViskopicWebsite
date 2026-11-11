/**
 * Plain-language wording shared by the home page and the case page.
 * These sentences summarise stored outcomes; they never replace the model's own template statements,
 * which stay available word for word under "Exact finding".
 */
import type { CaseRow } from "./types";

type Check = { status: string; outcome: string | null; website_status?: string | null } | null;

export function styleSentence(c: Check): { text: string; flagged: boolean } {
  if (!c) return { text: "Writing style hasn't run yet.", flagged: false };
  if (c.status === "completed") {
    if (c.outcome === "stylistically-unusual") return { text: "Writing style is unusual for this student.", flagged: true };
    return { text: "Writing style matches the student's past essays.", flagged: false };
  }
  switch (c.website_status) {
    case "insufficient_evidence": return { text: "Writing style is waiting for more past essays.", flagged: false };
    case "quality_review_required": return { text: "Writing style is waiting for texts to be checked.", flagged: false };
    case "unsupported_text": return { text: "Writing style can't compare these texts.", flagged: false };
    default: return { text: "Writing style didn't produce a result.", flagged: false };
  }
}

export function aiSentence(c: Check): { text: string; flagged: boolean } {
  if (!c || c.status === "unavailable") return { text: "AI comparison didn't run.", flagged: false };
  if (c.status === "completed") {
    if (c.outcome === "interchangeable") return { text: "The opening reads like AI essays on this assignment.", flagged: true };
    return { text: "AI comparison did not flag it.", flagged: false };
  }
  return { text: "AI comparison didn't run on this essay.", flagged: false };
}

export const confidenceWord = { low: "Low", medium: "Medium", high: "High" } as const;

export function confidenceSentence(row: CaseRow): string | null {
  const c = row.consistency;
  if (!c || c.status !== "completed" || !c.confidence) return null;
  if (c.demonstration) return "Demonstration only: compared with a research author, not this student.";
  const n = c.reference_count ?? 0;
  const sup = c.supervised_count ?? 0;
  return `${confidenceWord[c.confidence]} confidence: ${n} past essays${sup ? `, ${sup} written in an exam` : ""}.`;
}

export function shortDate(s?: string | null) {
  if (!s) return "";
  const d = new Date(s);
  const today = new Date();
  const days = Math.round((today.setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 86_400_000);
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  if (days === 0) return `today at ${time}`;
  if (days === 1) return `yesterday at ${time}`;
  return d.toLocaleDateString("en-GB", { weekday: days < 7 ? "long" : undefined, day: "numeric", month: "long" });
}

export function plural(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}
