# GPU FinOps Engine

A FastAPI-based GPU FinOps control plane that identifies under-utilised or wasteful GPU workloads, estimates avoidable capacity cost, applies checkpoint-aware safety rules, and manages approval-gated reclamation with a verifiable audit trail.

## Features

- GPU workload event ingestion
- Workload-state classification
- VRAM-retention and low-utilisation detection
- Checkpoint-aware safety gate
- Avoidable-cost and disruption-cost modelling
- Policy-based reclamation recommendations
- Approval-gated execution
- Post-action verification
- Decision-to-verification audit trail

## Run locally

```powershell
pip install -r requirements.txt
uvicorn main:app --reload
```

## API documentation

Open:

```text
http://127.0.0.1:8000/docs
```

## Tests

```powershell
pytest -v
```
