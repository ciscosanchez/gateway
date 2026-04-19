"""Service restart helper.

Uses the docker daemon socket (mounted from the host) to restart gateway
compose services by their compose-service label. We look up containers
by label instead of container_name so we also catch scaled services like
n8n-worker which don't have a fixed container_name.

This module is the ONLY reason the admin-ui container needs docker socket
access. Access is a significant privilege — it's effectively root on the
host. We keep the container loopback-only and profile-gated to compensate.

Mapping from env-var names to which services consume them lives in
sources/env.py REGISTRY.services.
"""
from __future__ import annotations

import os
import time
from typing import Iterable

import docker
from docker.errors import APIError, NotFound

# docker.from_env() reads DOCKER_HOST. Default is unix:///var/run/docker.sock.
_client = None


def client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


PROJECT_LABEL = os.getenv("ADMIN_COMPOSE_PROJECT", "gateway")

# Known services we'll accept as restart targets. Anything outside this set
# is rejected to keep the endpoint scoped.
ALLOWED_SERVICES = {
    "n8n", "n8n-worker", "kong", "alertmanager", "grafana",
    "prometheus", "postgres", "redis", "redpanda-0",
    "redpanda-1", "redpanda-2", "redpanda-console",
    "loki", "promtail",
}


def _containers_for(service: str):
    return client().containers.list(
        all=True,
        filters={
            "label": [
                f"com.docker.compose.project={PROJECT_LABEL}",
                f"com.docker.compose.service={service}",
            ],
        },
    )


def restart(services: Iterable[str], timeout: int = 10) -> list[dict]:
    """Restart each named service. Returns one entry per container touched.

    Errors per-service are captured into the result (not raised) so a partial
    success is observable and auditable.
    """
    results: list[dict] = []
    for svc in services:
        if svc not in ALLOWED_SERVICES:
            results.append({"service": svc, "status": "rejected", "error": "not in allowlist"})
            continue
        containers = _containers_for(svc)
        if not containers:
            results.append({"service": svc, "status": "not_found", "error": f"no containers for service '{svc}'"})
            continue
        for c in containers:
            started = time.time()
            try:
                c.restart(timeout=timeout)
                results.append({
                    "service":   svc,
                    "container": c.name,
                    "status":    "restarted",
                    "took_ms":   int((time.time() - started) * 1000),
                })
            except (APIError, NotFound) as e:
                results.append({
                    "service":   svc,
                    "container": c.name,
                    "status":    "error",
                    "error":     str(e),
                })
    return results
