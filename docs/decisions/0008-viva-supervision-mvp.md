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
