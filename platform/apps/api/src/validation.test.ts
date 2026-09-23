import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validKey, validateDocument } from './validation';
test('authentication rejects absent, wrong and unequal-length credentials', () => {
  assert.equal(validKey(undefined, 'secret'), false);
  assert.equal(validKey('Secret', 'secret'), false);
  assert.equal(validKey('x', 'secret'), false);
  assert.equal(validKey('secret', 'secret'), true);
});
test('accepts supported document signatures and Unicode text', () => {
  assert.equal(validateDocument('essay.TXT', Buffer.from('Unicode: \u00e9.')).extension, 'txt');
  assert.equal(validateDocument('essay.pdf', Buffer.from('%PDF-1.7\n')).extension, 'pdf');
  assert.equal(validateDocument('essay.docx', Buffer.from([0x50, 0x4b, 0x03, 0x04, 1])).extension, 'docx');
});
test('rejects mismatched, legacy, empty, oversized and invalid documents', () => {
  for (const [name, data] of [['x.pdf', Buffer.from('text')], ['x.docx', Buffer.from('text')], ['x.doc', Buffer.from('text')], ['x.txt', Buffer.from('  ')], ['x.txt', Buffer.from([255])], ['x.txt', Buffer.from([0])], ['x.pdf', Buffer.alloc(10 * 1024 * 1024 + 1)] ] as const) {
    assert.throws(() => validateDocument(name, data));
  }
});
