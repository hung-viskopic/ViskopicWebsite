"""Website contract around unchanged V1.3. No new model calibration.

QualityReview objects must come from trusted server-side review records, never
from booleans supplied by the browser. All scored outputs remain experimental.
"""
from __future__ import annotations
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Collection
from viskopic_v13.frozen import score_candidate_text_v1_3 as live

SCHEMA = 'viskopic.website-consistency.v1'
MIN_REFERENCES = 8
MIN_USABLE_WORDS = 1650

@dataclass(frozen=True)
class QualityReview:
    subject_sha256: str
    review_id: str
    language: str  # english, non_english, mixed, uncertain, unreviewed
    extraction: str  # pass, fail, unreviewed

    def __post_init__(self):
        if self.language not in {'english','non_english','mixed','uncertain','unreviewed'}:
            raise ValueError('Unknown language review state')
        if self.extraction not in {'pass','fail','unreviewed'}:
            raise ValueError('Unknown extraction review state')
        if not isinstance(self.subject_sha256,str) or len(self.subject_sha256)!=64 or any(c not in '0123456789abcdef' for c in self.subject_sha256):
            raise ValueError('Review requires a SHA-256 digest')
        if not isinstance(self.review_id,str) or not self.review_id.strip():
            raise ValueError('Review requires a server-side review identifier')

def candidate_digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def profile_digest(scorer, profile_id: str) -> str:
    # Pin review records to the exact immutable profile and calibration.
    with scorer._profile_lock:
        row = scorer._profiles[profile_id]
        payload = {'profile_id':profile_id,'profile':row,
                   'threshold':scorer.threshold,'v13_source_files':scorer.v13['source_files']}
        raw = json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()

def usable_word_count(text: str) -> int:
    normalized = live.normalise_plain_text(text)
    return len(' '.join(live.builder.sentence_units(normalized)).split())

def _review_state(review, digest):
    if review is None:return 'unreviewed'
    if not isinstance(review, QualityReview):raise TypeError('Use a trusted QualityReview record')
    if review.subject_sha256 != digest:return 'stale_review'
    if review.language in {'non_english','mixed'} or review.extraction == 'fail':return 'unsupported_text'
    if review.language != 'english' or review.extraction != 'pass':return 'unreviewed'
    return 'passed'

def score_for_website(scorer, candidate_text: str, authorized_profile_id: str, *,
                      permitted_profile_ids: Collection[str], candidate_review=None,
                      reference_review=None, allow_unreviewed_demo=False,
                      include_evidence=False) -> dict:
    """Call after authentication/profile ownership resolution on the server.

    permitted_profile_ids is obtained from the host's authorization layer.
    Reference review must cover all original reference texts and match the
    current profile digest. Unknown/stale quality cannot produce a standard
    scored result. Explicit local/demo mode permits unreviewed input but does
    not bypass known failures or stale records.
    """
    if not isinstance(authorized_profile_id,str) or not authorized_profile_id:
        raise ValueError('A profile ID is required')
    if isinstance(permitted_profile_ids,(str,bytes)) or authorized_profile_id not in permitted_profile_ids:
        raise PermissionError('Profile not permitted')
    if type(allow_unreviewed_demo) is not bool or type(include_evidence) is not bool:
        raise TypeError('Server options must be booleans')
    profiles={p['profile_id']:p for p in scorer.list_profiles()}
    if authorized_profile_id not in profiles:raise KeyError('Unknown permitted profile')
    metadata=profiles[authorized_profile_id]
    if not isinstance(candidate_text,str) or not candidate_text.strip():
        raise ValueError('Candidate text must be non-empty plain text')
    words=usable_word_count(candidate_text)
    response={'schema':SCHEMA,'model_version':'1.3','deployment_status':'experimental',
              'profile_id':authorized_profile_id,'status':None,'message':None,
              'input':{'reference_essays':metadata['reference_papers'],'candidate_usable_words':words,
                       'minimum_reference_essays':MIN_REFERENCES,'minimum_usable_words':MIN_USABLE_WORDS},
              'quality':{'candidate':'not_checked','references':'not_checked','comparison_bank':'not_fully_language_audited'},
              'measurements':None,'model_decision':None,'original_model_result':None}
    if metadata['reference_papers'] < MIN_REFERENCES or words < MIN_USABLE_WORDS:
        response.update(status='insufficient_evidence',message='V1.3 needs at least eight eligible reference essays and 1,650 usable words in the submission. Each reference also requires 1,650 usable words.')
        return response
    q_candidate=_review_state(candidate_review,candidate_digest(candidate_text))
    q_reference=_review_state(reference_review,profile_digest(scorer,authorized_profile_id))
    response['quality'].update(candidate=q_candidate,references=q_reference)
    states={q_candidate,q_reference}
    if 'stale_review' in states:
        response.update(status='quality_review_required',message='The text or profile has changed since quality review. Review the current version.')
        return response
    if 'unsupported_text' in states:
        response.update(status='unsupported_text',message='The current English-prose configuration does not support this language or extraction-quality result.')
        return response
    unreviewed='unreviewed' in states
    if unreviewed and not allow_unreviewed_demo:
        response.update(status='quality_review_required',message='Language and extraction quality must be reviewed for the submission and every reference essay before scoring.')
        return response
    try:
        original=scorer.score_text(candidate_text,authorized_profile_id,include_evidence=include_evidence)
    except ValueError:
        response.update(status='cannot_score',message='The scorer could not build a valid comparison from this text and history. Check the source extraction and sampled windows.')
        return response
    numeric={name:float(original[name]) for name in ['raw_distance','shrunk_typical_genuine_distance','v1_2_similarity','background_median_v1_2_similarity','v1_3_centered_similarity','frozen_threshold']}
    if not all(math.isfinite(x) for x in numeric.values()) or numeric['raw_distance']<0 or numeric['shrunk_typical_genuine_distance']<=0:
        raise RuntimeError('Invalid model measurements; do not display a result')
    expected='same-author-compatible' if numeric['v1_3_centered_similarity']>=numeric['frozen_threshold'] else 'stylistically-unusual'
    if original['decision']!=expected:raise RuntimeError('Model decision/threshold mismatch')
    relative=numeric['raw_distance']/numeric['shrunk_typical_genuine_distance']
    numeric.update(relative_distance=relative,log_v12_similarity=math.log(100)-.5*relative**2,
                   margin_to_threshold=numeric['v1_3_centered_similarity']-numeric['frozen_threshold'])
    if not all(math.isfinite(x) for x in numeric.values()):raise RuntimeError('Invalid derived diagnostics')
    response.update(status='scored_demo_unreviewed' if unreviewed else 'scored',
                    message='Experimental style comparison. The comparison bank and profile-specific error rates are not fully validated for student use.',
                    measurements=numeric,model_decision=original['decision'],original_model_result=original)
    return response
