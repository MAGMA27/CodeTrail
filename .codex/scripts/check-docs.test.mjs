import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, cpSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { checkDocs } from './check-docs.mjs';

const source = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
test('gate rejects broken links, missing ownership, invalid states and sections', () => {
  const root = mkdtempSync(resolve(tmpdir(), 'codetrail-doc-gate-'));
  try {
    // A bounded copy excludes dependencies and local runtime data.
    for (const p of ['docs', '.codex/skills', 'AGENTS.md', 'apps/api/app', 'apps/web/app', 'apps/web/AGENTS.md', 'contracts', 'fixtures']) {
      mkdirSync(dirname(resolve(root, p)), { recursive: true });
      cpSync(resolve(source, p), resolve(root, p), { recursive: true });
    }
    // A clean clone has no ignored memory-bank; the shared gate must still pass.
    assert.deepEqual(checkDocs(root), []);
    writeFileSync(resolve(root, 'docs/subsystems/storage.md'), '# Storage\n\n[missing](absent.md)\n');
    writeFileSync(resolve(root, 'apps/api/app/orphan.py'), '');
    writeFileSync(resolve(root, 'docs/decisions/2026-09-11-invalid.md'), '# Decision\n\nStatus: done\n');
    const errors = checkDocs(root);
    for (const fragment of ['broken link', 'missing or empty section', 'expected one subsystem owner', 'invalid decision status']) {
      assert.ok(errors.some(e => e.includes(fragment)), fragment);
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
