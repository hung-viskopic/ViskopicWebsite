"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

// Student viva page. Reached by an emailed link; no staff sign-in. The server releases one question at a time and
// enforces every time limit, so this page only shows the clock and records; it can't extend anything.

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Intro = {
  institution_name: string;
  status: string;
  message: string | null;
  kind: "style" | "reasoning";
  answer_format: "spoken" | "typed";
  question_count: number;
  prep_seconds: number;
  answer_seconds: number;
  deadline_at: string;
  is_test: boolean;
  transcribed: boolean;
};

type Question = {
  done: false;
  question: { id: string; position: number; total: number; text: string; passage: string | null };
  answer_format: "spoken" | "typed";
  prep_seconds: number;
  answer_seconds: number;
  released_at: string;
  due_at: string;
  server_now: string;
};

type Next = Question | { done: true; total: number };
type BrowserEvent = { kind: string; question_id?: string; detail?: Record<string, number | boolean> };

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function store(key: string, value?: string | null) {
  try {
    if (value === undefined) return sessionStorage.getItem(key);
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    /* private mode: the session simply isn't remembered across reloads */
  }
  return null;
}

function pickMime(): string {
  if (typeof MediaRecorder === "undefined") return "";
  for (const m of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"]) {
    if (MediaRecorder.isTypeSupported(m)) return m;
  }
  return "";
}

function duration(s: number) {
  if (s < 60 || s % 60 !== 0) return s < 60 ? `${s} seconds` : `${Math.floor(s / 60)} min ${s % 60} s`;
  return s === 60 ? "1 minute" : `${s / 60} minutes`;
}

function clock(s: number) {
  const v = Math.max(0, Math.ceil(s));
  return `${Math.floor(v / 60)}:${String(v % 60).padStart(2, "0")}`;
}

export default function VivaPage() {
  const { token } = useParams<{ token: string }>();
  const [intro, setIntro] = useState<Intro | null>(null);
  const [stage, setStage] = useState<"loading" | "intro" | "question" | "finished" | "submitted" | "closed" | "taken">("loading");
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState<Question | null>(null);
  const [total, setTotal] = useState(0);
  const [loads, setLoads] = useState(0); // remounts the question card if the same question comes back after an error
  const session = useRef<string | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const events = useRef<BrowserEvent[]>([]);
  const currentQ = useRef<string | undefined>(undefined);
  const skew = useRef(0); // server clock minus browser clock, ms

  const call = useCallback(async <T,>(method: string, path: string, body?: BodyInit, contentType?: string): Promise<T> => {
    const headers: Record<string, string> = {};
    if (session.current) headers["X-Viva-Session"] = session.current;
    if (contentType) headers["Content-Type"] = contentType;
    const res = await fetch(`${API}/api/public/viva/${token}${path}`, { method, headers, body, referrerPolicy: "no-referrer" });
    if (!res.ok) {
      const b = await res.json().catch(() => ({}));
      throw new HttpError(res.status, typeof b.detail === "string" ? b.detail : "Something went wrong. Please try again.");
    }
    return res.json() as Promise<T>;
  }, [token]);

  const flush = useCallback(async () => {
    if (!session.current || events.current.length === 0) return;
    const batch = events.current.splice(0, 50);
    await call("POST", "/events", JSON.stringify({ events: batch }), "application/json").catch(() => {});
  }, [call]);

  const track = useCallback((kind: string, detail?: Record<string, number | boolean>) => {
    events.current.push({ kind, question_id: currentQ.current, detail });
  }, []);

  // ---------------------------------------------------------------- load
  useEffect(() => {
    call<Intro>("GET", "")
      .then((i) => {
        setIntro(i);
        setTotal(i.question_count);
        if (i.status === "submitted") setStage("submitted");
        else if (i.message) setStage("closed");
        else if (i.status === "in_progress" && store(`viva:${token}`)) {
          session.current = store(`viva:${token}`);
          setStage("intro"); // resume after a reload in the same tab, once the microphone is ready again
        } else setStage("intro");
      })
      .catch((e: HttpError) => { setError(e.message); setStage("closed"); });
  }, [call, token]);

  // ---------------------------------------------------------------- signals (shown to the reviewer as hints)
  useEffect(() => {
    if (stage !== "question") return;
    const onVis = () => track(document.hidden ? "focus_lost" : "focus_back");
    const onBlur = () => !document.hidden && track("focus_lost");
    const onFocus = () => track("focus_back");
    const onFs = () => !document.fullscreenElement && track("fullscreen_exit");
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("blur", onBlur);
    window.addEventListener("focus", onFocus);
    document.addEventListener("fullscreenchange", onFs);
    const t = setInterval(flush, 3000);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("fullscreenchange", onFs);
      clearInterval(t);
    };
  }, [stage, track, flush]);

  // ---------------------------------------------------------------- flow
  const loadNext = useCallback(async () => {
    try {
      const n = await call<Next>("POST", "/next");
      if (n.done) {
        currentQ.current = undefined;
        await flush();
        setStage("finished");
        return;
      }
      skew.current = new Date(n.server_now).getTime() - Date.now();
      currentQ.current = n.question.id;
      setTotal(n.question.total);
      setQ(n);
      setLoads((k) => k + 1);
      setStage("question");
    } catch (e) {
      const err = e as HttpError;
      if (err.status === 409 && err.message.includes("another window")) setStage("taken");
      else setError(err.message);
    }
  }, [call, flush]);

  async function begin() {
    setError(null);
    if (intro?.answer_format === "spoken") {
      try {
        stream.current = stream.current ?? (await navigator.mediaDevices.getUserMedia({ audio: true }));
      } catch {
        setError("We couldn't use your microphone. Allow microphone access in your browser and try again. "
          + "If you can't use a microphone, contact your module team for a typed version.");
        return;
      }
    }
    try {
      if (!session.current) {
        const r = await call<{ session: string }>("POST", "/start");
        session.current = r.session;
        store(`viva:${token}`, r.session);
      }
      document.documentElement.requestFullscreen?.().catch(() => {});
      await loadNext();
    } catch (e) {
      const err = e as HttpError;
      if (err.status === 409 && err.message.includes("another window")) setStage("taken");
      else setError(err.message);
    }
  }

  async function takeOver() {
    session.current = null;
    store(`viva:${token}`, null);
    setStage("intro");
    setError(null);
  }

  async function submit() {
    try {
      await call("POST", "/submit");
      store(`viva:${token}`, null);
      stream.current?.getTracks().forEach((t) => t.stop());
      document.fullscreenElement && document.exitFullscreen().catch(() => {});
      setStage("submitted");
    } catch (e) {
      setError((e as HttpError).message);
    }
  }

  // ---------------------------------------------------------------- render
  return (
    <div className="viva-page">
      <header className="viva-top">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/logo.svg" alt="" width={24} height={24} />
        <span>{intro?.institution_name ?? "Viskopic"}</span>
        {intro?.is_test && <span className="viva-test">Test</span>}
      </header>
      <main className="viva-main">
        {stage === "loading" && <p className="muted">Loading…</p>}
        {stage === "closed" && (
          <div className="card narrow"><h1>Follow-up conversation</h1><p>{intro?.message ?? error ?? "This link is not valid."}</p></div>
        )}
        {stage === "submitted" && (
          <div className="card narrow" role="status">
            <h1>Thank you</h1>
            <p>Your answers have been submitted. You can close this page. Your module team will be in touch if they need anything else.</p>
          </div>
        )}
        {stage === "taken" && (
          <div className="card narrow">
            <h1>Open in another window</h1>
            <p>This conversation was opened in another window or on another device. You can only use one at a time, and switching is recorded.</p>
            <button className="btn" onClick={takeOver}>Continue here instead</button>
          </div>
        )}
        {stage === "intro" && intro && <IntroCard intro={intro} resuming={!!session.current} error={error} onBegin={begin} />}
        {stage === "question" && q && (
          <QuestionCard key={`${q.question.id}-${loads}`} q={q} skew={skew.current} stream={stream.current} call={call}
            onPasteBlocked={(chars) => track("paste_blocked", { chars })} onSaved={loadNext} onError={setError} />
        )}
        {stage === "question" && error && <p className="error" role="alert">{error}</p>}
        {stage === "finished" && (
          <div className="card narrow">
            <h1>All questions done</h1>
            <p>You have reached the end of the {total} questions. Submit to finish. You can&apos;t change answers after this.</p>
            {error && <p className="error" role="alert">{error}</p>}
            <button className="btn" onClick={submit}>Submit answers</button>
          </div>
        )}
      </main>
    </div>
  );
}

function IntroCard({ intro, resuming, error, onBegin }: { intro: Intro; resuming: boolean; error: string | null; onBegin: () => void }) {
  const [agreed, setAgreed] = useState(resuming);
  const [busy, setBusy] = useState(false);
  const spoken = intro.answer_format === "spoken";
  const perQ = intro.prep_seconds + intro.answer_seconds;
  return (
    <div className="card narrow viva-intro">
      <h1>A short follow-up about your coursework</h1>
      <p>
        {intro.institution_name} has asked you to answer {intro.question_count} questions about a recent piece of your
        coursework. It takes about {Math.max(5, Math.ceil((intro.question_count * perQ) / 60))} minutes. Please complete it by{" "}
        {new Date(intro.deadline_at).toLocaleString("en-GB", { dateStyle: "long", timeStyle: "short" })}.
      </p>
      <h2>How it works</h2>
      <ul>
        <li>Questions appear one at a time. You can&apos;t go back to an earlier question or see the next one early.</li>
        <li>
          Each question gives you {intro.prep_seconds > 0 ? `${duration(intro.prep_seconds)} to think, then ` : ""}
          {duration(intro.answer_seconds)} to {spoken ? "answer out loud" : "type your answer"}. The time is kept by
          our server, so it continues even if you leave the page.
        </li>
        {spoken ? (
          <li>Your answer is recorded through your microphone{intro.transcribed ? " and transcribed automatically" : ""}. Find a quiet place.</li>
        ) : (
          <li>Type your answer yourself. Pasting text is switched off.</li>
        )}
        <li>Answer in your own words. There are no trick questions; we want to hear how you worked.</li>
      </ul>
      <h2>What is recorded</h2>
      <p className="small">
        {spoken ? "Your spoken answers, " : "Your typed answers, "}how long each answer took, and whether you left this page,
        tried to paste, or opened the link on a second device. Staff on your module see this information; it is
        stored encrypted and deleted after the review. These details are only hints for the people reviewing your
        work; none of them decides anything on its own.
      </p>
      {!resuming && (
        <label className="viva-consent">
          <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
          <span>I understand what is recorded and I am ready to start.</span>
        </label>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <button className="btn" disabled={!agreed || busy} onClick={async () => { setBusy(true); await onBegin(); setBusy(false); }}>
        {busy ? "Starting…" : resuming ? "Continue" : spoken ? "Allow microphone and start" : "Start"}
      </button>
      <p className="muted small">If you need extra time or another adjustment, contact your module team before starting.</p>
    </div>
  );
}

function QuestionCard({ q, skew, stream, call, onPasteBlocked, onSaved, onError }: {
  q: Question;
  skew: number;
  stream: MediaStream | null;
  call: <T>(method: string, path: string, body?: BodyInit, contentType?: string) => Promise<T>;
  onPasteBlocked: (chars: number) => void;
  onSaved: () => void;
  onError: (msg: string | null) => void;
}) {
  const released = new Date(q.released_at).getTime();
  const answerStartsAt = released + q.prep_seconds * 1000;
  const due = new Date(q.due_at).getTime();
  const spoken = q.answer_format === "spoken";
  const [now, setNow] = useState(() => Date.now() + skew);
  const [skipPrep, setSkipPrep] = useState(false);
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const finished = useRef(false);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() + skew), 250);
    return () => clearInterval(t);
  }, [skew]);

  const answering = skipPrep || now >= answerStartsAt;
  const left = (due - now) / 1000;
  const prepLeft = (answerStartsAt - now) / 1000;

  const upload = useCallback(async (blob: Blob | null, typed?: string) => {
    if (finished.current) return;
    finished.current = true;
    setSaving(true);
    onError(null);
    try {
      if (spoken && blob && blob.size > 0) {
        await call("PUT", `/answers/${q.question.id}/audio`, blob, blob.type || "audio/webm");
      } else if (!spoken && typed && typed.trim()) {
        await call("PUT", `/answers/${q.question.id}/text`, JSON.stringify({ text: typed }), "application/json");
      } else {
        // Nothing to send: wait until the server's deadline has passed so it moves on to the next question.
        const wait = due - (Date.now() + skew) + 1500;
        if (wait > 0) await new Promise((r) => setTimeout(r, wait));
      }
    } catch (e) {
      onError((e as Error).message);
    } finally {
      onSaved();
    }
  }, [call, onError, onSaved, q.question.id, spoken, due, skew]);

  // Spoken: recording starts when the answer time starts and stops at the server deadline.
  useEffect(() => {
    if (!spoken || !answering || recorder.current || !stream) return;
    const mime = pickMime();
    const r = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    chunks.current = [];
    r.ondataavailable = (e) => e.data.size > 0 && chunks.current.push(e.data);
    r.onstop = () => upload(new Blob(chunks.current, { type: r.mimeType || "audio/webm" }));
    r.start(1000);
    recorder.current = r;
  }, [spoken, answering, stream, upload]);

  // Time is up: stop recording, or send what has been typed.
  useEffect(() => {
    if (left > 0 || finished.current) return;
    if (spoken) {
      if (recorder.current && recorder.current.state !== "inactive") recorder.current.stop();
      else upload(null);
    } else upload(null, text);
  }, [left, spoken, text, upload]);

  useEffect(() => () => {
    if (recorder.current && recorder.current.state !== "inactive") {
      recorder.current.onstop = null;
      recorder.current.stop();
    }
  }, []);

  function finishNow() {
    if (spoken) recorder.current?.stop();
    else upload(null, text);
  }

  return (
    <article className="card viva-question" aria-labelledby="viva-q">
      <p className="viva-progress">Question {q.question.position} of {q.question.total}</p>
      {q.question.passage && (
        <blockquote className="viva-passage">
          <span className="label">From your work</span>
          <p>{q.question.passage}</p>
        </blockquote>
      )}
      <h1 id="viva-q" className="viva-q-text">{q.question.text}</h1>

      {!answering && (
        <div className="viva-phase">
          <p className="viva-clock" aria-live="off">Think time: {clock(prepLeft)}</p>
          <p className="muted small">{spoken ? "Recording starts automatically when think time ends." : "The answer box opens when think time ends."}</p>
          <button className="btn secondary" onClick={() => setSkipPrep(true)}>I&apos;m ready to answer now</button>
        </div>
      )}

      {answering && spoken && (
        <div className="viva-phase">
          <p className="viva-recording"><span className="viva-dot" aria-hidden="true" /> {saving ? "Saving your answer…" : "Recording"}</p>
          <p className="viva-clock" aria-live="off">Time left: {clock(left)}</p>
          {!saving && <button className="btn" onClick={finishNow}>Finish this answer</button>}
        </div>
      )}

      {answering && !spoken && (
        <div className="viva-phase">
          <label className="field">
            <span>Your answer</span>
            <textarea rows={8} value={text} maxLength={5000} autoFocus disabled={saving}
              onChange={(e) => setText(e.target.value)}
              onPaste={(e) => { e.preventDefault(); onPasteBlocked(e.clipboardData.getData("text").length); }}
              onDrop={(e) => e.preventDefault()} />
            <small>{text.length} of 5,000 characters. Pasting is switched off.</small>
          </label>
          <p className="viva-clock" aria-live="off">Time left: {clock(left)}</p>
          <button className="btn" disabled={saving || !text.trim()} onClick={finishNow}>{saving ? "Saving…" : "Save and continue"}</button>
        </div>
      )}
    </article>
  );
}
