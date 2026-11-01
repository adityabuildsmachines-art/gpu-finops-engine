from types import SimpleNamespace

import httpx
import pytest
from kubernetes import client

from observe import MAX_METRICS_BYTES, discover_workloads, read_dcgm_metrics


def pod(name, gpu=None, phase='Running', init_gpu=None):
    def container(container_name, resource):
        return client.V1Container(name=container_name, resources=client.V1ResourceRequirements(limits=resource or {}))
    return client.V1Pod(
        metadata=client.V1ObjectMeta(name=name, namespace='research', uid=f'uid-{name}',
            owner_references=[client.V1OwnerReference(api_version='batch/v1', kind='Job', name='job1', uid='job-uid')]),
        spec=client.V1PodSpec(containers=[container('app', gpu)], init_containers=[container('init', init_gpu)], node_name='gpu-node', priority=10),
        status=client.V1PodStatus(phase=phase),
    )


def test_kubernetes_discovery_lists_only_gpu_pods_across_pages():
    class FakeAPI:
        def __init__(self):
            self.calls = []
        def list_namespaced_pod(self, namespace, **kwargs):
            self.calls.append((namespace, kwargs))
            if kwargs['_continue'] is None:
                return SimpleNamespace(items=[pod('gpu', {'nvidia.com/gpu': '1'}), pod('cpu')], metadata=SimpleNamespace(_continue='page-2'))
            return SimpleNamespace(items=[pod('mig', init_gpu={'nvidia.com/mig-1g.10gb': '1'})], metadata=SimpleNamespace(_continue=None))
    api = FakeAPI()
    result = discover_workloads(api, 'research')
    assert [item['pod'] for item in result] == ['gpu', 'mig']
    assert result[0]['owners'] == [{'kind': 'Job', 'name': 'job1', 'uid': 'job-uid'}]
    assert result[0]['node'] == 'gpu-node' and result[0]['gpu_resources']['app']['nvidia.com/gpu'] == '1'
    assert [call[1]['_continue'] for call in api.calls] == [None, 'page-2']
    assert all(call[0] == 'research' for call in api.calls)


def test_dcgm_samples_preserve_identity_and_exclude_nonfinite_values():
    body = '''# TYPE DCGM_FI_DEV_GPU_UTIL gauge
DCGM_FI_DEV_GPU_UTIL{UUID="GPU-1",namespace="research",pod="job"} 12.5
DCGM_FI_DEV_GPU_UTIL{UUID="GPU-2"} NaN
# TYPE DCGM_FI_DEV_FB_USED gauge
DCGM_FI_DEV_FB_USED{UUID="GPU-1"} 1024
unrelated_metric 5
'''
    def handle(request):
        assert request.method == 'GET'
        return httpx.Response(200, text=body)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        data = read_dcgm_metrics('http://dcgm:9400/metrics', http)
    assert [(item['name'], item['value']) for item in data] == [('DCGM_FI_DEV_GPU_UTIL', 12.5), ('DCGM_FI_DEV_FB_USED', 1024)]
    assert data[0]['labels']['pod'] == 'job' and data[0]['labels']['UUID'] == 'GPU-1'


def test_dcgm_failure_and_response_size_are_not_treated_as_empty_results():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as http:
        with pytest.raises(httpx.HTTPStatusError):
            read_dcgm_metrics('http://dcgm:9400/metrics', http)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'x' * (MAX_METRICS_BYTES + 1)))) as http:
        with pytest.raises(ValueError, match='size limit'):
            read_dcgm_metrics('http://dcgm:9400/metrics', http)
    with httpx.Client() as http:
        with pytest.raises(ValueError, match='without embedded credentials'):
            read_dcgm_metrics('http://user:password@dcgm/metrics', http)


def test_observers_against_local_http_endpoints():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import json
    import threading

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/api/v1/namespaces/research/pods'):
                payload = json.dumps({
                    'apiVersion': 'v1', 'kind': 'PodList', 'metadata': {},
                    'items': [{
                        'apiVersion': 'v1', 'kind': 'Pod',
                        'metadata': {'name': 'gpu-pod', 'namespace': 'research', 'uid': 'pod-1'},
                        'spec': {'containers': [{'name': 'training', 'resources': {'limits': {'nvidia.com/gpu': '1'}}}]},
                        'status': {'phase': 'Running'},
                    }],
                }).encode()
                content_type = 'application/json'
            elif self.path == '/metrics':
                payload = b'DCGM_FI_DEV_GPU_UTIL{UUID="GPU-1"} 17\n'
                content_type = 'text/plain'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        config = client.Configuration()
        config.host = url
        config.proxy = None
        api_client = client.ApiClient(config)
        try:
            pods = discover_workloads(client.CoreV1Api(api_client), 'research')
        finally:
            api_client.close()
        with httpx.Client(trust_env=False) as http:
            metrics = read_dcgm_metrics(f'{url}/metrics', http)
        assert pods[0]['pod'] == 'gpu-pod' and pods[0]['uid'] == 'pod-1'
        assert metrics[0]['name'] == 'DCGM_FI_DEV_GPU_UTIL' and metrics[0]['value'] == 17
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
