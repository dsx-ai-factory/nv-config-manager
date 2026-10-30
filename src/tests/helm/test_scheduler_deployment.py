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
"""Helm rendering contracts for scheduler and attribution workloads."""

from __future__ import annotations

import io
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

_CHART_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"
_TEMPORAL_TEMPLATE = "templates/temporal.yaml"
_RENDER_TEMPLATE = "templates/render-service.yaml"
_VAULT_SECRETS_TEMPLATE = "templates/vault-secrets.yaml"
_KUBERNETES_SECRETS_TEMPLATE = "templates/kubernetes-secrets.yaml"
_VAULT_AGENT_CONFIGMAP_TEMPLATE = "templates/vault-agent-configmap.yaml"
_SCHEDULER_DEPLOYMENT = "test-nv-config-manager-temporal-scheduler"
_ATTRIBUTION_WORKLOADS = (
    ("Deployment", "test-nv-config-manager-temporal-api", "api"),
    ("Deployment", "test-nv-config-manager-temporal-nv-config-manager-worker", "worker"),
    ("Deployment", "test-nv-config-manager-render-api", "api"),
    ("Deployment", "test-nv-config-manager-render-consumer-nautobot", "consumer"),
    ("Deployment", "test-nv-config-manager-render-consumer-device", "consumer"),
    ("Job", "test-nv-config-manager-render-template-updater", "template-updater"),
)
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
    templates: tuple[str, ...] = (),
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
    ]
    if not templates:
        secret_method = next(
            (pair.split("=", 1)[1] for pair in set_args if pair.startswith("secrets.method=")),
            "eso",
        )
        ini_template = {
            "eso": _VAULT_SECRETS_TEMPLATE,
            "kubernetes": _KUBERNETES_SECRETS_TEMPLATE,
            "vault-agent": _VAULT_AGENT_CONFIGMAP_TEMPLATE,
        }[secret_method]
        templates = (_TEMPORAL_TEMPLATE, ini_template)
    for template in templates:
        command += ["--show-only", template]
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
    templates: tuple[str, ...] = (),
) -> str:
    """Render selected templates and fail on template regressions."""
    result = subprocess.run(
        _helm_command(
            *set_args,
            set_string_args=set_string_args,
            extra_values=extra_values,
            templates=templates,
        ),
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


def _workload_container(
    rendered: str, kind: str, workload_name: str, container_name: str
) -> dict[str, Any]:
    """Return one container from a named rendered workload."""
    for document in _documents(rendered):
        if document.get("kind") == kind and document["metadata"]["name"] == workload_name:
            return next(
                container
                for container in document["spec"]["template"]["spec"]["containers"]
                if container["name"] == container_name
            )
    raise AssertionError(f"{kind} {workload_name} was not rendered")


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


def _selected_schedulers(rendered: str) -> str:
    """Return the selection written to INI and require no environment override."""
    deployment = _scheduler_deployment(rendered)
    assert deployment is not None, "scheduler Deployment was not rendered"
    assert "NVCM_ENABLED_SCHEDULERS" not in _environment(_scheduler_container(deployment))
    matches = re.findall(r"^\s*enabled_schedulers =([^\n]*)$", rendered, re.MULTILINE)
    assert len(matches) == 1
    return matches[0].strip()


def _init_container_names(deployment: dict[str, Any]) -> set[str]:
    """Return the scheduler pod init container names."""
    return {
        container["name"]
        for container in deployment["spec"]["template"]["spec"].get("initContainers", [])
    }


def test_nautobot_renders_backup_scheduler_deployment() -> None:
    rendered = _render()
    deployment = _scheduler_deployment(rendered)

    assert deployment is not None
    assert deployment["spec"]["replicas"] == 1
    pod_spec = deployment["spec"]["template"]["spec"]
    assert "wait-for-nautobot" in {container["name"] for container in pod_spec["initContainers"]}
    scheduler = _scheduler_container(deployment)
    assert scheduler["command"] == ["nv-config-manager-temporal-scheduler"]
    assert _selected_schedulers(rendered) == "builtin.backup"


def test_attribution_workloads_use_ini_instead_of_domain_env() -> None:
    rendered = _render(
        set_string_args=("global.defaultUserDomain=ci.example.com",),
        templates=(_TEMPORAL_TEMPLATE, _RENDER_TEMPLATE),
    )

    for kind, workload, container in _ATTRIBUTION_WORKLOADS:
        environment = _environment(_workload_container(rendered, kind, workload, container))
        assert "NV_CONFIG_MANAGER_DOMAIN" not in environment


def test_unset_user_domain_is_not_injected() -> None:
    rendered = _render(templates=(_TEMPORAL_TEMPLATE, _RENDER_TEMPLATE))

    for kind, workload, container in _ATTRIBUTION_WORKLOADS:
        environment = _environment(_workload_container(rendered, kind, workload, container))
        assert "NV_CONFIG_MANAGER_DOMAIN" not in environment


@pytest.mark.parametrize(
    ("secret_method", "template"),
    [
        ("eso", _VAULT_SECRETS_TEMPLATE),
        ("kubernetes", _KUBERNETES_SECRETS_TEMPLATE),
        ("vault-agent", _VAULT_AGENT_CONFIGMAP_TEMPLATE),
    ],
)
def test_default_user_domain_is_written_to_unified_ini(secret_method: str, template: str) -> None:
    rendered = _render(
        f"secrets.method={secret_method}",
        set_string_args=("global.defaultUserDomain=ci.example.com",),
        templates=(template,),
    )

    assert "user_domain = ci.example.com" in rendered


@pytest.mark.parametrize(
    ("secret_method", "template"),
    [
        ("eso", _VAULT_SECRETS_TEMPLATE),
        ("kubernetes", _KUBERNETES_SECRETS_TEMPLATE),
        ("vault-agent", _VAULT_AGENT_CONFIGMAP_TEMPLATE),
    ],
)
def test_application_settings_reach_every_ini_delivery_path(
    secret_method: str, template: str
) -> None:
    rendered = _render(
        f"secrets.method={secret_method}",
        "renderService.templateUpdater.concurrency=3",
        "renderService.templateUpdater.lockTimeoutSeconds=27",
        "monitoring.groupFastApiStatusCodes=false",
        "localDev.enableLocalTestWorkflows=true",
        f"{_FIXTURE_ENABLED}=true",
        templates=(template,),
    )

    for section, option, expected in (
        ("monitoring", "group_fastapi_status_codes", "false"),
        ("render.template_updater", "concurrency", "3"),
        ("render.template_updater", "lock_timeout", "27"),
        ("temporal.worker", "enable_local_test_workflows", "true"),
        ("temporal.scheduler", "enabled_schedulers", "builtin.backup,fixture.cleanup"),
    ):
        block = re.search(rf"\[{re.escape(section)}\]([^\[]*)", rendered)
        assert block is not None
        assert re.search(rf"^\s*{option} = {re.escape(expected)}\s*$", block[1], re.MULTILINE)


@pytest.mark.parametrize(
    ("secret_method", "template"),
    [
        ("eso", _VAULT_SECRETS_TEMPLATE),
        ("kubernetes", _KUBERNETES_SECRETS_TEMPLATE),
        ("vault-agent", _VAULT_AGENT_CONFIGMAP_TEMPLATE),
    ],
)
def test_domain_remains_in_ini_without_temporal(secret_method: str, template: str) -> None:
    rendered = _render(
        f"secrets.method={secret_method}",
        "temporal.enabled=false",
        set_string_args=("global.defaultUserDomain=ci.example.com",),
        templates=(template,),
    )

    assert "user_domain = ci.example.com" in rendered
    assert re.search(r"^\s*enabled_schedulers =\s*$", rendered, re.MULTILINE)


def test_migrated_and_unused_settings_are_not_workload_env_vars() -> None:
    rendered = _render(
        "localDev.enableLocalTestWorkflows=true",
        templates=(
            _TEMPORAL_TEMPLATE,
            _RENDER_TEMPLATE,
            "templates/config-store.yaml",
            "templates/dhcp.yaml",
            "templates/ztp.yaml",
        ),
    )
    for name in (
        "NV_CONFIG_MANAGER_DOMAIN",
        "NV_CONFIG_MANAGER_TEMPLATE_UPDATE_CONCURRENCY",
        "NV_CONFIG_MANAGER_TEMPLATE_UPDATE_LOCK_TIMEOUT",
        "NV_CONFIG_MANAGER_GROUP_FASTAPI_STATUS_CODES",
        "NVCM_ENABLED_SCHEDULERS",
        "NVCM_ENABLE_LOCAL_TEST_WORKFLOWS",
        "NV_CONFIG_MANAGER_DEV_SALT",
        "RETENTION_DAYS",
    ):
        assert f"name: {name}" not in rendered


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
    rendered = _render(f"{_FIXTURE_ENABLED}=true")
    deployment = _scheduler_deployment(rendered)

    assert _selected_schedulers(rendered) == "builtin.backup,fixture.cleanup"
    assert "wait-for-nautobot" in _init_container_names(deployment)


def test_non_nautobot_default_renders_no_scheduler_deployment() -> None:
    # builtin.backup requires nautobot-2x, so nothing is selected on other providers.
    assert _scheduler_deployment(_render(*_NON_NAUTOBOT)) is None


def test_non_nautobot_plugin_scheduler_runs_without_nautobot_wait() -> None:
    rendered = _render(*_NON_NAUTOBOT, f"{_FIXTURE_ENABLED}=true")
    deployment = _scheduler_deployment(rendered)

    assert _selected_schedulers(rendered) == "fixture.cleanup"
    assert "wait-for-nautobot" not in _init_container_names(deployment)


def test_builtin_backup_can_be_disabled_while_plugin_schedulers_run() -> None:
    # Only built-in schedulers receive DCIM access, so plugin schedulers must keep
    # starting while Nautobot is unavailable.
    rendered = _render(f"{_BACKUP_ENABLED}=false", f"{_FIXTURE_ENABLED}=true")
    deployment = _scheduler_deployment(rendered)

    assert _selected_schedulers(rendered) == "fixture.cleanup"
    assert "wait-for-nautobot" not in _init_container_names(deployment)


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
    rendered = _render(
        *provider_args,
        f"{_FIXTURE_ENABLED}=true",
        set_string_args=(f"{_FIXTURE_REQUIREMENT}={requirement}",),
    )
    deployment = _scheduler_deployment(rendered)

    if expected is None:
        assert deployment is None
    else:
        assert _selected_schedulers(rendered) == expected


def test_null_requirement_removes_the_builtin_provider_rule() -> None:
    """A null requirement deletes the default, so Helm selects builtin.backup.

    The scheduler host is provider-neutral, so it then runs builtin.backup against
    the configured DCIM provider rather than skipping it.
    """
    rendered = _render(*_NON_NAUTOBOT, f"{_BACKUP_REQUIREMENT}=null")
    deployment = _scheduler_deployment(rendered)

    assert _selected_schedulers(rendered) == "builtin.backup"
    assert "wait-for-nautobot" not in _init_container_names(deployment)


def test_null_requirement_keeps_the_nautobot_wait_for_builtin_backup() -> None:
    # The wait follows the built-in DCIM dependency, not the selection requirement.
    rendered = _render(f"{_BACKUP_REQUIREMENT}=null")
    deployment = _scheduler_deployment(rendered)

    assert _selected_schedulers(rendered) == "builtin.backup"
    assert "wait-for-nautobot" in _init_container_names(deployment)


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
    rendered = _render(extra_values=("values-observability.yaml",))
    deployment = _scheduler_deployment(rendered)

    assert deployment is not None
    environment = _environment(_scheduler_container(deployment))
    assert environment["OTEL_SERVICE_NAME"] == "nv-config-manager-temporal-scheduler"
    assert _selected_schedulers(rendered) == "builtin.backup"
