# P4-E2b current Codex host — holdout execution manifest

Frozen before starting any external Issue Run on 2026-10-01 (Asia/Shanghai). This is a known-case retest; no blind-evaluation or independent-quality claim is made. Scoring annotations and historical candidate answers were not opened before the three Runs were completed.

## Method and limits

- Dataset: `codetrail-p4-e1`, `p4-e1-v1`, split `holdout`; execution: current Codex host using its registered CodeTrail MCP tools and persistent external Runs.
- Code revision: `a63c76e9d0b803b726cdb491f000045bb1a6bbd6`; working tree dirty before execution. Existing user changes were not included in the isolated source projects.
- Input construction: one saved human Session message containing `title + newline + body`; the external Run receives the exact frozen Issue title/body/mentioned paths. Each Session is fixed to its case's only index.
- Shared API/MCP data directory: `.codetrail-runtime`. API local-import allowlist was limited to `evals/.runtime/p4-e2b-codex-host-20261001/sources` for project registration; the source copies themselves are Git-ignored and contain manifest-listed source files only.
- Identical Run budget for all cases: 8 tool calls, 30 seconds, 200 lines per source read, 800 total source lines, 50,000 output characters, 100 events; other MCP defaults unchanged. No real model-provider API or repository code execution.
- Codex model/token usage and cost are unavailable from the persisted Run contract; record as unknown, not zero. A failed first attempt, if any, remains in the result ledger and is not replaced by a retry.
- Run IDs and actual terminal outcomes are recorded in `raw-run-results.md` after each result is read back from the current host and HTTP API.

## Frozen cases

| Case | Issue ID | Raw Issue JSON SHA-256 | Isolated source files | Source manifest/snapshot hash | Project / fixed index / Session |
| --- | --- | --- | --- | --- | --- |
| redirect-query-encoding | `eval-issue-redirect-query-encoding` | `14577ef03d3bc4151cb33b9395c2f2036f8e7eee61e992d68e947f07d0a98067` | `auth.py`, `router.py`, `urls.py` | `83b6daed08a499b1a5981032b461312d5e10d137f8a1af833c9b703d43cded6b` | `project-2ad24ef7fd0f40129b8ccc5cccb572d5` / `index-import-efbb2859760255933134ef4a12260470-relations-v4-83b6daed08a499b1` / `session-12741ac79e454b348da945b37e60831c` |
| retry-control-flow | `eval-issue-retry-control-flow` | `3b6e6f0c957de2c72931da67e639584f31d1024100dea73321d7097a0c9131c2` | `dynamic.py`, `worker.py` | `2033251b411e241b44b310b8850bcb79cede9a26b91cf4e1ca9389af4739647d` | `project-a3add24c57e94ef69e344d2ecb0f8e19` / `index-import-da9490a1d2e185b84ac05a7bd946b4f5-relations-v4-2033251b411e241b` / `session-83506ba565e04998b9c60e34c5f2357a` |
| remote-rollout-config | `eval-issue-remote-rollout-config` | `243e891406d168772071e7bd4b82d1e6b08465687ebbf9bd9777d192b4804147` | `rollout/client.py`, `rollout/flags.py`, `rollout/region.py` | `ddddb739e050a2fc3f3c7281eba59a25b9d5f17b1ccf00ae8d8b18afcf2be78f` | `project-bde03f8eac184bb585f6b5b4affe11ec` / `index-import-8bcff9ee7e029f141fa92ad0dcb40447-relations-v4-ddddb739e050a2fc` / `session-162eb27cd13b4ee3bfb7e1f3c0d45707` |

All three indexes were returned by the current Codex MCP as `ready`, with zero index issues and snapshot hashes exactly matching the frozen manifests. The copied file sets and SHA-256 values were checked without displaying source contents. The full MCP and HTTP records remain in the shared managed database; the gold-free terminal result ledger was saved before annotations were loaded.

After all three Runs reached terminal states and the raw ledger was saved, the holdout annotations were loaded by the existing scorer. Scored rows and exact HTTP report/event/tool-result responses are now saved with the raw ledger and were not used to alter the Run investigation or produce any retry. The full scored summary is in `codex-host-holdout.json`.

After the first-attempt score was frozen, two additional, distinct remote-case Runs were recorded (never an in-place retry): one timed out at submission and one succeeded with `insufficient_evidence`. Their records are separated under `runs/remote-rollout-config/attempts/` and are excluded from the original first-attempt three-case score. The successful supplemental report was used for the post-restart workbench evidence-insufficient acceptance check.
