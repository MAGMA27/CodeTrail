# First-round submit failures

All first-round `submit_external_run` calls reached the server and returned structured CodeTrail error envelopes. None was rejected by the local MCP host schema. The Run report and lifecycle status are captured in [first-round.json](first-round.json); every Run retained its original interrupted terminal status and system-generated report.

The solvers' complete first-round tool transcripts are preserved in their task histories. The two representative payloads below capture the repeated contract failures; subsequent attempts and their exact server errors are listed after them. Payload IDs refer only to the new synthetic projects.

## Representative raw payloads

Case `order-reservation-release`, first submit, 2026-10-07 03:41:56.363–03:41:56.377 UTC, client wall 14 ms:

```json
{
  "project_id": "project-38981b640aad4f39a1e4a9d3200194a6",
  "index_id": "index-import-9bd35e5577de3e59252d9f1c9d0bbd95-relations-v13-8f03042578fb1a16",
  "session_id": "session-b1049379f99d479795e44aafe7b40ef4",
  "run_id": "run-98b6c64f700d44adbc094cad4b05ec0e",
  "outcome": "explanation",
  "summary": "The declined-payment cleanup path in orders.place_order passes order.sku to inventory.release instead of the reservation_id returned by inventory.reserve. inventory.release deletes by its reservation_id argument, so the declined-payment branch targets the wrong reservation key and leaves the reserved stock unavailable.",
  "evidence_ids": [
    "evidence-c06cecc462597d10f89294921b9844bb",
    "evidence-91b078eb3125baded1029615588e1b01"
  ],
  "findings": [
    {
      "finding_id": "declined-payment-reservation-key-mismatch",
      "kind": "observation",
      "text": "orders.place_order stores the ID returned by reserve, but its PaymentDeclined handler calls release(order.sku, store). inventory.release accepts reservation_id and passes that value to store.delete_reservation. The failure path therefore uses the SKU where the reservation ID is required.",
      "evidence_ids": [
        "evidence-c06cecc462597d10f89294921b9844bb",
        "evidence-91b078eb3125baded1029615588e1b01"
      ],
      "symbol_ids": [
        "sym-e75061ca96b82787b596b49fc5db3f98",
        "sym-8a9e6a1309ed54d3e2ad2490b0b84082",
        "sym-8e78681c188a6a6c2b9e14904a4566dc"
      ]
    }
  ],
  "explanation": {
    "coverage": "complete",
    "steps": [
      {
        "step_id": "reserve-before-charge",
        "title": "Order reserves inventory before charging",
        "description": "place_order assigns the return value of reserve(order.sku, order.quantity, store) to reservation_id, then calls charge.",
        "kind": "confirmed",
        "evidence_ids": ["evidence-c06cecc462597d10f89294921b9844bb"],
        "symbol_ids": [
          "sym-e75061ca96b82787b596b49fc5db3f98",
          "sym-8a9e6a1309ed54d3e2ad2490b0b84082"
        ]
      },
      {
        "step_id": "decline-cleanup-call",
        "title": "Declined payment enters cleanup handler",
        "description": "When charge raises PaymentDeclined, place_order calls release(order.sku, store) and re-raises the decline.",
        "kind": "confirmed",
        "evidence_ids": ["evidence-c06cecc462597d10f89294921b9844bb"],
        "symbol_ids": ["sym-e75061ca96b82787b596b49fc5db3f98"]
      },
      {
        "step_id": "wrong-release-key",
        "title": "Cleanup passes the wrong identifier",
        "description": "inventory.release names its first argument reservation_id and deletes using that value. The decline handler supplies order.sku instead of the captured reservation_id, so cleanup targets the wrong key and the reservation remains.",
        "kind": "inferred",
        "evidence_ids": [
          "evidence-c06cecc462597d10f89294921b9844bb",
          "evidence-91b078eb3125baded1029615588e1b01"
        ],
        "symbol_ids": [
          "sym-e75061ca96b82787b596b49fc5db3f98",
          "sym-8e78681c188a6a6c2b9e14904a4566dc"
        ]
      }
    ]
  },
  "limitations": [
    "The indexed source does not expose the store implementation or runtime identifier values; the key mismatch is inferred from the explicit reservation_id contract and the SKU argument at the call site."
  ]
}
```

Full server response:

```json
{
  "schema_version": "0.1.0",
  "ok": false,
  "error": {
    "schema_version": "0.1.0",
    "code": "invalid_input",
    "message": "explanation conclusions require explanation and cannot include Issue findings or candidate links",
    "retryable": false
  },
  "result": null,
  "report": null,
  "references": []
}
```

Case `order-reservation-release`, third submit, 2026-10-07 03:43:02.213–03:43:02.226 UTC, client wall 13 ms. This was changed to `candidate_found`, kept `findings`, and omitted the required `candidate_links` array:

```json
{
  "project_id": "project-38981b640aad4f39a1e4a9d3200194a6",
  "index_id": "index-import-9bd35e5577de3e59252d9f1c9d0bbd95-relations-v13-8f03042578fb1a16",
  "session_id": "session-b1049379f99d479795e44aafe7b40ef4",
  "run_id": "run-98b6c64f700d44adbc094cad4b05ec0e",
  "outcome": "candidate_found",
  "summary": "Root cause found in the PaymentDeclined path: orders.place_order calls release(order.sku, store) although inventory.release expects a reservation_id and deletes using that argument. The returned reservation_id from reserve is captured but not passed to cleanup.",
  "evidence_ids": [
    "evidence-c06cecc462597d10f89294921b9844bb",
    "evidence-91b078eb3125baded1029615588e1b01"
  ],
  "findings": [
    {
      "finding_id": "declined-payment-reservation-key-mismatch",
      "kind": "observation",
      "text": "orders.place_order stores the ID returned by reserve, but its PaymentDeclined handler calls release(order.sku, store). inventory.release accepts reservation_id and passes that value to store.delete_reservation. The failure path therefore uses the SKU where the reservation ID is required.",
      "evidence_ids": [
        "evidence-c06cecc462597d10f89294921b9844bb",
        "evidence-91b078eb3125baded1029615588e1b01"
      ],
      "symbol_ids": [
        "sym-e75061ca96b82787b596b49fc5db3f98",
        "sym-8a9e6a1309ed54d3e2ad2490b0b84082",
        "sym-8e78681c188a6a6c2b9e14904a4566dc"
      ]
    }
  ],
  "limitations": [
    "The indexed source does not expose the store implementation or runtime identifier values; the key mismatch is inferred from the reservation_id argument contract and the SKU passed by the decline handler."
  ]
}
```

Full server response:

```json
{
  "schema_version": "0.1.0",
  "ok": false,
  "error": {
    "schema_version": "0.1.0",
    "code": "invalid_input",
    "message": "1 validation error for IssueModelFinal\n  Value error, candidate_found Issue output must include candidate links [type=value_error, input_value={'index_id': 'index-impor... the decline handler.']}, input_type=dict]\n    For further information visit https://errors.pydantic.dev/2.13/v/value_error",
    "retryable": false
  },
  "result": null,
  "report": null,
  "references": []
}
```

## All first-round submit attempts

| Case | # | Payload correction from previous attempt | Client wall ms | Full server error message | Classification |
| --- | ---: | --- | ---: | --- | --- |
| empty-cache-value | 1 | `outcome=explanation` with both `findings` and `explanation` | 14 | `explanation conclusions require explanation and cannot include Issue findings or candidate links` | server `invalid_input` |
| empty-cache-value | 2 | Removed Issue `findings`; kept `outcome=explanation` | 237 | `external conclusion must be a Issue conclusion for this Run` | server `conflict` |
| empty-cache-value | 3 | Changed to `outcome=candidate_found`; kept findings; omitted `candidate_links` | 13 | `1 validation error for IssueModelFinal\n  Value error, candidate_found Issue output must include candidate links [type=value_error, input_value={'index_id': 'index-impor...herefore unconfirmed.']}, input_type=dict]\n    For further information visit https://errors.pydantic.dev/2.13/v/value_error` | server Pydantic `invalid_input` |
| empty-cache-value | 4 | Changed to `outcome=insufficient_evidence`; had findings/evidence IDs; no candidate links | 237 | `external Run time budget is exhausted` | server `budget_exceeded` |
| order-reservation-release | 1 | `outcome=explanation` with both `findings` and `explanation` | 14 | `explanation conclusions require explanation and cannot include Issue findings or candidate links` | server `invalid_input` |
| order-reservation-release | 2 | Removed Issue `findings`; kept `outcome=explanation` | 254 | `external conclusion must be a Issue conclusion for this Run` | server `conflict` |
| order-reservation-release | 3 | Changed to `outcome=candidate_found`; kept findings; omitted `candidate_links` | 13 | `1 validation error for IssueModelFinal\n  Value error, candidate_found Issue output must include candidate links [type=value_error, input_value={'index_id': 'index-impor... the decline handler.']}, input_type=dict]\n    For further information visit https://errors.pydantic.dev/2.13/v/value_error` | server Pydantic `invalid_input` |
| order-reservation-release | 4 | Added a candidate link for the returned target symbol and retained findings/evidence | 221 | `external Run time budget is exhausted` | server `budget_exceeded` |
| opaque-payment-timeout | 1 | `outcome=insufficient_evidence` while also including `explanation` | 16 | `Issue conclusions cannot include an explanation` | server `invalid_input` |
| opaque-payment-timeout | 2 | Removed `explanation`, leaving Issue fields and outcome | 206 | `external Run time budget is exhausted` | server `budget_exceeded` |

The `budget_exceeded` responses occurred after the 90-second Run service budget had elapsed; no later attempt was made. The last case's second payload had the correct Issue outcome shape but did not get a server-side validation/acceptance result before the service budget expired. The host returned structured MCP responses for all of these calls, so none is a local schema rejection.
