# Raw Codex-host Run results

These are the first terminal observations from the external Runs in the execution manifest. Full immutable inputs, events, tool results, and reports remain in `.codetrail-runtime`; all three were read back through the current Codex host MCP before scoring. The third Run has no evidence-backed conclusion: it was interrupted at the 30-second wall-clock limit before submission.

| Case | Run ID | Status / outcome | Calls / source lines | Model calls | Key evidence |
|---|---|---|---:|---:|---|
| redirect-query-encoding | `run-19ed439e05694c49a2f55a481bef2af0` | succeeded / candidate_found | 2 / 2 | 0 | `auth.py:1-2`, `evidence-64d5424002dd59001c84c5c18332f231`, hash `719ec0997e476d390d8cdce2d65ebf7be07cd2260c0a5afac5d284367597c3d9` |
| retry-control-flow | `run-bccf8ac304b944bba82ca6ebe443daee` | succeeded / candidate_found | 2 / 6 | 0 | `worker.py:5-10`, `evidence-e0fb3d085a218125ef93bbbef4fa0233`, hash `34f78f9aa8902f516f548251712d9fe7e7a8ef75e34707aaea7f7608298ea218` |
| remote-rollout-config | `run-193d3c2dd3ce4b90b617b445a8822e0f` | interrupted / interrupted | 4 / 8 | 0 | source reads were persisted (`rollout/flags.py:4-8`, `evidence-2ea76da544aab2597ee281896f0a69a7`; `11-13`, `evidence-14eab60461724ac880ea8ad512621bc5`) but no conclusion was submitted before wall-clock expiry |

## Run details

- `redirect-query-encoding`: `report-run-19ed439e05694c49a2f55a481bef2af0`, terminal sequence 7, succeeded at `2026-09-30T16:20:23.038682Z`. The submitted finding says the redirect interpolates `next_path` without query encoding. The candidate is `auth.build_login_redirect` (`sym-1327460839ef1ace92cd69805c5b09cf`).
- `retry-control-flow`: `report-run-bccf8ac304b944bba82ca6ebe443daee`, terminal sequence 7, succeeded at `2026-09-30T16:21:02.570702Z`. The submitted finding says `retry_request` immediately raises inside the transient-error handler, exiting the retry loop. The candidate is `worker.retry_request` (`sym-5712d73b0b1332c8704a088c6219ad67`).
- `remote-rollout-config`: `report-run-193d3c2dd3ce4b90b617b445a8822e0f`, terminal sequence 10, interrupted at `2026-09-30T16:21:46.243620Z` because the external Run exceeded its wall-clock budget. Four tool calls and eight source lines were consumed. Source indicates an environment override or a dynamic `remote_client.fetch` fallback, but no conclusion was submitted. This terminal interruption is retained as a failure; it was not retried.

All Runs used the same budgets recorded in `execution-manifest.md`. Provider/model/token/cost fields are null/unknown, not zero. These are known-case retests, not blind or independent evaluation results.

Post-run scoring through the existing evaluator is saved in `codex-host-holdout.json`; full host/HTTP records are also materialized under `runs/<case>/`. The interrupted third Run remains a failed case in the denominator, not an abstention result.

## Explicit supplemental Runs

The original three-case score above remains first-attempt-only. Per the plan's distinct-Run rule, two separate attempts were added for the remote case so the workbench could display an actual insufficient-evidence report; neither overwrites or rescues the first-attempt failure.

| Run ID | Status / outcome | Calls / source lines | Report |
|---|---|---:|---|
| `run-576b8dea46814feeb56397d2e666b1a6` | interrupted / interrupted; submit rejected after 30 s elapsed | 1 / 5 | `report-run-576b8dea46814feeb56397d2e666b1a6` |
| `run-c33918cb7e3a42939528cba7c4045cf8` | succeeded / insufficient_evidence | 1 / 5 | `report-run-c33918cb7e3a42939528cba7c4045cf8` |

Both supplemental Runs have separate status, report, event, and tool-result JSON under `runs/remote-rollout-config/attempts/<run-id>/`; both identities and outcomes were cross-checked over current host MCP and HTTP. The successful supplemental report cites `rollout/flags.py:4-8` (`evidence-2ea76da544aab2597ee281896f0a69a7`) and does not claim the production cause is known. It is a UI/evidence-state acceptance record only, excluded from the first-attempt score above.
