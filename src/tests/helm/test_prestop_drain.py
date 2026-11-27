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
"""Helm rendering contracts for the preStop drain delay.

A container must keep running briefly after its pod is deleted wherever the
gateway retry cannot hide its shutdown: traffic from a LoadBalancer Service,
writes that oauth2-proxy answers with a 502 once the app behind it is gone, and
pods where an in-pod dependency such as Kea can exit before the container in
front of it. oauth2-proxy itself is excluded, because Envoy connects to it
directly and retries the refused connection, as are workloads nothing routes to.
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
_PREFIX = "test-nv-config-manager-"
# OIDC puts oauth2-proxy in the browser path, and a ZTP LoadBalancer adds the
# device-facing http-lb container.
_BASE_SET_ARGS = (
    "oidc.enabled=true",
    "oidc.issuerUrl=https://issuer.example.invalid",
    "oidc.clientId=placeholder-client",
    "networkZtp.ingress.nlb.dns_name=ztp.example.invalid",
)
_DRAINED = {
    "config-store-api": {"api"},
    "dhcp": {"kea", "healthcheck", "api"},
    "mcp": {"api"},
    "nautobot": {"nautobot", "nginx"},
    "render-api": {"api"},
    "temporal-api": {"api"},
    "temporal-web": {f"{_PREFIX}temporal-web"},
    "ui": {"ui"},
    "ztp": {"http-api", "http-lb", "sftp"},
}


def _helm_command(
    *set_args: str,
    set_string_args: tuple[str, ...] = (),
    kube_version: str | None = None,
) -> list[str]:
    """Build a full-chart template command from the required CI values."""
    if shutil.which("helm") is None:
        pytest.skip("helm binary not available")
    if not (_CHART_DIR / "charts").is_dir():
        pytest.skip("helm chart dependencies not vendored (run `helm dependency build`)")

    command = [
        "helm",
        "template",
        "test",
        str(_CHART_DIR),
        "--values",
        str(_CHART_DIR / "values-ci.yaml"),
        "--kube-version",
        kube_version or "1.30.0",
    ]
    for pair in (*_BASE_SET_ARGS, *set_args):
        command += ["--set", pair]
    for pair in set_string_args:
        command += ["--set-string", pair]
    return command


def _render(*set_args: str, kube_version: str | None = None) -> list[dict[str, Any]]:
    """Render the chart and parse its non-empty Kubernetes documents."""
    result = subprocess.run(
        _helm_command(*set_args, kube_version=kube_version),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    yaml = YAML(typ="safe", pure=True)
    # The Nautobot celery Deployments repeat app.kubernetes.io/component in their metadata labels.
    yaml.allow_duplicate_keys = True
    return [document for document in yaml.load_all(io.StringIO(result.stdout)) if document]


def _render_failure(*set_args: str, set_string_args: tuple[str, ...] = ()) -> str:
    """Render the chart and return helm's error output."""
    result = subprocess.run(
        _helm_command(*set_args, set_string_args=set_string_args),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, "helm template unexpectedly succeeded"
    return result.stderr


def _prestop_by_container(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index each workload container's preStop hook by workload and container name."""
    hooks: dict[str, dict[str, Any]] = {}
    for document in documents:
        if document["kind"] not in ("Deployment", "StatefulSet"):
            continue
        workload = document["metadata"]["name"].removeprefix(_PREFIX)
        for container in document["spec"]["template"]["spec"]["containers"]:
            hooks[f"{workload}/{container['name']}"] = container.get("lifecycle", {}).get("preStop")
    return hooks


def _expected_drained() -> set[str]:
    return {f"{workload}/{name}" for workload, names in _DRAINED.items() for name in names}


def test_traffic_serving_containers_sleep_before_sigterm() -> None:
    hooks = _prestop_by_container(_render())

    drained = {key for key, hook in hooks.items() if hook}
    assert drained == _expected_drained()
    assert {hooks[key]["sleep"]["seconds"] for key in drained} == {10}


def test_drain_delay_follows_the_global_value() -> None:
    hooks = _prestop_by_container(_render("global.preStopSleepSeconds=5"))

    assert {hooks[key]["sleep"]["seconds"] for key in _expected_drained()} == {5}


def test_zero_disables_the_drain_delay() -> None:
    hooks = _prestop_by_container(_render("global.preStopSleepSeconds=0"))

    assert not any(hooks.values())


def test_absent_value_disables_the_drain_delay() -> None:
    # Helm deletes a key set to null, so this is also what an explicit null means.
    hooks = _prestop_by_container(_render("global.preStopSleepSeconds=null"))

    assert not any(hooks.values())


def test_drain_delay_is_omitted_before_kubernetes_1_30() -> None:
    # The native sleep action is off by default on 1.29, and the API server
    # silently drops it, so rendering it there would only hide the gap.
    hooks = _prestop_by_container(_render(kube_version="1.29.0"))

    assert not any(hooks.values())


def test_drain_delay_must_leave_room_in_the_grace_period() -> None:
    stderr = _render_failure("global.preStopSleepSeconds=21")

    assert "global.preStopSleepSeconds (21) must be at most 20" in stderr


@pytest.mark.parametrize("value", ["-1", "abc", "1.5", "false"])
def test_drain_delay_must_be_whole_seconds(value: str) -> None:
    stderr = _render_failure(f"global.preStopSleepSeconds={value}")

    assert "global.preStopSleepSeconds must be a non-negative whole number of seconds" in stderr


def test_empty_drain_delay_is_rejected_rather_than_treated_as_zero() -> None:
    stderr = _render_failure(set_string_args=("global.preStopSleepSeconds=",))

    assert "global.preStopSleepSeconds must be a non-negative whole number of seconds" in stderr
