# P3-I1 impact-chain acceptance

Status: **REQUESTED UI FLOW VERIFIED; FULL NEXT CHECKLIST PARTIAL** (2026-10-01). HTTP/MCP and the browser joint flow listed by the user passed. The separate UI states for unmapped findings, request failure/retry, and truncation have not been exercised, so this record does not claim the entire broader checklist is complete.

## Fixed Run records

| Case | Project / Session / Run | Index | Candidate / Finding | Candidate root |
|---|---|---|---|---|
| redirect-query-encoding | `project-2ad24ef7fd0f40129b8ccc5cccb572d5` / `session-12741ac79e454b348da945b37e60831c` / `run-19ed439e05694c49a2f55a481bef2af0` | `index-import-efbb2859760255933134ef4a12260470-relations-v4-83b6daed08a499b1` | `candidate-1` / `finding-1` | `auth.build_login_redirect` (`sym-1327460839ef1ace92cd69805c5b09cf`, `auth.py:1-2`) |
| retry-control-flow | `project-a3add24c57e94ef69e344d2ecb0f8e19` / `session-83506ba565e04998b9c60e34c5f2357a` / `run-bccf8ac304b944bba82ca6ebe443daee` | `index-import-da9490a1d2e185b84ac05a7bd946b4f5-relations-v4-2033251b411e241b` | `candidate-1` / `finding-1` | `worker.retry_request` (`sym-5712d73b0b1332c8704a088c6219ad67`, `worker.py:5-10`) |

## Results

- HTTP and current-host MCP `get_run_impact` responses were structurally identical for both fixed Runs across callers/callees and depths 1/2 (eight pairs, `limit=40`).
- Redirect callers: one AST `calls` edge, `router.redirect_to_login` → `auth.build_login_redirect`, located at `router.py:5`; one depth-1 path. Depth 2 adds no caller. Callees are empty. Source evidence: `evidence-39f1646bcf7680de9f6439414917c43c`, `router.py:5`, hash `a424bdb5ac108fba09baa235c24b74f9a64b8bbaae43972f8d7e22abfa1f18cb`; candidate source remains `evidence-64d5424002dd59001c84c5c18332f231`, hash `719ec0997e476d390d8cdce2d65ebf7be07cd2260c0a5afac5d284367597c3d9`.
- Retry callers/callees at both depths return the mapped root only, with no static edges or paths and no truncation. Both unresolved items remain explicit: `range(attempts)` at `worker.py:6` (`call_target_not_in_snapshot`) and `send()` at `worker.py:8` (`name_rebound_in_scope`). Empty callers are not evidence of no business impact.
- Separately, ordinary fixed-index `get_neighbors` for `dynamic.dispatch` (`dynamic.py:4-6`) matched HTTP exactly: no confirmed call edges; `globals()` remains `call_target_not_in_snapshot` and `globals()[handler_name](payload)` remains `dynamic_call_target`. This module is not on the retry candidate's returned static path and is not counted as candidate impact.
- Both saved Run reports were read without mutation. Their existing `graph.static_edges` are empty; the derived read-only impact query did not write to either report, create a Run, or call a model.

## Browser joint walkthrough

- Opened the exact redirect project/session and selected the saved `run-19ed439e05694c49a2f55a481bef2af0`; candidate action opened the impact panel. Callers depth 1 and 2 showed the root plus one caller and the `auth.build_login_redirect ← router.redirect_to_login` path at `router.py:L5`; callees showed the explicit empty-static-calls state. The diagram visibly distinguishes the candidate root and caller.
- Clicked the caller graph node. The source panel showed `router.redirect_to_login`, `router.py:L4-L5`, the same project/index, and source hash prefix `a424bdb5ac10`, matching the full fixed-index hash above.
- Switched to retry project and its existing Session. The report stayed on `run-bccf8ac304b944bba82ca6ebe443daee` and its fixed index; the empty callers state retained a visible candidate root and both unresolved items. Returning to redirect and reloading restored its original Run and fixed index. No Run was created.
- Screenshots: [`impact-chain-redirect-graph.jpg`](impact-chain-redirect-graph.jpg), [`impact-chain-source.jpg`](impact-chain-source.jpg), and [`impact-chain-retry-empty.jpg`](impact-chain-retry-empty.jpg).
- The screenshots above capture the earlier narrow side panel. [`impact-canvas-prominent.jpg`](impact-canvas-prominent.jpg) separately captures the updated wide impact canvas for the retry empty-static-calls case, with the report alongside it and unresolved details visible. This desktop screenshot does not establish narrow-screen behavior or the untested UI states below.

## Remaining acceptance

- The user-requested candidate, call-chain, source, switching, refresh, and screenshot flow is verified above.
- For the broader next.md checklist, still exercise unmapped, request-error/retry, and truncation UI states in an independent test environment. No implementation defect is inferred from the remaining untested states.
