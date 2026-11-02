# GPU FinOps Engine

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:31c0892a79bb2a5c0011faf3c5d168e926b97db6098f0ea616a8c7320f1bbf78 -->

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:fa4dafbbaa4e98d2775f7d221768e31344fdb323e1248f4c5460648188865b7c -->

This MVP control plane evaluates workload events submitted through the API. The caller supplies workload and checkpoint state; the service does not discover workloads from a cluster or independently confirm checkpoint state.

Approvals produce a simulation ID and `SIMULATED` status. They do not reclaim GPUs or verify memory release. The [architecture](ARCHITECTURE.md) describes the MVP and the intended production integrations for Kubernetes, Run:ai, DCGM, checkpoint authority, safe scheduler actions, and post-action verification.

<!-- /coderabbit-ai-generated:v1 -->

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

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:9f63952e3be78e7cd52617a9e471aaa8927463ec1e702af1104a7678f58c4fa9 -->

`GET /decisions/{id}` requires the `read` role for the same scope. Interactive API documentation is at `/docs`.

## Local demo

From the repository root, start the service in a disposable database with a generated local token. These commands use Bash:

```bash
export GPU_FINOPS_DEMO_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export GPU_FINOPS_PRINCIPALS="$(python -c 'import json,os; print(json.dumps([{"token":os.environ["GPU_FINOPS_DEMO_TOKEN"],"subject":"demo@example.com","roles":["evaluate","read","approve"],"scopes":[{"team_name":"Demo Team","workload_id":"demo-notebook"}]}]))')"
export GPU_FINOPS_DATABASE_URL="sqlite:////tmp/gpu-finops-demo.db"
uvicorn main:app --port 8000 &
demo_pid=$!
```

After the server is ready, submit the [sample event](examples/workload-event.json), simulate approval, and read its audit trail:

```bash
result=$(curl -fsS -H "Authorization: Bearer $GPU_FINOPS_DEMO_TOKEN" -H 'Content-Type: application/json' --data @examples/workload-event.json http://127.0.0.1:8000/evaluate)
printf '%s\n' "$result"
decision_id=$(printf '%s' "$result" | python -c 'import json,sys; print(json.load(sys.stdin)["decision_id"])')
curl -fsS -H "Authorization: Bearer $GPU_FINOPS_DEMO_TOKEN" -H 'Content-Type: application/json' --data '{"approved":true}' "http://127.0.0.1:8000/decisions/$decision_id/approve"
curl -fsS -H "Authorization: Bearer $GPU_FINOPS_DEMO_TOKEN" "http://127.0.0.1:8000/decisions/$decision_id"
kill "$demo_pid"
```

The approval returns `SIMULATED`; the audit includes `DECISION_CREATED` and `ACTION_SIMULATED`. The sample timestamp is deliberately historical. Delete the disposable database when finished.

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:154ea8de13eccc27d22123fe34d323efdb90a196d1067135d3f460507666606c -->

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:f0772c6ee39d05808c8f2089f384f68c1ab6ec00c6b424bf9dbad3aeca5989d1 -->

## API access

Use scoped API tokens for each operation.

<!-- /coderabbit-ai-generated:v1 --> <!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:8afe54ca5be9ea211af341dca34667e5107d0fdee3b72bdaf5b2c7a621b88b6e -->

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:328588b5b059f6713c41a15643ce2b4670a9d4f76908c3dfc046f216cd0aa500 -->

Protect API credentials and use HTTPS for remote access.

<!-- /coderabbit-ai-generated:v1 -->

<!-- /coderabbit-ai-generated:v1 --> API approval remains simulated.

## Read-only observations

<!-- /coderabbit-ai-generated:v1 -->

`observe.py` is a local CLI and does not submit events, update the database, or perform scheduler actions. It outputs JSON with a collection timestamp:

```bash
python observe.py kubernetes --namespace research
python observe.py kubernetes --namespace research --in-cluster
python observe.py dcgm --url http://127.0.0.1:9400/metrics
```

Kubernetes uses the current kubeconfig by default or the service account with `--in-cluster`; grant only `list` on pods in the selected namespace. It returns pods requesting `nvidia.com/gpu` or NVIDIA MIG resources, including pod and owner references. It does not resolve higher-level owners, detect every GPU allocation mechanism, or discover Run:ai workloads separately.

DCGM reads the specified trusted exporter URL and returns GPU utilisation, framebuffer usage, and GPU temperature samples with their exporter labels and any sample timestamp. Exporter labels and metric freshness vary by deployment; these observations do not establish workload identity, checkpoint state, GPU release, or safe reclamation. Protect observation output and exporter access as appropriate for your environment. The adapters do not expose a new HTTP endpoint.


<!-- /coderabbit-ai-generated:v1 -->


<!-- /coderabbit-ai-generated:v1 -->