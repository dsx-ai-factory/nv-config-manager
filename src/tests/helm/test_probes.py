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
"""Helm rendering tests for configurable container probes.

Container probes are rendered by the ``nv-config-manager.probe`` helper from a
chart-owned default handler plus the component's ``probes`` values. These tests
protect two properties:

* the defaults in ``values.yaml`` reproduce the probes the chart rendered before
  they were configurable (a representative table per component), and
* operator overrides behave as documented at the top of ``values.yaml``:
  timing fields, ``enabled: false``, enabling an off-by-default probe, ``null``
  to drop a default, zero values, and handler merge/replace semantics.

The tests skip cleanly when ``helm`` is unavailable or chart dependencies have
not been vendored (``helm dependency build``).
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
_RELEASE = "test"
_PREFIX = "test-nv-config-manager"

_HEALTHCHECK_HTTP = {"path": "/healthcheck", "port": 9000}
_SFTP_COMMAND = [
    "sshpass",
    "-p",
    "",
    "sftp",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-P",
    "2222",
    "healthcheck@localhost:/healthcheck",
    "/dev/null",
]


def _render(template: str, *set_args: str, values: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Render one template with values-ci.yaml and return the parsed objects."""
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
        f"templates/{template}",
    ]
    for path in values:
        cmd += ["--values", path]
    for pair in set_args:
        cmd += ["--set", pair]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    yaml = YAML(typ="safe", pure=True)
    # The rendered nautobot celery Deployment already carries a duplicate
    # app.kubernetes.io/component label key; that is unrelated to probes.
    yaml.allow_duplicate_keys = True
    return [doc for doc in yaml.load_all(io.StringIO(result.stdout)) if isinstance(doc, dict)]


def _container(docs: list[dict[str, Any]], deployment: str, container: str) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == deployment:
            for candidate in doc["spec"]["template"]["spec"]["containers"]:
                if candidate["name"] == container:
                    return candidate
    raise AssertionError(f"container {container} of {deployment} not rendered")


def _probe(docs: list[dict[str, Any]], deployment: str, container: str, kind: str) -> Any:
    """Return the rendered ``<kind>Probe`` of a container, or None if absent."""
    return _container(docs, deployment, container).get(f"{kind}Probe")


# (template, deployment, container, kind, expected probe)
_DEFAULTS: list[tuple[str, str, str, str, dict[str, Any] | None]] = [
    # render has no timing fields: it relies on Kubernetes defaults.
    (
        "render-service.yaml",
        f"{_PREFIX}-render-api",
        "api",
        "liveness",
        {"httpGet": _HEALTHCHECK_HTTP},
    ),
    (
        "render-service.yaml",
        f"{_PREFIX}-render-api",
        "api",
        "readiness",
        {"httpGet": _HEALTHCHECK_HTTP},
    ),
    ("render-service.yaml", f"{_PREFIX}-render-api", "api", "startup", None),
    (
        "config-store.yaml",
        f"{_PREFIX}-config-store-api",
        "api",
        "liveness",
        {
            "httpGet": _HEALTHCHECK_HTTP,
            "initialDelaySeconds": 30,
            "periodSeconds": 10,
            "timeoutSeconds": 5,
        },
    ),
    (
        "ui.yaml",
        f"{_PREFIX}-ui",
        "ui",
        "startup",
        {
            "httpGet": {"path": "/api/health", "port": 3000},
            "initialDelaySeconds": 30,
            "periodSeconds": 10,
            "timeoutSeconds": 3,
            "failureThreshold": 30,
        },
    ),
    (
        "ztp.yaml",
        f"{_PREFIX}-ztp",
        "http-api",
        "liveness",
        {
            "httpGet": _HEALTHCHECK_HTTP,
            "timeoutSeconds": 5,
            "periodSeconds": 15,
            "failureThreshold": 6,
        },
    ),
    (
        "ztp.yaml",
        f"{_PREFIX}-ztp",
        "sftp",
        "readiness",
        {
            "exec": {"command": _SFTP_COMMAND},
            "initialDelaySeconds": 10,
            "periodSeconds": 10,
            "timeoutSeconds": 5,
        },
    ),
    ("ztp.yaml", f"{_PREFIX}-ztp", "sftp", "startup", None),
    (
        "dhcp.yaml",
        f"{_PREFIX}-dhcp",
        "kea",
        "liveness",
        {
            "httpGet": {"path": "/livez", "port": 9090},
            "failureThreshold": 10,
            "initialDelaySeconds": 120,
            "periodSeconds": 10,
            "timeoutSeconds": 3,
        },
    ),
    ("dhcp.yaml", f"{_PREFIX}-dhcp", "kea", "readiness", None),
    (
        "dhcp.yaml",
        f"{_PREFIX}-dhcp",
        "api",
        "readiness",
        {
            "httpGet": {"path": "/healthcheck", "port": 9000},
            "failureThreshold": 10,
            "initialDelaySeconds": 10,
            "periodSeconds": 10,
            "timeoutSeconds": 3,
        },
    ),
    (
        "dhcp.yaml",
        f"{_PREFIX}-dhcp",
        "config-sync-v4",
        "liveness",
        {
            "exec": {"command": ["nv-config-manager-dhcp-confgen", "check-sync-heartbeat"]},
            "initialDelaySeconds": 180,
            "periodSeconds": 30,
            "timeoutSeconds": 10,
            "failureThreshold": 10,
        },
    ),
    (
        "temporal.yaml",
        f"{_PREFIX}-temporal-frontend",
        "temporal-frontend",
        "liveness",
        {"initialDelaySeconds": 150, "tcpSocket": {"port": "rpc"}},
    ),
    ("temporal.yaml", f"{_PREFIX}-temporal-frontend", "temporal-frontend", "readiness", None),
    (
        "temporal.yaml",
        f"{_PREFIX}-temporal-api",
        "api",
        "liveness",
        {
            "httpGet": _HEALTHCHECK_HTTP,
            "initialDelaySeconds": 30,
            "periodSeconds": 30,
            "timeoutSeconds": 5,
            "failureThreshold": 3,
        },
    ),
    (
        "nautobot.yaml",
        f"{_PREFIX}-nautobot",
        "nautobot",
        "startup",
        {
            "httpGet": {"path": "/health/", "port": "http"},
            "periodSeconds": 5,
            "timeoutSeconds": 5,
            "failureThreshold": 60,
        },
    ),
    (
        "nautobot.yaml",
        f"{_PREFIX}-nautobot",
        "nginx",
        "readiness",
        {
            "httpGet": {"path": "/stub_status", "port": "metrics"},
            "initialDelaySeconds": 5,
            "periodSeconds": 10,
        },
    ),
    (
        "redis.yaml",
        f"{_PREFIX}-redis",
        f"{_PREFIX}-redis",
        "liveness",
        {
            "exec": {
                "command": [
                    "sh",
                    "-c",
                    "REDISCLI_AUTH=$REDIS_PASSWORD redis-cli ping | grep -q PONG",
                ]
            },
            "initialDelaySeconds": 10,
            "periodSeconds": 10,
            "timeoutSeconds": 5,
        },
    ),
]


@pytest.mark.parametrize(
    ("template", "deployment", "container", "kind", "expected"),
    _DEFAULTS,
    ids=[f"{d[2]}-{d[3]}" for d in _DEFAULTS],
)
def test_default_probes_match_previous_hardcoded_values(
    template: str, deployment: str, container: str, kind: str, expected: dict[str, Any] | None
) -> None:
    """Default values.yaml must render exactly the probes the chart used to hardcode."""
    docs = _render(template)
    assert _probe(docs, deployment, container, kind) == expected


_RENDER = f"{_PREFIX}-render-api"


def test_timing_overrides_are_applied() -> None:
    """Any Kubernetes probe field can be set; the default handler is kept."""
    docs = _render(
        "render-service.yaml",
        "renderService.api.probes.liveness.timeoutSeconds=7",
        "renderService.api.probes.liveness.failureThreshold=9",
        "renderService.api.probes.readiness.successThreshold=2",
    )
    assert _probe(docs, _RENDER, "api", "liveness") == {
        "httpGet": _HEALTHCHECK_HTTP,
        "timeoutSeconds": 7,
        "failureThreshold": 9,
    }
    assert _probe(docs, _RENDER, "api", "readiness") == {
        "httpGet": _HEALTHCHECK_HTTP,
        "successThreshold": 2,
    }


def test_enabled_false_omits_probe_and_leaves_siblings() -> None:
    docs = _render("ui.yaml", "ui.probes.startup.enabled=false")
    assert _probe(docs, f"{_PREFIX}-ui", "ui", "startup") is None
    assert _probe(docs, f"{_PREFIX}-ui", "ui", "liveness") is not None
    assert _probe(docs, f"{_PREFIX}-ui", "ui", "readiness") is not None


def test_enabling_an_off_by_default_probe_uses_the_default_handler() -> None:
    docs = _render(
        "render-service.yaml",
        "renderService.api.probes.startup.enabled=true",
        "renderService.api.probes.startup.periodSeconds=5",
        "renderService.api.probes.startup.failureThreshold=60",
    )
    assert _probe(docs, _RENDER, "api", "startup") == {
        "httpGet": _HEALTHCHECK_HTTP,
        "periodSeconds": 5,
        "failureThreshold": 60,
    }


def test_zero_and_null_values() -> None:
    """Zero is honored (not dropped as 'empty'); null removes a default field."""
    docs = _render(
        "ui.yaml",
        "ui.probes.startup.initialDelaySeconds=0",
        "ui.probes.startup.timeoutSeconds=null",
    )
    startup = _probe(docs, f"{_PREFIX}-ui", "ui", "startup")
    assert startup["initialDelaySeconds"] == 0
    assert "timeoutSeconds" not in startup
    assert startup["failureThreshold"] == 30


def test_same_handler_type_is_merged_over_default() -> None:
    """Overriding httpGet.path alone must keep the default port."""
    docs = _render("render-service.yaml", "renderService.api.probes.liveness.httpGet.path=/livez")
    assert _probe(docs, _RENDER, "api", "liveness")["httpGet"] == {"path": "/livez", "port": 9000}


def test_different_handler_type_replaces_default() -> None:
    docs = _render("render-service.yaml", "renderService.api.probes.liveness.tcpSocket.port=9000")
    assert _probe(docs, _RENDER, "api", "liveness") == {"tcpSocket": {"port": 9000}}


def test_exec_command_override_replaces_command() -> None:
    docs = _render(
        "dhcp.yaml",
        "networkDhcp.probes.confgen.liveness.exec.command={echo}",
    )
    live = _probe(docs, f"{_PREFIX}-dhcp", "config-sync-v4", "liveness")
    assert live["exec"] == {"command": ["echo"]}
    assert live["initialDelaySeconds"] == 180


def test_temporal_worker_liveness_is_opt_in() -> None:
    """The server `worker` service had no probe; it can now be enabled."""
    name = f"{_PREFIX}-temporal-worker"
    default = _render("temporal.yaml")
    assert _probe(default, name, "temporal-worker", "liveness") is None

    docs = _render("temporal.yaml", "temporal.services.worker.probes.liveness.enabled=true")
    assert _probe(docs, name, "temporal-worker", "liveness") == {
        "initialDelaySeconds": 150,
        "tcpSocket": {"port": "rpc"},
    }


def test_dhcp_probes_are_independent_per_container() -> None:
    docs = _render(
        "dhcp.yaml",
        "networkDhcp.probes.api.liveness.failureThreshold=3",
        "networkDhcp.probes.kea.liveness.enabled=false",
    )
    name = f"{_PREFIX}-dhcp"
    assert _probe(docs, name, "api", "liveness")["failureThreshold"] == 3
    assert _probe(docs, name, "healthcheck", "liveness")["failureThreshold"] == 10
    assert _probe(docs, name, "kea", "liveness") is None


def test_ztp_http_containers_are_configured_separately(tmp_path: Path) -> None:
    values = tmp_path / "ztp-lb.yaml"
    values.write_text(
        "networkZtp:\n  ingress:\n    nlb:\n      type: external\n      target_type: ip\n"
    )
    docs = _render(
        "ztp.yaml",
        "networkZtp.probes.httpLb.liveness.periodSeconds=30",
        values=(str(values),),
    )
    name = f"{_PREFIX}-ztp"
    assert _probe(docs, name, "http-lb", "liveness")["periodSeconds"] == 30
    assert _probe(docs, name, "http-api", "liveness")["periodSeconds"] == 15
    assert _probe(docs, name, "http-lb", "liveness")["httpGet"]["port"] == 8000


def test_oidc_proxy_probes_configurable_without_changing_config_checksum(tmp_path: Path) -> None:
    """Probe settings live in the pod spec, so they must not alter checksum/config.

    Otherwise merely adding the probes values would restart the proxy on upgrade.
    """
    values = tmp_path / "oidc.yaml"
    values.write_text(
        "oidc:\n  enabled: true\n  issuerUrl: https://idp.example.com\n  clientId: abc\n"
    )

    def proxy(*set_args: str) -> dict[str, Any]:
        docs = _render("oidc-proxy.yaml", *set_args, values=(str(values),))
        return next(
            doc
            for doc in docs
            if doc.get("kind") == "Deployment"
            and doc["metadata"]["name"] == f"{_PREFIX}-oidc-proxy"
        )

    base = proxy()
    tuned = proxy(
        "oidc.proxy.probes.liveness.periodSeconds=20", "oidc.proxy.probes.startup.enabled=true"
    )

    def checksum(doc: dict[str, Any]) -> str:
        return doc["spec"]["template"]["metadata"]["annotations"]["checksum/config"]

    assert checksum(base) == checksum(tuned)
    first = tuned["spec"]["template"]["spec"]["containers"][0]
    assert first["livenessProbe"]["periodSeconds"] == 20
    assert first["startupProbe"]["httpGet"]["path"] == "/ping"
    assert "periodSeconds" not in base["spec"]["template"]["spec"]["containers"][0]["livenessProbe"]
