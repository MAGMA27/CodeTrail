# Codex-host P4-E2b holdout retest

Known-case retest on the current Codex host, not a blind or independent quality estimate and not a real model-provider evaluation. The existing P4-E1 scorer was applied only after all three first Runs and their raw records were durable.

| Cases | Succeeded | Failed | Applicable | file Hit@1 | symbol Hit@1 | location Hit@1 | Evidence legal |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 3 | 2 | 1 | 2 | 2/2 | 2/2 | 2/2 | 2/2 |

| Case | Run result | Candidate | Scoring |
|---|---|---|---|
| redirect-query-encoding | succeeded / candidate_found | `auth.build_login_redirect`, `auth.py:1-2` | file, symbol, and location hit at 1 |
| retry-control-flow | succeeded / candidate_found | `worker.retry_request`, `worker.py:5-10` | file, symbol, and location hit at 1 |
| remote-rollout-config | interrupted at 30 s before conclusion | none | failed case; no valid abstention sample |

P4-E1 baseline on the same holdout cases: 3/3 succeeded; file Hit@1 1/2; symbol Hit@1 0/2; insufficient-case false positives 1/1. This descriptive comparison is highly sample-limited; the first-attempt interruption means the primary score cannot establish abstention behavior.

For the workbench evidence-state check only, two explicitly separate supplemental Runs were recorded: `run-576b8dea46814feeb56397d2e666b1a6` also interrupted, then `run-c33918cb7e3a42939528cba7c4045cf8` succeeded with `insufficient_evidence` and a citation to `rollout/flags.py:4-8`. These are not folded into the first-attempt 3-case score; all attempts remain preserved.

All Runs' statuses, report IDs, event counts, and persisted tool-result counts matched between the current Codex host MCP and HTTP API. The workbench cold-loaded the successful supplemental evidence-insufficient report and retained its Run ID, limitation, event stream, tool result, and citation. No DeepSeek/provider calls occurred; provider identity, tokens, and cost remain unknown/not applicable. No source code or next plan was modified.

Machine-readable scoring: [codex-host-holdout.json](codex-host-holdout.json). Frozen execution details: [execution-manifest.md](execution-manifest.md). Terminal raw record: [raw-run-results.md](raw-run-results.md).
