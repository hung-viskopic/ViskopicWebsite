import { cp, mkdir, rm } from 'node:fs/promises';

const output = new URL('../dist-marketing/', import.meta.url);
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });

for (const filename of ['index.html', 'style.css', 'logo_favicon.svg', 'viskopic-wordmark.png']) {
  await cp(new URL(`../${filename}`, import.meta.url), new URL(filename, output));
}
