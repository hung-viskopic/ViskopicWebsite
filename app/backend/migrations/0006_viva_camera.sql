-- Viva: optional webcam recording. With the camera on, each spoken answer is recorded as one small video
-- (camera + microphone) for a human reviewer to watch. Nothing analyses the video automatically: no face
-- recognition, gaze tracking or object detection. Stored encrypted like audio, under the same rules.

ALTER TABLE viva_invitations ADD COLUMN camera_required boolean NOT NULL DEFAULT false;
ALTER TABLE viva_invitations DROP CONSTRAINT viva_invitations_supervision_mode_check;
ALTER TABLE viva_invitations ADD CONSTRAINT viva_invitations_supervision_mode_check
  CHECK (supervision_mode IN ('oral_recorded','oral_video','restricted'));
ALTER TABLE viva_invitations ADD CONSTRAINT viva_invitations_camera_check
  CHECK (camera_required = (supervision_mode = 'oral_video'));

ALTER TABLE viva_recordings ADD COLUMN has_video boolean NOT NULL DEFAULT false;
ALTER TABLE viva_recordings DROP CONSTRAINT viva_recordings_mime_type_check;
ALTER TABLE viva_recordings ADD CONSTRAINT viva_recordings_mime_type_check
  CHECK (mime_type IN ('audio/webm','audio/ogg','audio/mp4','video/webm','video/mp4'));

ALTER TABLE viva_session_events DROP CONSTRAINT viva_session_events_kind_check;
ALTER TABLE viva_session_events ADD CONSTRAINT viva_session_events_kind_check CHECK (kind IN (
  'open','start','resume','second_session','question_released','answer_saved','question_timed_out',
  'late_write','focus_lost','focus_back','paste_blocked','fullscreen_exit','mic_unavailable',
  'camera_unavailable','submit'));
