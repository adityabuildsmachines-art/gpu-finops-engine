from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def make_event(**overrides):
    event = {
        "gpu_id": "GPU-07",
        "workload_id": "nb-2291",
        "owner_email": "priya@example.com",
        "team_name": "Research A",
        "environment": "dev",
        "workload_type": "notebook",
        "pod_status": "Running",
        "checkpoint_state": "NONE",
        "sm_utilisation": 0.8,
        "memory_used_mb": 18432,
        "hourly_rate": 4.20,
        "last_activity_at": (
            datetime.now(timezone.utc) - timedelta(hours=10)
        ).isoformat(),
    }

    event.update(overrides)
    return event


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_vram_hoarding_requires_approval():
    response = client.post("/evaluate", json=make_event())

    assert response.status_code == 200

    data = response.json()

    assert data["workload_state"] == "VRAM_HOARDING"
    assert data["decision"] == "REQUIRE_APPROVAL"
    assert "VRAM_RETAINED" in data["reason_codes"]
    assert data["net_benefit"] > 0


def test_checkpointing_is_protected():
    response = client.post(
        "/evaluate",
        json=make_event(
            workload_type="training",
            checkpoint_state="IN_PROGRESS",
            sm_utilisation=2.0,
            memory_used_mb=32000,
            hourly_rate=8.0,
        ),
    )

    assert response.status_code == 200

    data = response.json()

    assert data["workload_state"] == "CHECKPOINTING"
    assert data["decision"] == "DO_NOT_TOUCH"
    assert "CHECKPOINT_WRITE_ACTIVE" in data["reason_codes"]


def test_approval_executes_and_verifies():
    created = client.post("/evaluate", json=make_event()).json()

    response = client.post(
        f"/decisions/{created['decision_id']}/approve",
        json={
            "approver_email": "platform@example.com",
            "approved": True,
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "EXECUTED_AND_VERIFIED"
    assert data["operation_id"].startswith("op_")
    assert data["verification"] == "GPU_MEMORY_RELEASED"


def test_decision_audit_trail():
    created = client.post("/evaluate", json=make_event()).json()

    client.post(
        f"/decisions/{created['decision_id']}/approve",
        json={
            "approver_email": "platform@example.com",
            "approved": True,
        },
    )

    response = client.get(f"/decisions/{created['decision_id']}")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "EXECUTED_AND_VERIFIED"

    events = [item["event"] for item in data["audit_trail"]]

    assert "DECISION_CREATED" in events
    assert "ACTION_APPROVED_AND_VERIFIED" in events
