# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Helm rendering tests for the DHCP strict traffic-gating Services and probes.

These render ``deploy/helm/templates/dhcp.yaml`` with ``helm template`` and
assert on the resulting Kubernetes objects. They protect the invariants that
keep an unconfigured DHCP pod from becoming a Ready external DHCP target:

* the external DHCP LoadBalancer selects ONLY Ready pods and never exposes the
  Kea control port (8000);
* ONLY the internal Kea bootstrap/validation Service sets
  ``publishNotReadyAddresses`` and exposes ONLY port 8000;
* the config-refresh container is pointed at that bootstrap Service;
* container liveness uses ``/livez`` (Kea process) while readiness uses the
  strict ``/healthcheck`` (Kea online AND desired config applied).

The tests skip cleanly when ``helm`` is unavailable or chart dependencies have
not been vendored (``helm dependency build``), so they never block a plain
``uv run pytest`` in a minimal environment.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

_CHART_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"
_TEMPLATE = "templates/dhcp.yaml"
_RELEASE = "test"
_DHCP_NAME = "test-nv-config-manager-dhcp"


def _render(*set_args: str) -> str:
    """Render the DHCP template with values-ci.yaml, or skip if unrenderable."""
    if shutil.which("helm") is None:
        pytest.skip("helm binary not available")
    if not (_CHART_DIR / "charts").is_dir():
        pytest.skip("helm chart dependencies not vendored (run `helm dependency build`)")

    cmd = [
        "helm",
        "template",
        _RELEASE,
        str(_CHART_DIR),
        "--values",
        str(_CHART_DIR / "values-ci.yaml"),
        "--show-only",
        _TEMPLATE,
    ]
    for pair in set_args:
        cmd += ["--set", pair]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # Both environment preconditions are checked above, so a non-zero exit
        # here is a template regression -- the thing this file exists to catch.
        # Skipping would let it through silently.
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    return result.stdout


def _load_docs(rendered: str) -> list[dict[str, Any]]:
    """Parse rendered multi-document YAML into a list of non-empty objects."""
    yaml = YAML(typ="safe", pure=True)
    return [doc for doc in yaml.load_all(io.StringIO(rendered)) if isinstance(doc, dict)]


def _services(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index rendered Service objects by name."""
    return {doc["metadata"]["name"]: doc for doc in docs if doc.get("kind") == "Service"}


def _containers(docs: list[dict[str, Any]], deployment_name: str) -> dict[str, dict[str, Any]]:
    """Index the containers of a named Deployment by container name."""
    for doc in docs:
        if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == deployment_name:
            containers = doc["spec"]["template"]["spec"]["containers"]
            return {c["name"]: c for c in containers}
    raise AssertionError(f"Deployment {deployment_name} not rendered")


# NLB overrides so the external LoadBalancer Service renders.
_NLB_SET = (
    "networkDhcp.ingress.nlb.name=test-dhcp-lb",
    "networkDhcp.ingress.nlb.sg=sg-test",
    "networkDhcp.ingress.nlb.subnets=subnet-test",
)


def test_external_dhcp_service_selects_only_ready_pods() -> None:
    """External DHCP LB must not publish not-ready addresses or expose Kea 8000."""
    docs = _load_docs(_render(*_NLB_SET))
    services = _services(docs)

    external = services[f"{_DHCP_NAME}-service"]
    assert external["spec"]["type"] == "LoadBalancer"
    # Ready-only: the field must be absent or explicitly false.
    assert external["spec"].get("publishNotReadyAddresses", False) is False

    exposed_ports = {port.get("port") for port in external["spec"]["ports"]}
    # DHCP (67) + healthcheck (80 -> 9090); the Kea control port is never exposed.
    assert 67 in exposed_ports
    assert 8000 not in exposed_ports


def test_only_bootstrap_service_publishes_not_ready_and_exposes_only_kea() -> None:
    """Exactly one Service enables publishNotReadyAddresses; it exposes ONLY 8000."""
    docs = _load_docs(_render(*_NLB_SET))
    services = _services(docs)

    not_ready = {
        name
        for name, svc in services.items()
        if svc["spec"].get("publishNotReadyAddresses") is True
    }
    assert not_ready == {f"{_DHCP_NAME}-kea-bootstrap"}

    bootstrap = services[f"{_DHCP_NAME}-kea-bootstrap"]
    assert bootstrap["spec"]["type"] == "ClusterIP"
    ports = bootstrap["spec"]["ports"]
    assert [p["port"] for p in ports] == [8000]
    assert ports[0]["targetPort"] == 8000
    assert bootstrap["spec"]["selector"] == {"app": _DHCP_NAME}


def test_internal_service_is_ready_only() -> None:
    """The internal ClusterIP Service must remain Ready-only (no bootstrap leakage)."""
    docs = _load_docs(_render())
    services = _services(docs)

    internal = services[f"{_DHCP_NAME}-internal"]
    assert internal["spec"].get("publishNotReadyAddresses", False) is False


def test_config_refresh_targets_bootstrap_service() -> None:
    """config-refresh-v4 must reach Kea via the internal bootstrap Service."""
    docs = _load_docs(_render())
    containers = _containers(docs, f"{_DHCP_NAME}-refresh")
    env = {e["name"]: e.get("value") for e in containers["config-refresh-v4"].get("env", [])}

    assert env.get("NV_CONFIG_MANAGER_KEA_SERVER") == f"{_DHCP_NAME}-kea-bootstrap"
    assert env.get("NV_CONFIG_MANAGER_KEA_PORT") == "8000"


def _probe_path(container: dict[str, Any], probe: str) -> str:
    return container[probe]["httpGet"]["path"]


def test_liveness_uses_livez_and_readiness_uses_healthcheck() -> None:
    """Liveness probes gate on Kea process (/livez); readiness stays strict."""
    docs = _load_docs(_render())
    containers = _containers(docs, _DHCP_NAME)

    # Kea container liveness only checks the process, never config.
    assert _probe_path(containers["kea"], "livenessProbe") == "/livez"

    # Healthcheck sidecar + api: liveness -> /livez, readiness -> strict /healthcheck.
    for name in ("healthcheck", "api"):
        assert _probe_path(containers[name], "livenessProbe") == "/livez"
        assert _probe_path(containers[name], "readinessProbe") == "/healthcheck"

    # The config-sync backstop must not restart on a config mismatch.
    assert _probe_path(containers["config-sync-v4"], "livenessProbe") == "/livez"
