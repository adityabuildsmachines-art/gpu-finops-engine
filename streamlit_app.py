"""Streamlit interface for the simulation API."""
from datetime import datetime, time, timezone
import os
import ipaddress

import httpx
import streamlit as st

API_URL = os.environ.get('GPU_FINOPS_API_URL', 'http://127.0.0.1:8000').rstrip('/')


def api_url_is_safe(value):
    try:
        url = httpx.URL(value)
        if not url.host or url.username or url.password or url.query or url.fragment:
            return False
        if url.scheme == 'https':
            return True
        if url.scheme != 'http':
            return False
        if url.host.lower() == 'localhost':
            return True
        return ipaddress.ip_address(url.host).is_loopback
    except (ValueError, httpx.InvalidURL):
        return False


def api_request(method, path, token, payload=None):
    if not api_url_is_safe(API_URL):
        st.error('API URL must use HTTPS, except for local loopback addresses.')
        return None
    try:
        with httpx.Client(base_url=API_URL, timeout=10, trust_env=False, follow_redirects=False) as client:
            response = client.request(method, path, headers={'Authorization': f'Bearer {token}'}, json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 401:
            message = 'Invalid or missing API token.'
        elif status == 404:
            message = 'Decision not found or you do not have access.'
        elif status == 409:
            message = 'Decision is no longer eligible. Submit a fresh event.'
        elif status == 422:
            message = 'The API rejected the submitted values. Check the workload details.'
        else:
            message = f'API request failed (HTTP {status}).'
        st.error(message)
    except (httpx.RequestError, ValueError):
        st.error('Could not reach the API or read its response.')
    return None


st.set_page_config(page_title='GPU FinOps', page_icon='📊', layout='wide')
st.title('GPU FinOps Engine')
st.caption('Evaluate workload reports and review simulated reclamation decisions.')
st.info('Simulation only: no workloads are stopped and GPU release is not verified. Checkpoint state comes from the submitted event.')

with st.sidebar:
    st.header('Connection')
    st.caption('Use a token scoped to the workload and action. The API address is configured by the server.')
    token = st.text_input('API token', type='password', help='Bearer token configured for the API.')
    st.caption('Keep this interface private and use HTTPS outside a local demo.')

# Do not show one principal's decision after the session changes credentials.
if token != st.session_state.get('_last_token', ''):
    st.session_state.pop('decision', None)
    st.session_state['_last_token'] = token

st.subheader('Workload event')
with st.form('event'):
    left, right = st.columns(2)
    with left:
        gpu_id = st.text_input('GPU ID', value='GPU-demo-01')
        workload_id = st.text_input('Workload ID', value='demo-notebook')
        owner_email = st.text_input('Owner email', value='owner@example.com')
        team_name = st.text_input('Team name', value='Demo Team')
        environment = st.selectbox('Environment', ['dev', 'staging', 'prod'])
        workload_type = st.selectbox('Workload type', ['notebook', 'training', 'inference', 'batch'])
        pod_status = st.selectbox('Pod status', ['Running', 'Pending', 'Failed', 'Completed'])
    with right:
        checkpoint = st.selectbox('Checkpoint state', ['NONE', 'IN_PROGRESS', 'COMPLETE', 'FAILED'])
        utilisation = st.number_input('GPU utilisation (%)', min_value=0.0, max_value=100.0, value=4.0)
        memory = st.number_input('GPU memory used (MiB)', min_value=0.0, max_value=1_000_000_000.0, value=18432.0)
        rate = st.number_input('Hourly rate', min_value=0.01, max_value=1_000_000.0, value=4.2)
        horizon = st.number_input('Savings horizon (hours)', min_value=0.01, max_value=24.0, value=2.0)
        cost_known = st.checkbox('Disruption cost is known', value=True)
        disruption = st.number_input('Estimated disruption cost', min_value=0.0, max_value=1_000_000_000.0, value=1.0, disabled=not cost_known)
        last_date = st.date_input('Last activity date (UTC)', value=datetime.now(timezone.utc).date())
        last_time = st.time_input('Last activity time (UTC)', value=time(0, 0))
    submitted = st.form_submit_button('Evaluate workload', type='primary')

if submitted:
    st.session_state.pop('decision', None)
    if not token:
        st.error('Enter an API token before evaluating.')
    else:
        event = dict(gpu_id=gpu_id, workload_id=workload_id, owner_email=owner_email, team_name=team_name,
                     environment=environment, workload_type=workload_type, pod_status=pod_status,
                     checkpoint_state=checkpoint, sm_utilisation=utilisation, memory_used_mb=memory,
                     hourly_rate=rate, savings_horizon_hours=horizon,
                     estimated_disruption_cost=disruption if cost_known else None,
                     last_activity_at=datetime.combine(last_date, last_time, tzinfo=timezone.utc).isoformat())
        result = api_request('POST', '/evaluate', token, event)
        if result is not None:
            detail = api_request('GET', f"/decisions/{result['decision_id']}", token)
            st.session_state['decision'] = detail if detail is not None else result

st.divider()
st.subheader('Decision and audit')
lookup_id = st.text_input('Find a decision by ID')
if st.button('Load decision'):
    st.session_state.pop('decision', None)
    if not token or not lookup_id.strip():
        st.error('Enter an API token and decision ID.')
    elif '/' in lookup_id or '?' in lookup_id or '#' in lookup_id:
        st.error('Enter a valid decision ID.')
    else:
        st.session_state['decision'] = api_request('GET', f'/decisions/{lookup_id.strip()}', token)

decision = st.session_state.get('decision')
if decision:
    st.write('Decision ID:', decision['decision_id'])
    st.write('Workload state:', decision['workload_state'])
    st.write('Recommendation:', decision['decision'])
    st.write('Status:', decision.get('status', 'Unavailable — read permission required'))
    cols = st.columns(3)
    cols[0].metric('Avoidable cost', str(decision['avoidable_cost']))
    cols[1].metric('Disruption cost', str(decision['disruption_cost']) if decision['disruption_cost'] is not None else 'Unknown')
    cols[2].metric('Net benefit', str(decision['net_benefit']) if decision['net_benefit'] is not None else 'Unknown')
    st.write('Reasons:', ', '.join(decision['reason_codes']) or 'None')
    if decision.get('status') == 'AWAITING_APPROVAL' and decision['decision'] == 'REQUIRE_APPROVAL':
        a, b = st.columns(2)
        approved = a.button('Approve simulation', type='primary')
        rejected = b.button('Reject decision')
        if approved or rejected:
            result = api_request('POST', f"/decisions/{decision['decision_id']}/approve", token, {'approved': approved})
            if result is not None:
                st.session_state['decision'] = api_request('GET', f"/decisions/{decision['decision_id']}", token)
                st.rerun()
    st.markdown('**Audit trail**')
    if decision.get('audit_trail'):
        st.json(decision['audit_trail'])
    else:
        st.caption('No audit records available.')
else:
    st.caption('Evaluate a workload or load an existing decision.')
