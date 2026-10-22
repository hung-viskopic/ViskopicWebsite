export type Department = { id: string; name: string; status: string };

export type Course = {
  id: string;
  code: string;
  title: string;
  status: string;
  department_id: string;
  department_name: string;
  active_students?: number;
  documents?: number;
  access: "manage" | "view" | null;
};

export type Student = {
  id: string;
  pseudonym: string;
  external_ref: string;
  display_name: string | null;
  status: string;
  baseline_docs?: number;
  submissions?: number;
  enrolment_id?: string;
  enrolment_status?: string;
};

export type Assignment = { id: string; title: string; prompt: string | null; genre: string | null; due_at: string | null };

export type DocumentRow = {
  id: string;
  student_id: string;
  course_id: string;
  role: "baseline" | "submission";
  provenance: "supervised" | "unsupervised";
  original_filename: string;
  file_type: string;
  size_bytes: number;
  sha256: string;
  status: string;
  attempts: number;
  last_error: string | null;
  uploaded_at: string;
  processed_at: string | null;
  course_code: string;
  student_pseudonym: string;
  assignment_title: string | null;
  uploaded_by_email: string;
  word_count: number | null;
  removed_at?: string | null;
  removal_reason?: string | null;
  removed_by_email?: string | null;
};

export type Grant = { id: string; user_id: string; role: string; scope_type: string; scope_id: string };
export type User = {
  id: string;
  email: string;
  display_name: string | null;
  status: string;
  last_login_at: string | null;
  roles: Grant[];
};

export type Span = { start: number; end: number; status: "exact" | "approximate"; window: number | null };

export type EvidenceItem = {
  ordinal: number;
  category: "style" | "semantic" | "regeneration" | "document";
  kind: string;
  statement: string;
  observed: number | null;
  baseline_mean: number | null;
  baseline_sd: number | null;
  z_score: number | null;
  contribution: number | null;
  spans: Span[];
  details: Record<string, unknown>;
};

export type AnalysisRun = {
  id: string;
  pipeline: string;
  label: string;
  pipeline_version: string;
  status: "completed" | "not_eligible" | "unavailable" | "failed";
  score: number | null;
  threshold: number | null;
  outcome: string | null;
  confidence: "low" | "medium" | "high" | null;
  reason: string | null;
  details: Record<string, unknown> & {
    // Δ consistency: the model team's website contract (viskopic.website-consistency.v1).
    website_status?: ConsistencyState;
    deployment_status?: "experimental";
    message?: string;
    quality?: { candidate: string; references: string; comparison_bank: string };
    input?: {
      reference_essays: number;
      candidate_usable_words: number;
      minimum_reference_essays: number;
      minimum_usable_words: number;
    };
    measurements?: ConsistencyMeasurements;
    interpretation_warning?: string;
    confidence_reason?: string;
    profile_papers?: number;
    candidate_usable_words?: number;
    model_version?: string;
    windows?: { window: number; raw_distance: number; sampled_words: number; span: Omit<Span, "window"> | null }[];
  };
  created_at: string;
  profile: {
    id: string;
    version: number;
    source: "enrolled" | "demonstration";
    demo_author_id: string | null;
    reference_count: number;
    supervised_count: number;
    status: string;
  } | null;
  evidence: EvidenceItem[];
};

export type ConsistencyState =
  | "insufficient_evidence"
  | "quality_review_required"
  | "unsupported_text"
  | "cannot_score"
  | "scored"
  | "scored_demo_unreviewed";

export type ConsistencyMeasurements = {
  v1_3_centered_similarity: number;
  frozen_threshold: number;
  margin_to_threshold: number;
  raw_distance: number;
  shrunk_typical_genuine_distance: number;
  relative_distance: number;
  v1_2_similarity: number;
  background_median_v1_2_similarity: number;
  log_v12_similarity: number;
};

export type QualityReview = {
  language: "english" | "non_english" | "mixed" | "uncertain";
  extraction: "pass" | "fail";
  reviewed_at: string;
  reviewed_by_email: string;
  current: boolean;
};

export type AnalysisReport = {
  role: "baseline" | "submission";
  case: { id: string; status: string; created_at: string; updated_at: string } | null;
  runs: AnalysisRun[];
  total_runs?: number;
};

export type BaselineProfile = {
  id: string;
  pipeline: string;
  version: number;
  source: "enrolled" | "demonstration";
  demo_author_id: string | null;
  model_version: string;
  status: "queued" | "building" | "ready" | "failed" | "superseded";
  reference_count: number;
  supervised_count: number;
  artifact_sha256: string | null;
  summary: { maturity?: string; background_documents?: number; note?: string };
  error: string | null;
  created_at: string;
  built_at: string | null;
};

export type ProfilesResponse = {
  profiles: BaselineProfile[];
  consistency: {
    model_version: string;
    pipeline_version: string;
    min_documents: number;
    min_words: number;
    eligible_documents: number;
    quality_passed_documents: number;
    quality_problem_documents: number;
    demo_profiles_allowed: boolean;
    unreviewed_scoring_allowed: boolean;
  };
};
