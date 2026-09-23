import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Archive, ArchiveRestore, CheckCircle2, ChevronRight, Clock3, FileText, Folder, FolderPlus, History, Inbox, LogOut, Pencil, RefreshCw, Search, Trash2, Upload, UserPlus, Users, X } from 'lucide-react';
import './style.css';

type Course = { id: string; name: string; archived: boolean; student_count: number };
type Student = { id: string; course_id: string; reference: string; name: string; archived: boolean; document_count: number };
type Submission = { id: string; student_id: string | null; filename: string; status: string; word_count: number | null; created_at: string; error: string | null };
type Detail = Submission & { extracted_text: string | null; sha256: string; pipeline_version: string | null; events: { event: string; created_at: string }[] };
type Event = { id: string; action: string; actor: string; created_at: string };
type Modal = { kind: 'course'; edit: boolean } | { kind: 'student'; edit: boolean } | { kind: 'delete'; entity: 'course' | 'student'; id: string; name: string } | { kind: 'move'; id: string };
const date = (value: string) => new Date(value).toLocaleString();

function App() {
  const [key, setKey] = useState('');
  const [draftKey, setDraftKey] = useState('');
  const [courses, setCourses] = useState<Course[]>([]);
  const [students, setStudents] = useState<Student[]>([]);
  const [courseId, setCourseId] = useState<string | null>(null);
  const [studentId, setStudentId] = useState<string | null>(null);
  const [unassigned, setUnassigned] = useState(false);
  const [items, setItems] = useState<Submission[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [activity, setActivity] = useState<Event[] | null>(null);
  const [activityId, setActivityId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [modalError, setModalError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [revision, setRevision] = useState(0);
  const [modal, setModal] = useState<Modal | null>(null);
  const [name, setName] = useState('');
  const [reference, setReference] = useState('');
  const [targetCourse, setTargetCourse] = useState('');
  const [targetStudent, setTargetStudent] = useState('');
  const [targets, setTargets] = useState<Student[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const course = courses.find(c => c.id === courseId);
  const student = students.find(s => s.id === studentId);
  const documents = unassigned || !!studentId;
  const archived = course?.archived || student?.archived;
  const selectedStatus = items.find(i => i.id === selected)?.status;

  async function request(path: string, init: RequestInit = {}, credential = key) {
    const response = await fetch('/api' + path, { ...init, headers: { ...init.headers, 'x-access-key': credential } });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(response.status >= 500 ? 'The application service is unavailable. Please try again shortly.' : body.message || `Request failed (${response.status}).`);
    }
    return response.json();
  }
  function json(method: string, body: unknown): RequestInit { return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }; }
  function navigate(nextCourse: string | null = null, nextStudent: string | null = null, inbox = false) {
    setCourseId(nextCourse); setStudentId(nextStudent); setUnassigned(inbox); setSelected(null); setDetail(null); setItems([]); setFilter(''); setError(''); setActivityId(null); setActivity(null);
    if (nextCourse !== courseId) setStudents([]);
  }
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController(); let active = true;
    async function load() {
      try {
        const opts = { signal: controller.signal };
        const [cs, ss, ds] = await Promise.all([request('/courses', opts), courseId ? request(`/courses/${courseId}/students`, opts) : [], documents ? request('/submissions?studentId=' + (studentId || 'unassigned'), opts) : []]);
        if (active) { setCourses(cs); setStudents(ss); setItems(ds); }
      } catch (e) { if (active) setError((e as Error).message); }
      finally { if (active) setLoading(false); }
    }
    setLoading(true); void load(); const timer = window.setInterval(load, 3000);
    return () => { active = false; controller.abort(); clearInterval(timer); };
  }, [key, courseId, studentId, unassigned, revision]);
  useEffect(() => {
    if (!key || !studentId) return;
    const controller = new AbortController();
    void request('/students/' + studentId, { signal: controller.signal }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [key, studentId]);
  useEffect(() => {
    if (!key || !selected) { setDetail(null); return; }
    const controller = new AbortController();
    void request('/submissions/' + selected, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setDetail(value); }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [key, selected, selectedStatus, revision]);
  useEffect(() => {
    if (!activityId) { setActivity(null); return; }
    const controller = new AbortController();
    void request('/activity/' + activityId, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setActivity(value); }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [activityId, revision]);
  useEffect(() => { if (modal) dialog.current?.showModal(); else dialog.current?.close(); }, [modal]);
  useEffect(() => {
    setTargets([]); setTargetStudent('');
    if (!targetCourse || modal?.kind !== 'move') return;
    const controller = new AbortController();
    void request(`/courses/${targetCourse}/students`, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setTargets(value); }).catch(e => { if (!controller.signal.aborted) setModalError(e.message); });
    return () => controller.abort();
  }, [targetCourse, modal]);
  function openForm(kind: 'course' | 'student', edit = false) {
    setName(edit ? (kind === 'course' ? course?.name || '' : student?.name || '') : '');
    setReference(edit ? student?.reference || '' : ''); setModalError(''); setModal({ kind, edit });
  }
  async function mutate(path: string, init: RequestInit) {
    setBusy(true); setError('');
    try { await request(path, init); setRevision(v => v + 1); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function save(e: React.FormEvent) {
    e.preventDefault(); if (!modal) return;
    setBusy(true); setModalError('');
    try {
      if (modal.kind === 'course') {
        const result = await request(modal.edit ? '/courses/' + courseId : '/courses', json(modal.edit ? 'PATCH' : 'POST', { name }));
        if (!modal.edit) navigate(result.id);
      } else if (modal.kind === 'student') {
        const result = await request(modal.edit ? '/students/' + studentId : `/courses/${courseId}/students`, json(modal.edit ? 'PATCH' : 'POST', { reference, name }));
        if (!modal.edit) navigate(courseId, result.id);
      } else if (modal.kind === 'delete') {
        await request(`/${modal.entity === 'course' ? 'courses' : 'students'}/${modal.id}`, { method: 'DELETE' });
        navigate(modal.entity === 'student' ? courseId : null);
      } else {
        await request('/submissions/' + modal.id, json('PATCH', { studentId: targetStudent || null })); setSelected(null); setDetail(null);
      }
      setModal(null); setRevision(v => v + 1);
    } catch (e) { setModalError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function upload(file?: File) {
    if (!file) return;
    setBusy(true); setError('');
    try {
      const form = new FormData(); form.append('file', file);
      const result = await request('/submissions' + (studentId ? '?studentId=' + studentId : ''), { method: 'POST', body: form });
      setSelected(result.id); setDetail(null); setRevision(v => v + 1);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); if (input.current) input.current.value = ''; }
  }
  const icon = (title: string, child: React.ReactNode, action: () => void, disabled = busy) => <button type="button" className="icon" title={title} aria-label={title} onClick={action} disabled={disabled}>{child}</button>;
  const recordActions = () => {
    const entity = studentId ? 'student' : 'course'; const id = studentId || courseId!;
    const isArchived = studentId ? student?.archived : course?.archived;
    return <>{icon('Edit ' + entity, <Pencil size={17}/>, () => openForm(entity, true))}{icon(isArchived ? 'Restore ' + entity : 'Archive ' + entity, isArchived ? <ArchiveRestore size={17}/> : <Archive size={17}/>, () => void mutate(`/${entity === 'student' ? 'students' : 'courses'}/${id}`, json('PATCH', { archived: !isArchived })))}{icon('Delete ' + entity, <Trash2 size={17}/>, () => { setModalError(''); setModal({ kind: 'delete', entity, id, name: student?.reference || course?.name || '' }); })}{icon('View activity', <History size={17}/>, () => { setActivity(null); setActivityId(id); setRevision(v => v + 1); })}</>;
  };
  if (!key) return <main className="entry"><img src="/logo_favicon.svg" alt="Viskopic" width="60" height="60"/><h1>Viskopic</h1><p>Secure research workspace</p><form onSubmit={async e => { e.preventDefault(); setBusy(true); setError(''); try { await request('/courses', {}, draftKey); setKey(draftKey); setDraftKey(''); } catch(e) { setError((e as Error).message); } finally { setBusy(false); } }}><label htmlFor="key">Workspace access key</label><input id="key" autoComplete="off" type="password" required value={draftKey} onChange={e => setDraftKey(e.target.value)}/><button disabled={busy}>{busy ? 'Connecting...' : 'Open workspace'}</button></form>{error && <p role="alert" className="error">{error}</p>}</main>;
  const matches = (text: string) => text.toLowerCase().includes(filter.toLowerCase());
  const visibleCourses = courses.filter(c => (showArchived || !c.archived) && matches(c.name));
  const visibleStudents = students.filter(s => (showArchived || !s.archived) && matches(s.reference + ' ' + s.name));
  const visibleItems = items.filter(i => matches(i.filename));
  return <>
    <header><div className="brand"><img src="/logo_favicon.svg" alt="" width="32" height="32"/><strong>Viskopic</strong><span>Research workspace</span></div>{icon('Lock workspace', <LogOut size={19}/>, () => { setKey(''); setCourses([]); setStudents([]); navigate(); }, false)}</header>
    <main className="workspace">
      <nav className="navigation" aria-label="Workspace"><button className={!unassigned ? 'nav-button active' : 'nav-button'} onClick={() => navigate()}><Folder size={17}/>Courses</button><button className={unassigned ? 'nav-button active' : 'nav-button'} onClick={() => navigate(null, null, true)}><Inbox size={17}/>Unassigned</button></nav>
      {courseId && <nav className="breadcrumbs" aria-label="Breadcrumb"><button onClick={() => navigate()}>Courses</button><ChevronRight size={14}/><button onClick={() => navigate(courseId)}>{course?.name || 'Course'}</button>{studentId && <><ChevronRight size={14}/><span>{student?.reference || 'Student record'}</span></>}</nav>}
      <div className="heading"><div><p className="eyebrow">{studentId ? 'STUDENT RECORD' : courseId ? 'COURSE / COHORT' : 'DOCUMENT INTAKE'}</p><h1>{unassigned ? 'Unassigned documents' : studentId ? student?.reference || 'Loading...' : courseId ? course?.name || 'Loading...' : 'Courses'}</h1>{student?.name && <p className="student-name">{student.name}</p>}{archived && <p className="archive-label"><Archive size={14}/>Archived</p>}</div><div className="actions">{icon('Refresh workspace', <RefreshCw size={18}/>, () => { setError(''); setRevision(v => v + 1); })}{courseId && recordActions()}{documents ? <button disabled={busy || !!archived} onClick={() => input.current?.click()}><Upload size={17}/>{busy ? 'Working...' : 'Upload document'}</button> : <button disabled={busy || !!archived} onClick={() => openForm(courseId ? 'student' : 'course')}>{courseId ? <UserPlus size={17}/> : <FolderPlus size={17}/>}{courseId ? 'New student' : 'New course'}</button>}</div></div>
      <input ref={input} type="file" hidden accept=".pdf,.docx,.txt" onChange={e => void upload(e.target.files?.[0])}/>
      <div className="summary">{documents ? <><span><strong>{items.length}</strong>Documents</span><span><strong>{items.filter(i => i.status === 'completed').length}</strong>Extracted</span><small>PDF, DOCX, TXT / up to 10 MB</small></> : <><span><strong>{courseId ? students.filter(s => !s.archived).length : courses.filter(c => !c.archived).length}</strong>{courseId ? 'Active student records' : 'Active courses'}</span><span><strong>{courseId ? students.reduce((n, s) => n + s.document_count, 0) : courses.reduce((n, c) => n + c.student_count, 0)}</strong>{courseId ? 'Documents' : 'Student records'}</span></>}</div>
      {error && <p role="alert" className="error">{error}</p>}
      <div className="toolbar"><div className="search"><Search size={17}/><input aria-label="Search workspace" placeholder={documents ? 'Search documents' : courseId ? 'Search student ID or name' : 'Search courses'} value={filter} onChange={e => setFilter(e.target.value)}/></div>{!documents && <label className="checkbox"><input type="checkbox" checked={showArchived} onChange={e => setShowArchived(e.target.checked)}/>Show archived</label>}<span aria-live="polite">{loading ? 'Updating...' : ''}</span></div>
      {!documents ? <section aria-label={courseId ? 'Student records' : 'Courses'} className="record-list">
        <div className="record-head"><span>{courseId ? 'Student reference / name' : 'Course / cohort'}</span><span>{courseId ? 'Documents' : 'Students'}</span><span/></div>
        {(courseId ? visibleStudents : visibleCourses).map(record => <button className="record-row" key={record.id} onClick={() => 'reference' in record ? navigate(courseId, record.id) : navigate(record.id)}><span className="filename">{'reference' in record ? <Users size={21}/> : <Folder size={21}/>}<span><strong>{'reference' in record ? record.reference : record.name}</strong>{'reference' in record && record.name && <small>{record.name}</small>}{record.archived && <small>Archived</small>}</span></span><span>{'document_count' in record ? record.document_count : record.student_count}</span><ChevronRight size={17}/></button>)}
        {!(courseId ? visibleStudents : visibleCourses).length && <div className="empty"><Folder size={36}/><h2>{filter ? 'No matches' : courseId ? 'No student records' : 'No courses'}</h2>{!filter && !archived && <button onClick={() => openForm(courseId ? 'student' : 'course')}>{courseId ? <UserPlus size={17}/> : <FolderPlus size={17}/>} {courseId ? 'New student' : 'New course'}</button>}</div>}
      </section> : <div className={'content ' + (selected ? 'has-detail' : '')}><section aria-label="Submission list" className="list"><div className="row row-head"><span>Document</span><span>Status</span><span>Words</span></div>{visibleItems.map(i => <button className={'row ' + (selected === i.id ? 'selected' : '')} key={i.id} onClick={() => { if (selected !== i.id) setDetail(null); setSelected(i.id); }}><span className="filename"><FileText size={19}/><span>{i.filename}<small>{date(i.created_at)}</small></span></span><span className={'status ' + i.status}>{i.status === 'completed' ? <CheckCircle2 size={13}/> : <Clock3 size={13}/>} {i.status}</span><span>{i.word_count ?? '--'}</span></button>)}{!visibleItems.length && <div className="empty"><FileText size={38}/><h2>{filter ? 'No matching documents' : 'No documents yet'}</h2></div>}</section>
      {selected && <aside aria-label="Submission details"><div className="detail-head"><h2>{detail?.filename || 'Loading document...'}</h2>{icon('Close details', <X size={18}/>, () => { setSelected(null); setDetail(null); }, false)}</div>{detail && <><div className="detail-actions"><button className="secondary" disabled={busy} onClick={() => { setTargetCourse(''); setTargetStudent(''); setModalError(''); setModal({ kind: 'move', id: detail.id }); }}><Folder size={16}/>Move document</button>{icon('Document activity', <History size={17}/>, () => { setActivity(null); setActivityId(detail.id); setRevision(v => v + 1); })}</div><p className={'status ' + detail.status}>{detail.status}</p><h3>Processing history</h3><ol className="history">{detail.events.map((e, i) => <li key={i}>{e.event}<small>{date(e.created_at)}</small></li>)}</ol>{detail.error && <p className="error">{detail.error}</p>}<h3>Extracted text</h3><pre>{detail.extracted_text ?? 'Waiting for extraction.'}</pre><p className="note">Authorship analysis has not run.</p><details><summary>Document provenance</summary><p>Pipeline: {detail.pipeline_version ?? 'Pending'}</p><p>SHA-256</p><code>{detail.sha256}</code></details></>}</aside>}</div>}
      {activityId && <section className="activity" aria-label="Activity history"><div className="detail-head"><h2>Activity history</h2>{icon('Close activity', <X size={18}/>, () => setActivityId(null), false)}</div>{activity === null ? <p>Loading...</p> : activity.length ? <ol className="history">{activity.map(event => <li key={event.id}>{event.action}<small>{date(event.created_at)} / {event.actor}</small></li>)}</ol> : <p>No activity recorded.</p>}</section>}
    </main>
    <dialog ref={dialog} onCancel={e => { if (busy) e.preventDefault(); else setModal(null); }}><form onSubmit={save}><div className="detail-head"><h2>{modal?.kind === 'delete' ? 'Delete record' : modal?.kind === 'move' ? 'Move document' : `${modal?.edit ? 'Edit' : 'New'} ${modal?.kind === 'course' ? 'course / cohort' : 'student record'}`}</h2>{icon('Close dialog', <X size={18}/>, () => setModal(null))}</div>
      {modal?.kind === 'course' && <label>Course or cohort name<input autoFocus required maxLength={160} value={name} onChange={e => setName(e.target.value)}/></label>}
      {modal?.kind === 'student' && <><label>Student reference<input autoFocus required maxLength={160} placeholder="e.g. STU-2026-001" value={reference} onChange={e => setReference(e.target.value)}/></label><label>Display name (optional)<input maxLength={160} value={name} onChange={e => setName(e.target.value)}/></label></>}
      {modal?.kind === 'delete' && <p>Delete <strong>{modal.name}</strong>? Only empty records can be deleted. This cannot be undone.</p>}
      {modal?.kind === 'move' && <><label>Destination course<select value={targetCourse} onChange={e => setTargetCourse(e.target.value)}><option value="">Unassigned documents</option>{courses.filter(c => !c.archived).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>{targetCourse && <label>Student record<select required value={targetStudent} onChange={e => setTargetStudent(e.target.value)}><option value="">Select student</option>{targets.filter(s => !s.archived).map(s => <option key={s.id} value={s.id}>{s.reference}{s.name ? ' - ' + s.name : ''}</option>)}</select></label>}</>}
      {modalError && <p className="error" role="alert">{modalError}</p>}<div className="modal-actions"><button type="button" className="secondary" disabled={busy} onClick={() => setModal(null)}>Cancel</button><button className={modal?.kind === 'delete' ? 'danger' : ''} disabled={busy || (modal?.kind === 'move' && !!targetCourse && !targetStudent)}>{busy ? 'Saving...' : modal?.kind === 'delete' ? 'Delete record' : modal?.kind === 'move' ? 'Move document' : 'Save'}</button></div>
    </form></dialog>
  </>;
}
createRoot(document.getElementById('root')!).render(<App/>);
