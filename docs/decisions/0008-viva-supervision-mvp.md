# 0008 Viva supervision MVP: format and server rules, not automated proctoring

Date: 2026-10-28

## Decision

The viva MVP limits outside help through the format and through rules the server enforces, and shows a human
reviewer neutral signals. It does not build automated webcam proctoring.

- Questions about specific passages of the student's own essay; spoken answers recorded in the browser; short,
  server-measured time per answer; one question at a time, no reading ahead or going back; one session at a time.
- Signals (left the page, paste attempts, late answers, device switch, speech/silence timeline) are hints shown
  next to the answer, never a score and never a flag that changes a case.
- Token-only access for test vivas, delivered through Mailpit locally. Student SSO is required before any pilot.
- External AI (OpenAI) may draft questions and transcribe answers for local testing with synthetic essays only,
  behind `VISKOPIC_ALLOW_EXTERNAL_AI`. Drafted passages must appear verbatim in the essay; a reviewer approves
  every question; the AI never rates answers.

## Webcam recording (added 2026-10-29)

A per-invitation option records the webcam together with each spoken answer, as many company online assessments
do. The student is told on the intro page, sees a self-view and must allow the camera to start; students who can't
use a camera get an audio-only or typed viva. The video is watched by a person only and stored encrypted with the
audio's rules. No automated analysis, which keeps it ordinary personal data rather than biometric processing or
high-risk AI monitoring.

## Not built, and why

Gaze tracking for reading detection, response-latency flags, pitch-variance ("monotone") flags and phone/headphone
object detection were considered. Rejected for now: webcam gaze and object detection are unreliable across
lighting, glasses, camera angles and disability, and need continuous video; latency and pitch flags overlap heavily
with normal thinking pauses, anxiety, accents and non-native speech, so they would flag innocent students and
cut against comparing students with themselves. AI that monitors students' behaviour during tests is high-risk
under the EU AI Act. Any such feature first needs a study measuring accuracy and false positives by group;
universities that want camera proctoring now can use a third-party vendor (later milestone).

## Known limits of the MVP

Identity is not verified (anyone with the link can answer); a helper in the room or a fluent reader of a phone
screen is not prevented. These cases go to a live viva. Recordings have no automatic retention purge yet. The rate
limit is per API process and per client IP.

## Full screen, copy blocking and spoken Visq 2 (added 2026-11-04)

A self-test showed the gap the "known limits" above describe: with ChatGPT in a window beside the viva, answers
retyped by hand produced almost no signals (one page leave, no paste). Three changes raise the effort and give the
reviewer more to go on, without any automated judgement of the student:

- **Full screen, required by default** (`viva_invitations.fullscreen_required`, migration 0009). Laptop and desktop
  browsers only: a phone, tablet or browser without full screen is asked to use a computer. Leaving full screen
  (including an accidental Esc, which browsers always allow) covers the question with a "Return to full screen"
  panel; the typed answer is kept and the server clock keeps running, so leaving can't buy time. Exits and the time
  spent out are recorded as hints. The reviewer can switch it off for students whose assistive tools don't work in
  full screen.
- **Copying the question is blocked** and each attempt recorded (`copy_blocked`), like pasting. The answer box still
  allows copying within itself.
- **Visq 2 vivas may be spoken**, optionally with the webcam; the extension stays typed. Reading a prepared answer
  aloud is easier for a person to notice than retyping it. Each spoken answer is transcribed before Visq reads it.

None of this stops a second device. Serious cases still go to a live or invigilated viva.
