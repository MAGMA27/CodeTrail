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
    for (const p of ['docs', '.codex/skills', '.codex/scripts', 'AGENTS.md', 'README.md', 'codetrail-subsystems.json', 'apps/api', 'apps/web', 'contracts', 'fixtures', 'evals', 'scripts']) {
      mkdirSync(dirname(resolve(root, p)), { recursive: true });
      cpSync(resolve(source, p), resolve(root, p), { recursive: true,
        filter: path => !/(?:^|[\\/])(?:node_modules|\.venv|\.next|\.runtime|__pycache__|\.pytest_cache)(?:[\\/]|$)/.test(path) });
    }
    // A clean clone has no ignored memory-bank; the shared gate must still pass.
    assert.deepEqual(checkDocs(root), []);
    writeFileSync(resolve(root, 'apps/api/indexing.md'), '# Indexing\n\n[missing local file](absent.md)\n');
    assert.ok(checkDocs(root).some(e => e.includes('apps/api/indexing.md: broken link')));
    writeFileSync(resolve(root, 'docs/subsystems/storage.md'), '# Storage\n\n[missing](absent.md)\n');
    writeFileSync(resolve(root, 'apps/api/app/orphan.py'), '');
    writeFileSync(resolve(root, 'docs/decisions/2026-09-11-invalid.md'), '# Decision\n\nStatus: done\n');
    const errors = checkDocs(root);
    for (const fragment of ['broken link', 'unindexed subsystem', 'expected one subsystem owner', 'invalid decision status']) {
      assert.ok(errors.some(e => e.includes(fragment)), fragment);
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
