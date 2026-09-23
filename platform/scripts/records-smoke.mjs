import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const base = process.env.API_URL || 'http://127.0.0.1:4300/api';
const key = process.env.DEV_ACCESS_KEY || 'local-viskopic-development-key';
async function call(path, method = 'GET', body, status = 200) {
  const response = await fetch(base + path, { method, headers: { 'x-access-key': key, ...(body ? { 'content-type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  const result = await response.json();
  assert.equal(response.status, status, JSON.stringify(result));
  return result;
}
async function upload(student, status = 201) {
  const form = new FormData();
  // Above the old worker's 1 MiB read boundary; checks the advertised intake limit.
  form.append('file', new Blob(['Synthetic student writing for pipeline verification.\n'.repeat(24000)]), 'synthetic-student-essay.txt');
  const response = await fetch(base + '/submissions?studentId=' + student, { method: 'POST', headers: { 'x-access-key': key }, body: form });
  const result = await response.json();
  assert.equal(response.status, status, JSON.stringify(result));
  return result;
}
assert.equal((await fetch(base + '/courses')).status, 401);
await call('/courses', 'POST', { name: ' ' }, 400);
const before = await call('/submissions?studentId=unassigned');
const course = (await call('/courses')).find(c => c.name === 'Demo cohort (synthetic)') || await call('/courses', 'POST', { name: 'Demo cohort (synthetic)' }, 201);
const student = (await call(`/courses/${course.id}/students`)).find(s => s.reference === 'DEMO-001') || await call(`/courses/${course.id}/students`, 'POST', { reference: 'DEMO-001', name: '' }, 201);
await call(`/courses/${course.id}/students`, 'POST', { reference: 'DEMO-001' }, 409);
await call(`/courses/${randomUUID()}/students`, 'POST', { reference: 'MISSING' }, 404);
await call(`/students/${student.id}`, 'PATCH', { reference: 'DEMO-001', name: 'Synthetic student' });
await call(`/students/${student.id}`, 'PATCH', { archived: 'yes' }, 400);
await call(`/students/${student.id}`, 'PATCH', { archived: true });
await upload(student.id, 409);
await call(`/students/${student.id}`, 'PATCH', { archived: false });
await call(`/courses/${course.id}`, 'PATCH', { archived: true });
await call(`/courses/${course.id}/students`, 'POST', { reference: 'BLOCKED' }, 409);
await upload(student.id, 409);
await call(`/courses/${course.id}`, 'PATCH', { archived: false, name: 'Demo cohort (synthetic)' });
const doc = (await call(`/submissions?studentId=${student.id}`)).find(d => d.filename === 'synthetic-student-essay.txt') || await upload(student.id);
let detail;
for (let attempt = 0; attempt < 45; attempt++) {
  detail = await call('/submissions/' + doc.id);
  if (detail.status === 'completed' || detail.status === 'failed') break;
  await new Promise(resolve => setTimeout(resolve, 1000));
}
assert.equal(detail.status, 'completed');
assert.equal(detail.word_count, 144000);
assert.equal(detail.student_id, student.id);
assert.equal((await call(`/submissions?studentId=${student.id}`)).length, 1);
assert.equal((await call(`/courses/${course.id}/students`))[0].document_count, 1);
await call(`/students/${student.id}`, 'DELETE', undefined, 409);
await call(`/courses/${course.id}`, 'DELETE', undefined, 409);
await call(`/submissions/${doc.id}`, 'PATCH', { studentId: randomUUID() }, 404);
await call(`/submissions/${doc.id}`, 'PATCH', { studentId: null });
assert.equal((await call(`/submissions?studentId=${student.id}`)).length, 0);
assert.equal((await call('/submissions?studentId=unassigned')).length, before.length + 1);
await call(`/submissions/${doc.id}`, 'PATCH', { studentId: student.id });
assert.equal((await call('/submissions?studentId=unassigned')).length, before.length);
await call(`/students/${student.id}`);
const events = await call(`/activity/${student.id}`);
for (const action of ['Student record created', 'Student details updated', 'Student archived', 'Student restored', 'Student record viewed']) assert.ok(events.some(e => e.action === action), action);
const emptyCourse = await call('/courses', 'POST', { name: 'Temporary lifecycle test' }, 201);
const emptyStudent = await call(`/courses/${emptyCourse.id}/students`, 'POST', { reference: 'TEMP' }, 201);
await call(`/students/${emptyStudent.id}`, 'DELETE');
await call(`/students/${emptyStudent.id}`, 'GET', undefined, 404);
await call(`/courses/${emptyCourse.id}`, 'DELETE');
console.log('PASS: authentication, validation, record lifecycle, archive restrictions, >1 MiB extraction, assignment, counts, preservation, activity, and empty-record deletion.');
console.log(`Synthetic demo retained: course ${course.id}, student ${student.id}, submission ${doc.id}`);
