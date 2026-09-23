import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

const base = process.env.API_URL || 'http://127.0.0.1:4300/api';
const headers = { 'x-access-key': process.env.DEV_ACCESS_KEY || 'local-viskopic-development-key' };
const text = 'Synthetic integration test.\r\nNo student data is included.';
const unauthorised = await fetch(`${base}/submissions`);
assert.equal(unauthorised.status, 401, 'Missing credentials must be rejected');
const invalid = new FormData();
invalid.append('file', new Blob(['not a supported document']), 'unsupported.pdf');
assert.equal((await fetch(`${base}/submissions`, { method: 'POST', headers, body: invalid })).status, 400);
const form = new FormData();
form.append('file', new Blob([text], {type:'text/plain'}), 'synthetic-smoke-test.txt');
const created = await fetch(`${base}/submissions`, { method:'POST', headers, body:form });
assert.equal(created.status, 201, await created.clone().text());
const { id } = await created.json();
for (let attempt=0; attempt<30; attempt++) {
  const response = await fetch(`${base}/submissions/${id}`, { headers });
  assert.equal(response.status, 200);
  const record = await response.json();
  assert.notEqual(record.status, 'failed', 'Worker failed extraction');
  if (record.status === 'completed') {
    assert.equal(record.extracted_text, text.replaceAll('\r\n', '\n'));
    assert.equal(record.sha256, createHash('sha256').update(text).digest('hex'));
    assert.equal(record.pipeline_version, 'document-v2');
    assert.equal(record.word_count, 8);
    assert.ok(record.events.some(e => e.event === 'Text extraction completed'));
    const list = await (await fetch(`${base}/submissions`, { headers })).json();
    assert.ok(list.some(s => s.id === id));
    console.log(`Upload, private storage retrieval, extraction and history passed: ${id}`);
    process.exit(0);
  }
  await new Promise(resolve => setTimeout(resolve, 1000));
}
throw new Error('Worker did not complete within 30 seconds');
