-- Viva supervision, after a self-test where answers were retyped from ChatGPT in a window beside the viva:
--
-- * Full screen can be required (the default for new invitations). Leaving it covers the question until the
--   student returns; the server clock keeps running. Exits and the time spent out are recorded as hints.
--   Phones and tablets are refused when full screen is required (laptop and desktop browsers only).
-- * Copying the question text is blocked and each attempt recorded, like pasting.
-- * Visq 2 vivas may take spoken answers (optionally with the webcam); the extension stays typed. Each spoken
--   answer is transcribed before Visq takes it in.

ALTER TABLE viva_invitations ADD COLUMN fullscreen_required boolean NOT NULL DEFAULT false;

ALTER TABLE viva_invitations DROP CONSTRAINT viva_invitations_visq2_check;
ALTER TABLE viva_invitations ADD CONSTRAINT viva_invitations_visq2_check
  CHECK (flow = 'standard' OR extension_seconds IS NOT NULL);

ALTER TABLE viva_session_events DROP CONSTRAINT viva_session_events_kind_check;
ALTER TABLE viva_session_events ADD CONSTRAINT viva_session_events_kind_check CHECK (kind IN (
  'open','start','resume','second_session','question_released','answer_saved','question_timed_out',
  'late_write','focus_lost','focus_back','paste_blocked','fullscreen_exit','mic_unavailable',
  'camera_unavailable','submit','followup_waiting','example_shown','visq2_offline_step',
  'fullscreen_back','copy_blocked'));
