# Three fresh synthetic Issue Runs — 2026-10-07

Three small, self-authored source snapshots exercise one local defect, one cross-module defect, and one case where the available source cannot support attribution. The prior six evaluation issues and previously exposed host examples were excluded. These cases are process-isolated fresh-agent checks, not evidence of external-repository generalization or model training-set novelty.

Each `sources/<case>/` directory is the complete import/index root for that case. Frozen Issues, manifests, and answer rubrics live beside those roots, never inside an indexed source tree. Each case uses its own managed Project, immutable Index, Session, and first external Run. First-run results remain authoritative; any follow-up is recorded separately. Fresh solvers were created with no inherited conversation history and instructed to use only MCP, but that is process isolation, not a technical filesystem permission boundary; do not claim their training data or local environment were technically blocked.

## Cases

| Case | Kind | Files | Expected outcome |
| --- | --- | --- | --- |
| `empty-cache-value` | local | `cache.py` | candidate found |
| `order-reservation-release` | cross-module | `orders.py`, `inventory.py` | candidate found |
| `opaque-payment-timeout` | insufficient attribution | `settlement.py`, `ports.py` | insufficient evidence |

`issues/` contains the solver inputs. `gold/` contains the hidden answer/rubric and must only be read after all three first-run reports are frozen. `manifests/` records source file size, SHA-256, and the canonical snapshot hash used for each import.

The synthetic snippets are intentionally tiny. Scores describe only these three scenarios. Timing distinguishes API import/index HTTP wall time, each solver's client-observed MCP wall time, Run/server timestamps, and whole investigation wall time; none is a pure AST or model-inference measurement.

## Results and evidence

- [First-round timing and UI observations](first-round.json)
- [First-round rubric score](first-round-scoring.json): 0/3 persisted structured hits; interrupted Runs stay in the denominator.
- [Submit failures and contract root cause](submission-failures.md): representative raw parameters, complete service errors and all attempt outcomes.
- [Guided post-score UI acceptance](guided-acceptance.md): separate answer-informed Runs, not included in first-round scoring.
- [Freeze manifest and hashes](freeze.json), [runtime project/index/session setup](runtime-setup.json)

The description fix has been verified with a cold stdio `tools/list` response and a protocol regression. The current host must reload the MCP server before it sees the new description. No new fresh first-round case tested whether the wording improves first-submit success; the guided checks are answer-informed and are not evidence of that effect.
