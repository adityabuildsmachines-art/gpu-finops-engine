"""Read-only Kubernetes and DCGM observations; never submits decisions."""
import argparse
from datetime import datetime, timezone
import json
import math
import sys
from urllib.parse import urlsplit

import httpx
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from kubernetes.utils.quantity import parse_quantity
from prometheus_client.parser import text_string_to_metric_families

MAX_METRICS_BYTES = 5_000_000
METRICS = {"DCGM_FI_DEV_GPU_UTIL", "DCGM_FI_DEV_FB_USED", "DCGM_FI_DEV_GPU_TEMP"}


def discover_workloads(api, namespace: str) -> list[dict]:
    """List GPU-requesting pods in one namespace, including all result pages."""
    results = []
    continuation = None
    seen_tokens = set()
    while True:
        page = api.list_namespaced_pod(namespace, limit=200, _continue=continuation, _request_timeout=10)
        for pod in page.items:
            gpu_resources = {}
            for container in (pod.spec.containers or []) + (pod.spec.init_containers or []):
                resources = container.resources
                for kind in ("requests", "limits"):
                    for name, quantity in (getattr(resources, kind, None) or {}).items():
                        if (name == "nvidia.com/gpu" or name.startswith("nvidia.com/mig-")) and parse_quantity(str(quantity)) > 0:
                            gpu_resources.setdefault(container.name, {})[name] = str(quantity)
            if not gpu_resources:
                continue
            results.append({
                "namespace": pod.metadata.namespace, "pod": pod.metadata.name,
                "uid": pod.metadata.uid, "phase": pod.status.phase,
                "node": pod.spec.node_name, "priority": pod.spec.priority,
                "owners": [{"kind": ref.kind, "name": ref.name, "uid": ref.uid}
                           for ref in (pod.metadata.owner_references or [])],
                "gpu_resources": gpu_resources,
            })
        continuation = page.metadata._continue
        if not continuation:
            return results
        if continuation in seen_tokens:
            raise ValueError("Kubernetes returned a repeated continuation token")
        seen_tokens.add(continuation)


def read_dcgm_metrics(url: str, http: httpx.Client) -> list[dict]:
    """Read selected DCGM samples, preserving labels and original sample times."""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("DCGM URL must be an HTTP(S) URL without embedded credentials")
    body = bytearray()
    with http.stream("GET", url, headers={"Accept": "text/plain"}) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > MAX_METRICS_BYTES:
                raise ValueError("DCGM response exceeds size limit")
    samples = []
    for family in text_string_to_metric_families(body.decode("utf-8")):
        for sample in family.samples:
            if (sample.name in METRICS and math.isfinite(sample.value)
                    and (sample.timestamp is None or math.isfinite(sample.timestamp))):
                samples.append({"name": sample.name, "labels": dict(sample.labels),
                                "value": sample.value, "timestamp": sample.timestamp})
    return samples


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="source", required=True)
    k8s = commands.add_parser("kubernetes", help="list GPU pods in a namespace")
    k8s.add_argument("--namespace", required=True)
    k8s.add_argument("--in-cluster", action="store_true", help="use the pod service account")
    dcgm = commands.add_parser("dcgm", help="read selected DCGM exporter metrics")
    dcgm.add_argument("--url", required=True, help="trusted exporter metrics URL")
    args = parser.parse_args(argv)
    try:
        if args.source == "kubernetes":
            if args.in_cluster:
                config.load_incluster_config()
            else:
                config.load_kube_config()
            observations = discover_workloads(client.CoreV1Api(), args.namespace)
        else:
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as http:
                observations = read_dcgm_metrics(args.url, http)
    except (ApiException, ConfigException, httpx.HTTPError, ValueError, UnicodeError) as exc:
        print(f"Observation failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    print(json.dumps({"source": args.source, "observed_at": datetime.now(timezone.utc).isoformat(),
                      "observations": observations}, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
