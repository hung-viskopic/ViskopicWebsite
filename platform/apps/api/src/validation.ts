import { timingSafeEqual } from 'node:crypto';

export type DocumentFormat = {
  extension: 'txt' | 'pdf' | 'docx';
  contentType: string;
};

const MAX_DOCUMENT_BYTES = 10 * 1024 * 1024;

export function validKey(actual: string | undefined, expected: string): boolean {
  if (!actual || !expected) return false;
  const a = Buffer.from(actual), b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function validateDocument(filename: string, content: Buffer): DocumentFormat {
  if (!content.length || content.length > MAX_DOCUMENT_BYTES) {
    throw new Error('File must contain between 1 byte and 10 MB.');
  }

  const extension = filename.toLowerCase().split('.').pop();
  if (extension === 'txt') {
    let text: string;
    try { text = new TextDecoder('utf-8', { fatal: true }).decode(content); }
    catch { throw new Error('Text files must use UTF-8 encoding.'); }
    if (!text.trim() || /[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(text)) {
      throw new Error('Text files must contain readable text.');
    }
    return { extension: 'txt', contentType: 'text/plain; charset=utf-8' };
  }

  if (extension === 'pdf') {
    if (content.subarray(0, 5).toString('ascii') !== '%PDF-') throw new Error('The selected PDF is not a valid PDF file.');
    return { extension: 'pdf', contentType: 'application/pdf' };
  }

  if (extension === 'docx') {
    const zipHeader = content.subarray(0, 4);
    const isZip = zipHeader.equals(Buffer.from([0x50, 0x4b, 0x03, 0x04])) || zipHeader.equals(Buffer.from([0x50, 0x4b, 0x05, 0x06]));
    if (!isZip) throw new Error('The selected Word document is not a valid DOCX file.');
    return { extension: 'docx', contentType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' };
  }

  throw new Error('Choose a PDF, DOCX, or UTF-8 text file. Legacy DOC files are not supported.');
}
