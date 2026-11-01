from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from main import app, get_db
from models import Base, Decision

TOKEN = "test-token-" + "x" * 32
READ_TOKEN = "read-token-" + "x" * 32
OTHER_TOKEN = "other-token-" + "x" * 32
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    principals = [
        {"token": TOKEN, "subject": "operator@example.com", "roles": ["evaluate", "read", "approve"], "scopes": [{"team_name": "Research A", "workload_id": "nb-2291"}]},
        {"token": READ_TOKEN, "subject": "reader@example.com", "roles": ["read"], "scopes": [{"team_name": "Research A", "workload_id": "nb-2291"}]},
        {"token": OTHER_TOKEN, "subject": "other@example.com", "roles": ["read", "approve"], "scopes": [{"team_name": "Other", "workload_id": "other"}]},
    ]
    monkeypatch.setenv("GPU_FINOPS_PRINCIPALS", json.dumps(principals))
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)

    def test_db():
        db = sessions()
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = test_db
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, sessions, engine
    app.dependency_overrides.clear()
    engine.dispose()


def event(**overrides):
    result = {
        "gpu_id": "GPU-07", "workload_id": "nb-2291", "owner_email": "priya@example.com",
        "team_name": "Research A", "environment": "dev", "workload_type": "notebook",
        "pod_status": "Running", "checkpoint_state": "NONE", "sm_utilisation": 0.8,
        "memory_used_mb": 18432, "hourly_rate": 4.2,
        "last_activity_at": (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat(),
        "savings_horizon_hours": 2, "estimated_disruption_cost": 1,
    }
    return {**result, **overrides}


def evaluate(client, **changes):
    response = client.post('/evaluate', json=event(**changes), headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, decision_id, approved=True, headers=HEADERS):
    return client.post(f'/decisions/{decision_id}/approve', json={"approved": approved}, headers=headers)


def test_authentication_and_scoped_authorization(api, monkeypatch):
    client, _, _ = api
    assert client.post('/evaluate', json=event()).status_code == 401
    assert client.post('/evaluate', json=event(), headers={"Authorization": f"Bearer {READ_TOKEN}"}).status_code == 403
    data = evaluate(client)
    other = {"Authorization": f"Bearer {OTHER_TOKEN}"}
    assert client.get(f"/decisions/{data['decision_id']}", headers=other).status_code == 404
    assert approve(client, data['decision_id'], headers=other).status_code == 404
    assert approve(client, data['decision_id'], headers={"Authorization": f"Bearer {READ_TOKEN}"}).status_code == 404
    assert client.post(f"/decisions/{data['decision_id']}/approve", headers=HEADERS, json={"approved": True, "approver_email": "forged@example.com"}).status_code == 422
    monkeypatch.delenv('GPU_FINOPS_PRINCIPALS')
    assert client.post('/evaluate', json=event(), headers=HEADERS).status_code == 503


@pytest.mark.parametrize('pod', ['Running', 'Pending', 'Failed', 'Completed'])
@pytest.mark.parametrize('checkpoint', ['IN_PROGRESS', 'FAILED'])
def test_checkpoint_never_actionable(api, pod, checkpoint):
    client, _, _ = api
    data = evaluate(client, pod_status=pod, checkpoint_state=checkpoint)
    assert data['decision'] == 'DO_NOT_TOUCH'
    assert data['workload_state'] == 'CHECKPOINTING'
    assert approve(client, data['decision_id']).status_code == 409


def test_active_workload_and_unknown_disruption_are_not_actionable(api):
    client, _, _ = api
    active = evaluate(client, sm_utilisation=90)
    assert active['decision'] == 'NO_ACTION'
    assert approve(client, active['decision_id']).status_code == 409
    unknown = evaluate(client, estimated_disruption_cost=None)
    assert unknown['decision'] == 'NO_ACTION'
    assert unknown['disruption_cost'] is None and unknown['net_benefit'] is None
    assert 'DISRUPTION_COST_UNKNOWN' in unknown['reason_codes']
    assert approve(client, unknown['decision_id']).status_code == 409


def test_prospective_cost_and_production_requires_approval(api):
    client, _, _ = api
    data = evaluate(client, environment='prod', savings_horizon_hours=2, estimated_disruption_cost=1)
    assert data['avoidable_cost'] == 8.4 and data['net_benefit'] == 7.4
    assert data['decision'] == 'REQUIRE_APPROVAL'
    no_benefit = evaluate(client, estimated_disruption_cost=100)
    assert no_benefit['decision'] == 'NO_ACTION'


def test_approval_and_rejection_audit_persist_and_identity_is_authenticated(api):
    client, _, _ = api
    data = evaluate(client)
    response = approve(client, data['decision_id'])
    assert response.status_code == 200
    assert response.json()['status'] == 'SIMULATED'
    assert response.json()['verification'] is None
    detail = client.get(f"/decisions/{data['decision_id']}", headers=HEADERS).json()
    assert [entry['event'] for entry in detail['audit_trail']] == ['DECISION_CREATED', 'ACTION_SIMULATED']
    assert detail['audit_trail'][-1]['actor'] == 'operator@example.com'
    assert detail['audit_trail'][0]['savings_horizon_hours'] == 2
    assert detail['audit_trail'][0]['estimated_disruption_cost'] == 1
    assert approve(client, data['decision_id']).status_code == 409
    rejected = evaluate(client)
    assert approve(client, rejected['decision_id'], approved=False).json()['status'] == 'REJECTED'
    detail = client.get(f"/decisions/{rejected['decision_id']}", headers=HEADERS).json()
    assert detail['audit_trail'][-1]['event'] == 'ACTION_REJECTED'
    assert approve(client, rejected['decision_id']).status_code == 409


def test_newer_or_expired_observation_blocks_simulation(api):
    client, sessions, _ = api
    old = evaluate(client)
    evaluate(client, checkpoint_state='IN_PROGRESS')
    assert approve(client, old['decision_id']).status_code == 409
    expired = evaluate(client)
    with sessions.begin() as db:
        db.get(Decision, expired['decision_id']).created_at = datetime.utcnow() - timedelta(minutes=6)
    assert approve(client, expired['decision_id']).status_code == 409
    assert approve(client, expired['decision_id'], approved=False).status_code == 200


def test_parallel_approvals_have_one_winner(api):
    client, _, _ = api
    data = evaluate(client)
    barrier = threading.Barrier(2)
    def submit():
        barrier.wait()
        return approve(client, data['decision_id']).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(results) == [200, 409]
    detail = client.get(f"/decisions/{data['decision_id']}", headers=HEADERS).json()
    assert [entry['event'] for entry in detail['audit_trail']].count('ACTION_SIMULATED') == 1


def test_failed_database_update_preserves_status_and_audit(api):
    client, _, engine = api
    data = evaluate(client)
    with engine.begin() as connection:
        connection.execute(text("CREATE TRIGGER refuse_approval BEFORE UPDATE ON decisions BEGIN SELECT RAISE(ABORT, 'blocked'); END"))
    assert approve(client, data['decision_id']).status_code == 500
    detail = client.get(f"/decisions/{data['decision_id']}", headers=HEADERS).json()
    assert detail['status'] == 'AWAITING_APPROVAL'
    assert [entry['event'] for entry in detail['audit_trail']] == ['DECISION_CREATED']


@pytest.mark.parametrize('changes', [
    {'hourly_rate': 1e308}, {'hourly_rate': 'Infinity'}, {'sm_utilisation': -1},
    {'memory_used_mb': -1}, {'last_activity_at': '2026-01-01T12:00:00'},
    {'last_activity_at': '2999-01-01T00:00:00Z'}, {'team_name': '  '},
])
def test_invalid_inputs_are_rejected(api, changes):
    client, _, _ = api
    assert client.post('/evaluate', json=event(**changes), headers=HEADERS).status_code == 422


def test_timezone_offset_is_normalized_before_storage(api):
    client, sessions, _ = api
    data = evaluate(client, last_activity_at='2020-01-01T12:00:00+05:30')
    with sessions() as db:
        assert db.get(Decision, data['decision_id']).last_activity_at == datetime(2020, 1, 1, 6, 30)


def test_nonfinite_json_is_a_validation_error(api):
    client, _, _ = api
    body = json.dumps(event()).replace('"hourly_rate": 4.2', '"hourly_rate": NaN')
    response = client.post('/evaluate', content=body, headers={**HEADERS, "Content-Type": "application/json"})
    assert response.status_code == 422


def test_legacy_decision_cannot_be_approved(api):
    client, sessions, _ = api
    data = evaluate(client)
    with sessions.begin() as db:
        db.get(Decision, data['decision_id']).policy_version = 'gpu-finops-mvp-v1'
    assert approve(client, data['decision_id']).status_code == 409


def test_ambiguous_observation_order_fails_closed(api):
    client, sessions, _ = api
    first = evaluate(client)
    second = evaluate(client)
    with sessions.begin() as db:
        a = db.get(Decision, first['decision_id'])
        b = db.get(Decision, second['decision_id'])
        b.created_at = a.created_at
    assert approve(client, first['decision_id']).status_code == 409
    assert approve(client, second['decision_id']).status_code == 409


def test_documented_sample_walkthrough(api, monkeypatch):
    import json
    from pathlib import Path
    client, _, _ = api
    principals = [{"token": TOKEN, "subject": "demo@example.com", "roles": ["evaluate", "read", "approve"],
                   "scopes": [{"team_name": "Demo Team", "workload_id": "demo-notebook"}]}]
    monkeypatch.setenv("GPU_FINOPS_PRINCIPALS", json.dumps(principals))
    sample = json.loads((Path(__file__).parent / "examples/workload-event.json").read_text())
    response = client.post('/evaluate', headers=HEADERS, json=sample)
    assert response.status_code == 200
    decision_id = response.json()['decision_id']
    simulated = approve(client, decision_id)
    assert simulated.status_code == 200 and simulated.json()['status'] == 'SIMULATED'
    assert simulated.json()['verification'] is None
    detail = client.get(f'/decisions/{decision_id}', headers=HEADERS).json()
    assert [item['event'] for item in detail['audit_trail']] == ['DECISION_CREATED', 'ACTION_SIMULATED']
