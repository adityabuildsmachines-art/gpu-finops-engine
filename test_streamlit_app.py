from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).with_name('streamlit_app.py'))


def test_evaluate_and_approve_simulation(monkeypatch):
    requests = []
    state = {'status': 'AWAITING_APPROVAL'}
    decision = {'decision_id': 'dec_test', 'workload_state': 'VRAM_HOARDING',
                'decision': 'REQUIRE_APPROVAL', 'reason_codes': ['VRAM_RETAINED'],
                'avoidable_cost': 8.4, 'disruption_cost': 1.0, 'net_benefit': 7.4}

    def handler(request):
        requests.append(request)
        assert request.headers['authorization'] == 'Bearer demo-token'
        if request.url.path == '/evaluate':
            return httpx.Response(200, json=decision)
        if request.method == 'POST':
            assert request.content == b'{"approved":true}'
            state['status'] = 'SIMULATED'
            return httpx.Response(200, json={'decision_id': 'dec_test', 'status': 'SIMULATED'})
        audit = [{'event': 'DECISION_CREATED'}]
        if state['status'] == 'SIMULATED':
            audit.append({'event': 'ACTION_SIMULATED'})
        return httpx.Response(200, json={**decision, **state, 'audit_trail': audit})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    app = AppTest.from_file(APP).run()
    next(item for item in app.text_input if item.label == 'API token').set_value('demo-token')
    app.button(key='FormSubmitter:event-Evaluate workload').click().run()
    assert not app.exception
    assert any('REQUIRE_APPROVAL' in item.value for item in app.markdown)
    next(button for button in app.button if button.label == 'Approve simulation').click().run()
    assert not app.exception
    assert any('SIMULATED' in item.value for item in app.markdown)
    assert 'ACTION_SIMULATED' in app.json[0].value
    assert len(requests) == 4


def test_missing_token_and_checkpoint_protection(monkeypatch):
    decision = {'decision_id': 'dec_safe', 'workload_state': 'CHECKPOINTING',
                'decision': 'DO_NOT_TOUCH', 'reason_codes': ['CHECKPOINT_WRITE_ACTIVE'],
                'avoidable_cost': 0.0, 'disruption_cost': 1.0, 'net_benefit': 0.0,
                'status': 'NO_ACTION', 'audit_trail': [{'event': 'DECISION_CREATED'}]}
    real_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: real_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=decision)), **kwargs))
    app = AppTest.from_file(APP).run()
    app.button(key='FormSubmitter:event-Evaluate workload').click().run()
    assert 'Enter an API token' in app.error[0].value
    next(item for item in app.text_input if item.label == 'API token').set_value('demo-token')
    next(select for select in app.selectbox if select.label == 'Checkpoint state').set_value('IN_PROGRESS')
    app.button(key='FormSubmitter:event-Evaluate workload').click().run()
    assert not app.exception
    assert any('DO_NOT_TOUCH' in item.value for item in app.markdown)
    assert all(button.label != 'Approve simulation' for button in app.button)


@pytest.mark.parametrize('url', ['http://example.com', 'http://localhost.example.com', 'http://127.0.0.1@example.com', 'ftp://localhost'])
def test_remote_http_does_not_send_the_token(monkeypatch, url):
    calls = []
    monkeypatch.setenv('GPU_FINOPS_API_URL', url)
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: calls.append(kwargs))
    app = AppTest.from_file(APP).run()
    next(item for item in app.text_input if item.label == 'API token').set_value('demo-token')
    app.button(key='FormSubmitter:event-Evaluate workload').click().run()
    assert not app.exception
    assert 'API URL must use HTTPS' in app.error[0].value
    assert not calls


def test_remote_https_reaches_api(monkeypatch):
    calls = []
    monkeypatch.setenv('GPU_FINOPS_API_URL', 'https://api.example.com')
    real_client = httpx.Client
    def handler(request):
        calls.append(request.url.scheme)
        return httpx.Response(401)
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    app = AppTest.from_file(APP).run()
    next(item for item in app.text_input if item.label == 'API token').set_value('demo-token')
    app.button(key='FormSubmitter:event-Evaluate workload').click().run()
    assert not app.exception
    assert calls == ['https']
    assert 'Invalid or missing API token' in app.error[0].value
