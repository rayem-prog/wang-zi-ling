# 6luna verification report

Date: 2026-10-12 (Asia/Shanghai)  
Repository: `stock-pilot`, branch `fix/signals-snapshots`  
Verifier: GPT-6 Luna, per the user’s verification-model restriction.

## Results

- Final full backend suite: `.venv/bin/pytest -q` — **486 passed**, 2 Starlette/httpx deprecation warnings, 142.56 seconds.
- Final frontend suite: `npm test` — **52 passed**. `npm run build` succeeded; Vite reported a 1.305 MB JavaScript chunk warning.
- Final whitespace validation: `git diff --check` passed.
- Windows compatibility follow-up: before the portability fix, an import simulation with `fcntl` unavailable reproduced an API import failure; three focused RED tests also demonstrated missing Windows byte-range locking, unsafe process-identity probing, and an unhandled no-lock-backend case. After the portability implementation, the three focused Windows simulations passed and an import with `fcntl` unavailable passed on this macOS host. This is a simulated compatibility check, not a Windows VM run.
- Regression review added two red/green manual BUY tests for requests without `snapshot_id`, a direct generation-lease test, a strict legacy-infinity import test, an active API-refresh pause test, a legacy status-health projection test, and direct sync-generation race tests. Missing references are rejected when a published result exists; legacy installations without a published head retain independent manual BUY behavior. Sync and API generation now invalidate the audit certificate before long-running work, while preserving the old published rows.
- The direct sync race test initially returned `current` during a blocked `pipeline.update_all`; after the producer invalidated the certificate at lease acquisition, the response stayed `historical`, paused new buys, exposed `audit_state=pending`, and retained the old snapshot ID and rows. Focused lease/race run: 3 passed.
- Follow-up REDs found that a fresh health-cache certificate could overwrite a direct generator's pending audit, a publish poll could observe `succeeded` before the complete result was persisted, a bound manual BUY could proceed during a running job, and the UI could remain buy-enabled while refresh submission was pending. Focused regressions now pass: active jobs remain paused even if an audit finishes, publication persists the successful result atomically with job completion, operation handlers recheck the job, and the frontend disables buys immediately while submitting.
- A legacy bootstrap regression showed that rejected provenance could fall back to a tradeable CSV. The new persistent migration-rejection state survives later successful market audits and blocks no-head CSV fallback; the focused test passes while true no-head legacy installations retain the intended compatibility behavior.

## HTTP and browser acceptance

The isolated HTTP harness uses a temporary result database and a standalone FastAPI/TCP server with 5,000 seeded rows. It performs 20 warmups and 1,000 requests per state at concurrency 5, and spies on audit, training, market-signature, and fetch functions to ensure GET does not invoke them. The first completed run recorded:

| State | P50 | P95 | Max | Requests/sec |
|---|---:|---:|---:|---:|
| Valid | 178.66 ms | 248.31 ms | 558.98 ms | 24.77 |
| Historical | 179.58 ms | 247.60 ms | 440.29 ms | 24.50 |
| Updating | 179.84 ms | 235.14 ms | 376.98 ms | 25.10 |

Raw evidence: [http-benchmark.json](http-benchmark.json), integrated-tree run at `2026-10-12T03:42:23Z`. This is a synthetic local API/JSON measurement; it does not measure production market database contention, live-market writes, or full generation/training latency. P95 was below the design target of 500 ms in all three isolated scenes; one valid-scene maximum was 558.98 ms.

Browser verification used the built `frontend/dist` against a temporary isolated fixture server. The updating view retained the old six rows and dates, displayed the updating stage, and disabled order buttons. The failed/history view retained the same dated rows, displayed generation and audit errors, and disabled order buttons. The fixture’s valid certificate expired during the manual browser session; that view correctly downgraded to history and is excluded from valid-state browser evidence.

## Restarted service and live browser acceptance

After root restarted the API, PID `20086` was serving `python -m uvicorn api.main:app --host 127.0.0.1 --port 8888` from the repository at 2026-10-12 11:57:32 local time. Its `/openapi.json` includes `/api/signals/snapshot`. A live read-only GET returned the preserved legacy result with snapshot ID `f10d90bd604545e9afe7c955b689ddf1`, 7 rows, data through `2026-10-09 15:00 +08:00`, generated `2026-10-11 22:03:58 +08:00`. The result remains `historical`; `create_buy=paused` and position/order actions are paused. At 12:04:34 local time, the market audit was `passed` and `data_health.ready=true`, but the imported legacy result remained historical and non-actionable as intended.

The old stale-cache startup policy automatically started data-only job `f44c1066c7f445248d182a4ea3a66d77` at 12:02:53 local time. No verifier request submitted or retried this job. The latest read-only status at 12:04:34 showed it had failed during `fetch` with `database is locked` at 12:03:52 local time. The persisted job projection has no traceback or database path, and no separate server log was available to identify the lock site. The seven published rows and historical state were still present, with actions paused. This startup refresh failure is recorded for root; it was not retried during verification.

I opened the real service in Chrome and navigated to the Signals page. The rendered page showed the 7 retained recommendations, data cutoff `2026/10/9 15:00:00`, generation time `2026/10/11 22:03:58`, the historical-only warning, and disabled `下单` buttons. No refresh, paper order, or holdings action was clicked. The Vite proxy used for the browser view was stopped after inspection; the restarted API remains running.


## Limitations

The API restart and automatic data-only job were performed by the root workflow; the verifier did not change market data, holdings, or send notifications. The startup job's `database is locked` failure is the only live-service issue found. Full A16 production-load equivalence and a real generation/training-duration run remain unmeasured; the synthetic HTTP numbers should not be presented as such. The build warning for the large frontend chunk remains.
