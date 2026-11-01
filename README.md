# GPU FinOps Engine

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:31c0892a79bb2a5c0011faf3c5d168e926b97db6098f0ea616a8c7320f1bbf78 -->

This service evaluates GPU workload reports and records **simulated** approval decisions. It does not reclaim GPUs or verify GPU memory release. A real executor requires an authoritative live workload provider and a final safety check.

## Setup

Use Python 3.12 or 3.13 and install dependencies with `pip install -r requirements.txt`. Start with `uvicorn main:app --reload` and run the suite with `pytest -v`.

The default database is `sqlite:///./gpu_finops.db`. Set `GPU_FINOPS_DATABASE_URL` to an explicit SQLAlchemy URL when needed. SQLite is intended for development; use a database and driver suitable for concurrent deployment. Existing decisions remain readable, but decisions made under the v1 policy cannot be approved under v2.

## Authentication

Configure `GPU_FINOPS_PRINCIPALS` as a JSON array. Each entry contains a secret `token` of at least 32 characters, a stable `subject`, `roles` chosen from `evaluate`, `read`, and `approve`, and exact workload `scopes` with `team_name` and `workload_id`. Supply the token with `Authorization: Bearer <token>`. Protect the configuration as a secret, use strong random tokens, and use TLS. With missing or invalid configuration the API fails closed. `/health` is public.

For example, replace the token placeholder with a unique random secret before using this configuration:

```json
[{"token":"REPLACE_WITH_A_RANDOM_SECRET_AT_LEAST_32_CHARACTERS", "subject":"operator@example.com", "roles":["evaluate","read","approve"], "scopes":[{"team_name":"Research A","workload_id":"nb-2291"}]}]
```

Grant `evaluate` only to a trusted telemetry source. This prototype records when a report arrives; it cannot independently confirm the report represents live workload state. Credential configuration is a local prototype mechanism, not an identity provider.

## Decision and approval behavior

`POST /evaluate` requires a timezone-aware `last_activity_at`. `savings_horizon_hours` is a prospective horizon of at most 24 hours, and `estimated_disruption_cost` must be provided by the trusted evaluator to obtain an actionable recommendation. Unknown disruption cost, active compute, and non-positive benefit produce no action. In-progress and failed checkpoints produce `DO_NOT_TOUCH` regardless of pod status. All actionable recommendations require approval.

`POST /decisions/{id}/approve` takes `{"approved": true}` or `{"approved": false}`. The authenticated subject is recorded as the actor; caller-supplied approver identities are rejected. Approval is allowed only for the latest v2 report for that team and workload received within five minutes. Approval returns `SIMULATED` and a simulation ID; it never returns successful verification. Rejection returns `REJECTED`. Concurrent approvals cannot both succeed. Status and audit changes are committed together. A newer report or expired report blocks simulation; obtain another report before trying again.

`GET /decisions/{id}` requires the `read` role for the same scope. Interactive API documentation is at `/docs`.


<!-- /coderabbit-ai-generated:v1 -->