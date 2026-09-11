import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { resolve, relative, dirname, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const headings = ['状态', '职责与范围', '接口与依赖', '数据与生命周期', '错误与限制', '验证', '相关决策'];
function walk(root) {
  if (!existsSync(root)) return [];
  return readdirSync(root, { withFileTypes: true }).flatMap(e =>
    e.isDirectory() ? walk(resolve(root, e.name)) : [resolve(root, e.name)]);
}

// Check only local files and declared structure; semantic claims need review.
export function checkDocs(root, { local = false } = {}) {
  const errors = [];
  const read = p => readFileSync(p, 'utf8');
  const name = p => relative(root, p).replaceAll('\\', '/');
  const required = ['AGENTS.md', 'docs/development.md', 'docs/subsystems/README.md',
    'docs/templates/subsystem.md', 'docs/templates/decision.md', 'docs/templates/review.md',
    ...['design-change', 'sync-subsystem', 'review-subsystem'].map(s => `.codex/skills/codetrail-${s}/SKILL.md`)];
  for (const p of required) if (!existsSync(resolve(root, p))) errors.push(`missing required file: ${p}`);
  const docs = [...walk(resolve(root, 'docs')), ...walk(resolve(root, '.codex/skills')),
    ...(existsSync(resolve(root, 'AGENTS.md')) ? [resolve(root, 'AGENTS.md')] : []),
    ...(local ? walk(resolve(root, 'memory-bank')) : [])].filter(p => p.endsWith('.md'));
  for (const p of docs) {
    const content = read(p);
    const prose = content.replace(/^```[^\n]*\n[\s\S]*?^```\s*$/gm, '');
    for (const m of prose.matchAll(/\[[^\]\n]*\]\(([^)\n]+)\)/g)) {
      const target = m[1].trim().replace(/^<|>$/g, '').split('#')[0];
      if (!target || /^[a-z][a-z0-9+.-]*:/i.test(target)) continue;
      let decoded;
      try { decoded = decodeURIComponent(target); } catch { errors.push(`${name(p)}: malformed link ${target}`); continue; }
      const resolved = resolve(dirname(p), decoded);
      if (!local && name(resolved).startsWith('memory-bank/')) continue;
      if (!existsSync(resolved)) errors.push(`${name(p)}: broken link ${target}`);
    }
    if (name(p).startsWith('docs/') || name(p).startsWith('.codex/')) {
      if (!content.endsWith('\n') || /\n\s*\n$/.test(content)) errors.push(`${name(p)}: expected one trailing newline`);
    }
    if (p.endsWith('SKILL.md')) {
      const front = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
      const skillName = front?.[1].match(/^name: (.+)$/m)?.[1].trim();
      if (skillName !== dirname(p).split(/[\\/]/).at(-1) || !/^description: .+/m.test(front?.[1] ?? '')) errors.push(`${name(p)}: invalid skill metadata`);
    }
    if (name(p).startsWith('docs/subsystems/') && !p.endsWith('README.md')) {
      for (const h of headings) {
        const body = content.split(`## ${h}\n`)[1]?.split('\n## ')[0].trim();
        if (!body) errors.push(`${name(p)}: missing or empty section ${h}`);
      }
    }
    if (name(p).startsWith('docs/decisions/') && !p.endsWith('README.md')) {
      const status = content.match(/^Status: (\S+)$/m)?.[1];
      if (!['proposed', 'implemented', 'rejected', 'superseded'].includes(status)) errors.push(`${name(p)}: invalid decision status`);
      for (const h of ['问题', '决定', '替代方案', '验收与证据', '后果']) {
        if (!content.split(`## ${h}\n`)[1]?.split('\n## ')[0].trim()) errors.push(`${name(p)}: missing decision section ${h}`);
      }
      if (status === 'superseded' && !/\]\([^)]*\d{4}-\d{2}-\d{2}-[^)]+\.md\)/.test(content)) errors.push(`${name(p)}: missing successor link`);
    }
  }
  const index = resolve(root, 'docs/subsystems/README.md');
  const owners = [];
  const indexedPages = new Set();
  if (existsSync(index)) {
    for (const line of read(index).split('\n')) {
      if (!/^\|.*\]\(/.test(line)) continue;
      const cells = line.split('|').map(s => s.trim());
      const page = cells[2]?.match(/\]\(([^)]+)\)/)?.[1];
      if (page) indexedPages.add(resolve(dirname(index), page));
      for (const owner of (cells[3] ?? '').split(',').map(s => s.trim()).filter(Boolean)) {
        if (owner.startsWith('/') || owner.includes('..') || owner.includes('\\') || !existsSync(resolve(root, owner))) errors.push(`invalid owner: ${owner}`);
        else owners.push(owner);
      }
    }
  }
  for (const p of walk(resolve(root, 'docs/subsystems')).filter(p => p.endsWith('.md') && !p.endsWith('README.md'))) {
    if (!indexedPages.has(p)) errors.push(`unindexed subsystem: ${name(p)}`);
  }
  for (const p of ['apps/api/app', 'apps/web/app', 'contracts', 'fixtures'].flatMap(d => walk(resolve(root, d)))) {
    if (name(p).includes('/__pycache__/') || !['.py', '.ts', '.tsx', '.css', '.json'].includes(extname(p))) continue;
    const matches = owners.filter(o => o.endsWith('/') ? name(p).startsWith(o) : name(p) === o);
    if (matches.length !== 1) errors.push(`${name(p)}: expected one subsystem owner, found ${matches.length}`);
  }
  return errors;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const errors = checkDocs(root, { local: process.argv.includes('--local') });
  for (const error of errors) console.error(error);
  console.log(errors.length ? `docs:check FAIL (${errors.length})` : 'docs:check PASS (structure, file links, ownership; semantics not checked)');
  process.exitCode = errors.length ? 1 : 0;
}
