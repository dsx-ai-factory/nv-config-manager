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
"""Helm rendering tests for bundled NATS gateway access and its blackbox Probe.

An exporter outside the cluster cannot resolve the in-cluster NATS service, so
the Probe goes through a /healthz-only rule on the NATS gateway route when one
is rendered. Client access over WebSocket is a separate, opt-in rule on the
same route. These tests skip cleanly when ``helm`` is unavailable or chart
dependencies have not been vendored.
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
_NATS_NAME = "test-nv-config-manager-nats"
_GATEWAY_NAME = "nv-config-manager-gateway"
_BASE_SET = (
    "nautobotNats.enabled=true",
    "networkPolicy.enabled=true",
    "gateway.enabled=true",
    "gateway.baseHostname=cell.example.test",
    "monitoring.enabled=true",
    "monitoring.prometheus.namespace=prom",
    "monitoring.probes.enabled=true",
    "monitoring.probes.blackboxExporterUrl=exporter.example:443",
)
_WEBSOCKET_ON = "nautobotNats.gateway.websocket.enabled=true"
_KGATEWAY = ("gateway.type=kgateway", "gateway.createGatewayClass=false")
_HEALTH_MATCH = [{"path": {"type": "Exact", "value": "/healthz"}, "method": "GET"}]
_WEBSOCKET_MATCH = [{"path": {"type": "PathPrefix", "value": "/"}}]


def _render(*set_args: str) -> list[dict[str, Any]]:
    if shutil.which("helm") is None:
        pytest.skip("helm binary not available")
    if not (_CHART_DIR / "charts").is_dir():
        pytest.skip("helm chart dependencies not vendored (run `helm dependency build`)")

    cmd = [
        "helm",
        "template",
        "test",
        str(_CHART_DIR),
        "--values",
        str(_CHART_DIR / "values-ci.yaml"),
    ]
    for pair in (*_BASE_SET, *set_args):
        cmd += ["--set", pair]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    yaml = YAML(typ="safe")
    # The Nautobot Celery Deployment renders a duplicate label key.
    yaml.allow_duplicate_keys = True
    return [doc for doc in yaml.load_all(io.StringIO(result.stdout)) if doc]


def _find(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any] | None:
    for doc in docs:
        if doc["kind"] == kind and doc["metadata"]["name"] == name:
            return doc
    return None


def _nats_route(docs: list[dict[str, Any]]) -> dict[str, Any] | None:
    return _find(docs, "HTTPRoute", _NATS_NAME)


def _redirect_route(docs: list[dict[str, Any]]) -> dict[str, Any] | None:
    return _find(docs, "HTTPRoute", f"{_NATS_NAME}-redirect-http")


def _route_matches(docs: list[dict[str, Any]]) -> list[Any]:
    route = _nats_route(docs)
    return [] if route is None else [rule["matches"] for rule in route["spec"]["rules"]]


def _nats_probe_target(docs: list[dict[str, Any]]) -> str | None:
    probe = _find(docs, "Probe", _NATS_NAME)
    return None if probe is None else probe["spec"]["targets"]["staticConfig"]["static"][0]


def _monitor_port_open_to_prometheus(docs: list[dict[str, Any]]) -> bool:
    policy = _find(docs, "NetworkPolicy", "namespace-isolation")
    assert policy is not None
    return any(
        p.get("port") == 8222 for rule in policy["spec"]["ingress"] for p in rule.get("ports", [])
    )


def _nats_conf(docs: list[dict[str, Any]]) -> str:
    configmap = _find(docs, "ConfigMap", f"{_NATS_NAME}-config")
    assert configmap is not None
    return configmap["data"]["nats.conf"]


def _service_ports(docs: list[dict[str, Any]]) -> dict[str, int]:
    service = _find(docs, "Service", _NATS_NAME)
    assert service is not None
    return {p["name"]: p["port"] for p in service["spec"]["ports"]}


def test_probe_uses_gateway_health_rule_by_default() -> None:
    docs = _render()
    assert _nats_probe_target(docs) == "https://nats.cell.example.test/healthz"
    assert not _monitor_port_open_to_prometheus(docs)


def test_health_rule_exposes_only_exact_get_healthz() -> None:
    route = _nats_route(_render())
    assert route is not None
    assert route["spec"]["hostnames"] == ["nats.cell.example.test"]
    assert route["metadata"]["labels"]["nv-config-manager.nvidia.com/timeout-profile"] == "default"
    [rule] = route["spec"]["rules"]
    assert rule["matches"] == _HEALTH_MATCH
    assert rule["backendRefs"] == [{"name": _NATS_NAME, "port": 8222}]


def test_route_attaches_only_to_https_listener() -> None:
    docs = _render()
    route = _nats_route(docs)
    assert route is not None
    [parent] = route["spec"]["parentRefs"]
    assert parent["name"] == _GATEWAY_NAME
    assert parent["sectionName"] == "https"


def test_http_listener_redirects_to_https() -> None:
    redirect = _redirect_route(_render())
    assert redirect is not None
    assert redirect["spec"]["hostnames"] == ["nats.cell.example.test"]
    [parent] = redirect["spec"]["parentRefs"]
    assert parent["sectionName"] == "http"
    [rule] = redirect["spec"]["rules"]
    assert "backendRefs" not in rule
    assert rule["filters"] == [
        {"type": "RequestRedirect", "requestRedirect": {"scheme": "https", "statusCode": 301}}
    ]


@pytest.mark.parametrize(
    "set_args",
    [
        ("gateway.create=false",),
        ("gateway.sectionName=shared-https",),
    ],
)
def test_shared_gateway_keeps_default_parent_ref(set_args: tuple[str, ...]) -> None:
    docs = _render(*set_args)
    route = _nats_route(docs)
    assert route is not None
    [parent] = route["spec"]["parentRefs"]
    assert parent.get("sectionName") == ("shared-https" if "sectionName" in set_args[0] else None)
    assert _redirect_route(docs) is None


def test_probe_falls_back_to_in_cluster_without_health_rule() -> None:
    docs = _render("nautobotNats.gateway.healthRoute.enabled=false")
    assert _nats_route(docs) is None
    assert _redirect_route(docs) is None
    assert _nats_probe_target(docs) == f"http://{_NATS_NAME}.nv-config-manager-ci.svc:8222/healthz"
    assert _monitor_port_open_to_prometheus(docs)


def test_explicit_target_replaces_health_rule() -> None:
    docs = _render("monitoring.probes.nats.target=http://nats.example/healthz")
    assert _nats_probe_target(docs) == "http://nats.example/healthz"
    assert _nats_route(docs) is None
    assert not _monitor_port_open_to_prometheus(docs)


@pytest.mark.parametrize(
    "set_arg",
    [
        "monitoring.probes.nats.enabled=false",
        "monitoring.probes.enabled=false",
        "monitoring.probes.blackboxExporterUrl=",
        "monitoring.enabled=false",
    ],
)
def test_no_route_without_nats_probe(set_arg: str) -> None:
    docs = _render(set_arg)
    assert _nats_probe_target(docs) is None
    assert _nats_route(docs) is None
    assert _redirect_route(docs) is None


def test_no_route_or_probe_without_bundled_nats() -> None:
    docs = _render(
        "nautobotNats.enabled=false",
        "externalServices.nats.local=false",
        _WEBSOCKET_ON,
    )
    assert _nats_route(docs) is None
    assert _redirect_route(docs) is None
    assert _nats_probe_target(docs) is None


def test_websocket_off_by_default() -> None:
    docs = _render()
    assert _route_matches(docs) == [_HEALTH_MATCH]
    assert "websocket" not in _nats_conf(docs)
    assert "websocket" not in _service_ports(docs)
    assert _find(docs, "BackendTrafficPolicy", f"{_NATS_NAME}-websocket-policy") is None


def test_websocket_rule_joins_health_rule() -> None:
    docs = _render(_WEBSOCKET_ON)
    route = _nats_route(docs)
    assert route is not None
    assert route["metadata"]["labels"]["nv-config-manager.nvidia.com/timeout-profile"] == "stream"
    assert _route_matches(docs) == [_WEBSOCKET_MATCH, _HEALTH_MATCH]
    assert route["spec"]["rules"][0]["backendRefs"] == [{"name": _NATS_NAME, "port": 8080}]
    assert _service_ports(docs)["websocket"] == 8080
    conf = _nats_conf(docs)
    assert "websocket {" in conf
    assert "port: 8080" in conf
    assert "no_tls: true" in conf


def test_websocket_rule_without_nats_probe() -> None:
    docs = _render(_WEBSOCKET_ON, "monitoring.probes.enabled=false")
    assert _route_matches(docs) == [_WEBSOCKET_MATCH]
    assert _redirect_route(docs) is not None


def test_websocket_listener_without_gateway_has_no_route() -> None:
    docs = _render(_WEBSOCKET_ON, "gateway.enabled=false")
    assert _nats_route(docs) is None
    assert "websocket {" in _nats_conf(docs)
    assert _service_ports(docs)["websocket"] == 8080


def test_websocket_timeouts_envoy_gateway() -> None:
    docs = _render(_WEBSOCKET_ON)
    policy = _find(docs, "BackendTrafficPolicy", f"{_NATS_NAME}-websocket-policy")
    assert policy is not None
    assert policy["spec"]["targetRef"]["name"] == _NATS_NAME
    assert policy["spec"]["timeout"]["http"]["requestTimeout"] == "0s"
    assert policy["spec"]["timeout"]["http"]["connectionIdleTimeout"] == "300s"
    assert _find(docs, "ListenerPolicy", "test-nv-config-manager-websocket-upgrades") is None


def test_websocket_timeouts_kgateway() -> None:
    docs = _render(_WEBSOCKET_ON, *_KGATEWAY)
    policy = _find(docs, "TrafficPolicy", "test-nv-config-manager-stream-timeouts")
    assert policy is not None
    [selector] = policy["spec"]["targetSelectors"]
    assert selector["matchLabels"] == {"nv-config-manager.nvidia.com/timeout-profile": "stream"}
    assert policy["spec"]["timeouts"] == {"request": "0s", "streamIdle": "300s"}


def test_websocket_upgrades_enabled_on_kgateway_listener() -> None:
    docs = _render(_WEBSOCKET_ON, *_KGATEWAY)
    policy = _find(docs, "ListenerPolicy", "test-nv-config-manager-websocket-upgrades")
    assert policy is not None
    assert policy["spec"]["targetRefs"] == [
        {"group": "gateway.networking.k8s.io", "kind": "Gateway", "name": _GATEWAY_NAME}
    ]
    assert policy["spec"]["default"]["httpSettings"]["upgradeConfig"] == {
        "enabledUpgrades": ["websocket"]
    }


@pytest.mark.parametrize(
    "set_args",
    [
        _KGATEWAY,
        (_WEBSOCKET_ON, *_KGATEWAY, "gateway.create=false"),
    ],
)
def test_no_kgateway_listener_policy_without_owned_websocket_gateway(
    set_args: tuple[str, ...],
) -> None:
    docs = _render(*set_args)
    assert _find(docs, "ListenerPolicy", "test-nv-config-manager-websocket-upgrades") is None
