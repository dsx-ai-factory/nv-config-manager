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
"""Helm rendering contracts for the provider-neutral scheduler host."""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from nv_config_manager.temporal.scheduler import host

_CHART_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"
_TEMPLATE = "templates/temporal.yaml"
_SCHEDULER_DEPLOYMENT = "test-nv-config-manager-temporal-scheduler"
_NON_NAUTOBOT = (
    "secrets.method=kubernetes",
    "dcim.provider=fixture",
    "dcim.server=https://dcim.example.com",
)
_SCHEDULERS = "temporal.scheduler.schedulers"
_BACKUP_ENABLED = r"temporal.scheduler.schedulers.builtin\.backup.enabled"
_BACKUP_REQUIREMENT = r"temporal.scheduler.schedulers.builtin\.backup.requiresDcimProvider"
_FIXTURE_ENABLED = r"temporal.scheduler.schedulers.fixture\.cleanup.enabled"
_FIXTURE_REQUIREMENT = r"temporal.scheduler.schedulers.fixture\.cleanup.requiresDcimProvider"
_REQUIREMENT_ERROR = (
    "temporal.scheduler.schedulers.{identity}.requiresDcimProvider must be a non-empty string"
)


def _helm_command(
    *set_args: str,
    set_string_args: tuple[str, ...] = (),
    extra_values: tuple[str, ...] = (),
) -> list[str]:
    """Build the scheduler template command from the required CI values."""
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
        "--show-only",
        _TEMPLATE,
    ]
    for values_file in extra_values:
        command += ["--values", str(_CHART_DIR / values_file)]
    for pair in set_args:
        command += ["--set", pair]
    for pair in set_string_args:
        command += ["--set-string", pair]
    return command


def _render(
    *set_args: str,
    set_string_args: tuple[str, ...] = (),
    extra_values: tuple[str, ...] = (),
) -> str:
    """Render the Temporal template and fail on template regressions."""
    result = subprocess.run(
        _helm_command(*set_args, set_string_args=set_string_args, extra_values=extra_values),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    return result.stdout


def _render_failure(*set_args: str, set_string_args: tuple[str, ...] = ()) -> str:
    """Render the Temporal template and return helm's error output."""
    result = subprocess.run(
        _helm_command(*set_args, set_string_args=set_string_args),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, "helm template unexpectedly succeeded"
    return result.stderr


def _documents(rendered: str) -> list[dict[str, Any]]:
    """Parse non-empty Kubernetes documents from rendered YAML."""
    yaml = YAML(typ="safe", pure=True)
    return [document for document in yaml.load_all(io.StringIO(rendered)) if document]


def _scheduler_deployment(rendered: str) -> dict[str, Any] | None:
    """Return the scheduler Deployment when it is rendered."""
    for document in _documents(rendered):
        if (
            document.get("kind") == "Deployment"
            and document["metadata"]["name"] == _SCHEDULER_DEPLOYMENT
        ):
            return document
    return None


def _scheduler_container(deployment: dict[str, Any]) -> dict[str, Any]:
    """Return the scheduler host container from its Deployment."""
    return next(
        container
        for container in deployment["spec"]["template"]["spec"]["containers"]
        if container["name"] == "scheduler"
    )


def _environment(container: dict[str, Any]) -> dict[str, Any]:
    """Index literal container environment variables by name."""
    return {item["name"]: item.get("value") for item in container["env"]}


def _selected_schedulers(deployment: dict[str, Any] | None) -> str:
    """Return the scheduler selection Helm passes to the host."""
    assert deployment is not None, "scheduler Deployment was not rendered"
    return _environment(_scheduler_container(deployment))["NVCM_ENABLED_SCHEDULERS"]


def _init_container_names(deployment: dict[str, Any]) -> set[str]:
    """Return the scheduler pod init container names."""
    return {
        container["name"]
        for container in deployment["spec"]["template"]["spec"].get("initContainers", [])
    }


def test_builtin_backup_values_pin_main_helm_provider_restriction() -> None:
    """Pin main's Helm behavior for the built-in backup scheduler.

    By default the chart renders the scheduler Deployment for builtin.backup only
    when the DCIM provider is nautobot-2x. The scheduler host itself is
    provider-neutral and runs builtin.backup whenever it is selected.
    """
    values = YAML(typ="safe", pure=True).load(_CHART_DIR / "values.yaml")
    schedulers = values["temporal"]["scheduler"]["schedulers"]

    assert host.BUILTIN_BACKUP_SCHEDULER_IDENTITY in schedulers
    assert (
        schedulers[host.BUILTIN_BACKUP_SCHEDULER_IDENTITY]["requiresDcimProvider"] == "nautobot-2x"
    )


def test_nautobot_renders_backup_scheduler_deployment() -> None:
    deployment = _scheduler_deployment(_render())

    assert deployment is not None
    assert deployment["spec"]["replicas"] == 1
    pod_spec = deployment["spec"]["template"]["spec"]
    assert "wait-for-nautobot" in {container["name"] for container in pod_spec["initContainers"]}
    scheduler = _scheduler_container(deployment)
    assert scheduler["command"] == ["nv-config-manager-temporal-scheduler"]
    assert _environment(scheduler)["NVCM_ENABLED_SCHEDULERS"] == "builtin.backup"


def test_scheduler_keeps_the_shared_deployment_strategy() -> None:
    # Changing a live Deployment from the server-defaulted strategy to Recreate is
    # rejected under server-side apply, so the scheduler must follow the chart-wide
    # strategy rather than hard-coding its own.
    assert "strategy" not in _scheduler_deployment(_render())["spec"]

    deployment = _scheduler_deployment(
        _render(
            "global.deploymentStrategy.type=RollingUpdate",
            "global.deploymentStrategy.rollingUpdate.maxSurge=1",
        )
    )
    assert deployment["spec"]["strategy"]["type"] == "RollingUpdate"
    assert deployment["spec"]["strategy"]["rollingUpdate"]["maxSurge"] == 1


def test_scheduler_deployment_can_be_disabled() -> None:
    assert _scheduler_deployment(_render("temporal.scheduler.enabled=false")) is None


def test_enabling_a_plugin_scheduler_keeps_the_builtin_default() -> None:
    deployment = _scheduler_deployment(_render(f"{_FIXTURE_ENABLED}=true"))

    assert _selected_schedulers(deployment) == "builtin.backup,fixture.cleanup"


def test_non_nautobot_default_renders_no_scheduler_deployment() -> None:
    # builtin.backup requires nautobot-2x, so nothing is selected on other providers.
    assert _scheduler_deployment(_render(*_NON_NAUTOBOT)) is None


def test_non_nautobot_plugin_scheduler_runs_without_nautobot_wait() -> None:
    deployment = _scheduler_deployment(_render(*_NON_NAUTOBOT, f"{_FIXTURE_ENABLED}=true"))

    assert _selected_schedulers(deployment) == "fixture.cleanup"
    assert "wait-for-nautobot" not in _init_container_names(deployment)


def test_builtin_backup_can_be_disabled_while_plugin_schedulers_run() -> None:
    deployment = _scheduler_deployment(
        _render(f"{_BACKUP_ENABLED}=false", f"{_FIXTURE_ENABLED}=true")
    )

    assert _selected_schedulers(deployment) == "fixture.cleanup"


def test_no_selected_schedulers_renders_no_scheduler_deployment() -> None:
    assert _scheduler_deployment(_render(f"{_BACKUP_ENABLED}=false")) is None


@pytest.mark.parametrize(
    ("provider_args", "requirement", "expected"),
    [
        pytest.param((), "nautobot-2x", "builtin.backup,fixture.cleanup", id="nautobot-match"),
        pytest.param((), "fixture", "builtin.backup", id="nautobot-mismatch"),
        pytest.param(_NON_NAUTOBOT, "fixture", "fixture.cleanup", id="fixture-match"),
        pytest.param(_NON_NAUTOBOT, "nautobot-2x", None, id="fixture-mismatch"),
    ],
)
def test_plugin_scheduler_provider_requirement(
    provider_args: tuple[str, ...], requirement: str, expected: str | None
) -> None:
    deployment = _scheduler_deployment(
        _render(
            *provider_args,
            f"{_FIXTURE_ENABLED}=true",
            set_string_args=(f"{_FIXTURE_REQUIREMENT}={requirement}",),
        )
    )

    if expected is None:
        assert deployment is None
    else:
        assert _selected_schedulers(deployment) == expected


def test_null_requirement_removes_the_builtin_provider_rule() -> None:
    """A null requirement deletes the default, so Helm selects builtin.backup.

    The scheduler host is provider-neutral, so it then runs builtin.backup against
    the configured DCIM provider rather than skipping it.
    """
    deployment = _scheduler_deployment(_render(*_NON_NAUTOBOT, f"{_BACKUP_REQUIREMENT}=null"))

    assert _selected_schedulers(deployment) == "builtin.backup"
    assert "wait-for-nautobot" not in _init_container_names(deployment)


def test_scheduler_entry_must_be_a_map() -> None:
    stderr = _render_failure(
        set_string_args=("temporal.scheduler.schedulers.fixture\\.cleanup=yes",)
    )

    assert "temporal.scheduler.schedulers.fixture.cleanup must be a map" in stderr


def test_scheduler_enabled_flag_must_be_boolean() -> None:
    stderr = _render_failure(set_string_args=(f"{_FIXTURE_ENABLED}=true",))

    assert "temporal.scheduler.schedulers.fixture.cleanup.enabled must be true or false" in stderr


def test_scheduler_requirement_must_not_be_empty() -> None:
    stderr = _render_failure(set_string_args=(f"{_BACKUP_REQUIREMENT}=",))

    assert _REQUIREMENT_ERROR.format(identity="builtin.backup") in stderr


@pytest.mark.parametrize("value", ["1", "true"])
def test_scheduler_requirement_must_be_a_string(value: str) -> None:
    # Validation applies to disabled entries too.
    stderr = _render_failure(f"{_FIXTURE_ENABLED}=false", f"{_FIXTURE_REQUIREMENT}={value}")

    assert _REQUIREMENT_ERROR.format(identity="fixture.cleanup") in stderr


def test_missing_schedulers_map_fails_instead_of_removing_the_scheduler() -> None:
    # Helm deletes a null key, matching --reuse-values from a chart that predates the map.
    stderr = _render_failure(f"{_SCHEDULERS}=null")

    assert "temporal.scheduler.schedulers is missing" in stderr
    assert "--reuse-values" in stderr
    assert r"builtin\.backup.enabled=true" in stderr
    assert r"builtin\.backup.requiresDcimProvider=nautobot-2x" in stderr


def test_missing_schedulers_map_is_allowed_when_scheduler_is_disabled() -> None:
    rendered = _render(f"{_SCHEDULERS}=null", "temporal.scheduler.enabled=false")

    assert _scheduler_deployment(rendered) is None


def test_observability_overlay_renders_scheduler_telemetry() -> None:
    deployment = _scheduler_deployment(_render(extra_values=("values-observability.yaml",)))

    assert deployment is not None
    environment = _environment(_scheduler_container(deployment))
    assert environment["OTEL_SERVICE_NAME"] == "nv-config-manager-temporal-scheduler"
    assert environment["NVCM_ENABLED_SCHEDULERS"] == "builtin.backup"
