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
"""Helm rendering tests for configurable DHCP container probes.

The DHCP pod's probes are rendered by the ``nv-config-manager.probe`` helper from
a template-owned default handler plus ``networkDhcp.probes.<container>``. These
tests protect two properties:

* the defaults in ``values.yaml`` reproduce the probes the chart used to hardcode
  (liveness on ``/livez``, strict ``/healthcheck`` readiness, the confgen
  heartbeat exec probe), and
* operator overrides behave as documented next to ``networkDhcp.probes``:
  timing fields, ``enabled: false``, enabling an off-by-default probe, ``null`` to
  drop a default, zero values, and handler merge/replace semantics.

``test_dhcp_services.py`` separately asserts the strict-gating invariants; this
file covers configurability. Tests skip cleanly when ``helm`` is unavailable or
chart dependencies have not been vendored (``helm dependency build``).
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


def _render(*set_args: str) -> list[dict[str, Any]]:
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
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    yaml = YAML(typ="safe", pure=True)
    return [doc for doc in yaml.load_all(io.StringIO(result.stdout)) if isinstance(doc, dict)]


def _probe(docs: list[dict[str, Any]], container: str, kind: str) -> Any:
    """Return the rendered ``<kind>Probe`` of a DHCP container, or None if absent."""
    for doc in docs:
        if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == _DHCP_NAME:
            for candidate in doc["spec"]["template"]["spec"]["containers"]:
                if candidate["name"] == container:
                    return candidate.get(f"{kind}Probe")
    raise AssertionError(f"container {container} of {_DHCP_NAME} not rendered")


def _http(path: str, port: int) -> dict[str, Any]:
    return {"path": path, "port": port}


_CONFGEN_HANDLER = {"command": ["nv-config-manager-dhcp-confgen", "check-sync-heartbeat"]}

# (container, kind, expected probe). The container key in values differs from the
# rendered container name only for confgen ("config-sync-v4").
_DEFAULTS: list[tuple[str, str, dict[str, Any] | None]] = [
    (
        "kea",
        "liveness",
        {
            "httpGet": _http("/livez", 9090),
            "failureThreshold": 10,
            "initialDelaySeconds": 120,
            "periodSeconds": 10,
            "timeoutSeconds": 3,
        },
    ),
    ("kea", "readiness", None),
    ("kea", "startup", None),
    (
        "healthcheck",
        "liveness",
        {
            "httpGet": _http("/livez", 9090),
            "failureThreshold": 10,
            "initialDelaySeconds": 120,
            "periodSeconds": 10,
            "timeoutSeconds": 3,
        },
    ),
    (
        "healthcheck",
        "readiness",
        {
            "httpGet": _http("/healthcheck", 9090),
            "failureThreshold": 10,
            "initialDelaySeconds": 10,
            "periodSeconds": 10,
            "timeoutSeconds": 3,
        },
    ),
    ("healthcheck", "startup", None),
    (
        "api",
        "liveness",
        {
            "httpGet": _http("/livez", 9000),
            "failureThreshold": 10,
            "initialDelaySeconds": 120,
            "periodSeconds": 10,
            "timeoutSeconds": 3,
        },
    ),
    (
        "api",
        "readiness",
        {
            "httpGet": _http("/healthcheck", 9000),
            "failureThreshold": 10,
            "initialDelaySeconds": 10,
            "periodSeconds": 10,
            "timeoutSeconds": 3,
        },
    ),
    ("api", "startup", None),
    (
        "config-sync-v4",
        "liveness",
        {
            "exec": _CONFGEN_HANDLER,
            "initialDelaySeconds": 180,
            "periodSeconds": 30,
            "timeoutSeconds": 10,
            "failureThreshold": 10,
        },
    ),
    ("config-sync-v4", "readiness", None),
    ("config-sync-v4", "startup", None),
]


@pytest.mark.parametrize(
    ("container", "kind", "expected"),
    _DEFAULTS,
    ids=[f"{c}-{k}" for c, k, _ in _DEFAULTS],
)
def test_default_probes_match_previous_hardcoded_values(
    container: str, kind: str, expected: dict[str, Any] | None
) -> None:
    """Default values.yaml must render exactly the probes the chart used to hardcode."""
    assert _probe(_render(), container, kind) == expected


def test_timing_overrides_are_applied_and_isolated_per_container() -> None:
    """Fields are set per container; the default handler and other timings are kept."""
    docs = _render(
        "networkDhcp.probes.api.liveness.failureThreshold=20",
        "networkDhcp.probes.api.readiness.periodSeconds=5",
    )
    live = _probe(docs, "api", "liveness")
    assert live["failureThreshold"] == 20
    assert live["httpGet"] == _http("/livez", 9000)
    assert live["initialDelaySeconds"] == 120
    assert _probe(docs, "api", "readiness")["periodSeconds"] == 5
    # The healthcheck container is configured independently.
    assert _probe(docs, "healthcheck", "liveness")["failureThreshold"] == 10
    assert _probe(docs, "healthcheck", "readiness")["periodSeconds"] == 10


def test_enabled_false_omits_probe_and_leaves_siblings() -> None:
    docs = _render("networkDhcp.probes.kea.liveness.enabled=false")
    assert _probe(docs, "kea", "liveness") is None
    assert _probe(docs, "healthcheck", "liveness") is not None
    assert _probe(docs, "api", "readiness") is not None


@pytest.mark.parametrize(
    ("container", "rendered", "expected"),
    [
        ("kea", "kea", {"httpGet": _http("/livez", 9090)}),
        ("healthcheck", "healthcheck", {"httpGet": _http("/livez", 9090)}),
        ("api", "api", {"httpGet": _http("/livez", 9000)}),
        ("confgen", "config-sync-v4", {"exec": _CONFGEN_HANDLER}),
    ],
)
def test_enabling_startup_uses_the_container_default_handler(
    container: str, rendered: str, expected: dict[str, Any]
) -> None:
    docs = _render(
        f"networkDhcp.probes.{container}.startup.enabled=true",
        f"networkDhcp.probes.{container}.startup.failureThreshold=60",
    )
    assert _probe(docs, rendered, "startup") == {**expected, "failureThreshold": 60}


def test_enabling_kea_readiness_uses_the_strict_healthcheck() -> None:
    docs = _render("networkDhcp.probes.kea.readiness.enabled=true")
    assert _probe(docs, "kea", "readiness") == {"httpGet": _http("/healthcheck", 9090)}


def test_zero_and_null_values() -> None:
    """Zero is honored (not dropped as 'empty'); null removes a default field."""
    docs = _render(
        "networkDhcp.probes.healthcheck.readiness.initialDelaySeconds=0",
        "networkDhcp.probes.healthcheck.readiness.timeoutSeconds=null",
    )
    ready = _probe(docs, "healthcheck", "readiness")
    assert ready["initialDelaySeconds"] == 0
    assert "timeoutSeconds" not in ready
    assert ready["failureThreshold"] == 10


def test_same_handler_type_is_merged_over_default() -> None:
    """Overriding httpGet.path alone must keep the default port."""
    docs = _render("networkDhcp.probes.api.readiness.httpGet.path=/other")
    assert _probe(docs, "api", "readiness")["httpGet"] == _http("/other", 9000)


def test_different_handler_type_replaces_default() -> None:
    docs = _render("networkDhcp.probes.api.liveness.tcpSocket.port=9000")
    live = _probe(docs, "api", "liveness")
    assert live["tcpSocket"] == {"port": 9000}
    assert "httpGet" not in live
    assert live["failureThreshold"] == 10


def test_exec_command_override_replaces_command() -> None:
    docs = _render("networkDhcp.probes.confgen.liveness.exec.command={echo}")
    live = _probe(docs, "config-sync-v4", "liveness")
    assert live["exec"] == {"command": ["echo"]}
    assert live["initialDelaySeconds"] == 180
