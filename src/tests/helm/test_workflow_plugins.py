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
"""Helm rendering contracts for workflow plugin installation."""

from __future__ import annotations

import importlib.machinery
import io
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

_CHART_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"
_TEMPLATES = ("templates/temporal.yaml", "templates/mcp.yaml")
_PLUGIN_IMAGE = (
    "temporal.workflowPlugins.images[0].image=registry.example.com/acme-workflows:1.0.0",
)
_ENABLE = (
    "temporal.workflowPlugins.enabled=true",
    "temporal.workflowPlugins.images[0].name=acme",
    *_PLUGIN_IMAGE,
)
_SECOND_PLUGIN = (
    "temporal.workflowPlugins.images[1].name=contoso",
    "temporal.workflowPlugins.images[1].image=registry.example.com/contoso-workflows:2.3.1",
)
_DCIM_ENABLE = (
    "dcim.providerPackages.enabled=true",
    "dcim.providerPackages.images[0].name=fixture",
    "dcim.providerPackages.images[0].image=registry.example.com/dcim-fixture:1.0.0",
)
# Container name in each workflow-registry process, keyed by a label for readable failures.
_REGISTRY_PROCESSES = {
    "worker": "worker",
    "temporal-api": "api",
    "scheduler": "scheduler",
    "mcp": "api",
}
_REGISTRY_DEPLOYMENT_SUFFIXES = {
    "worker": "-nv-config-manager-worker",
    "temporal-api": "-temporal-api",
    "scheduler": "-temporal-scheduler",
    "mcp": "-mcp",
}
_ARCHIVE_SUFFIX = "-temporal-archive"


def _helm_command(*set_args: str, values: tuple[str, ...] = ()) -> list[str]:
    """Build the template command from the required CI values."""
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
    for values_file in values:
        command += ["--values", str(_CHART_DIR / values_file)]
    for template in _TEMPLATES:
        command += ["--show-only", template]
    for pair in set_args:
        command += ["--set", pair]
    return command


def _run(*set_args: str, values: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _helm_command(*set_args, values=values), capture_output=True, text=True, check=False
    )


def _documents(*set_args: str, values: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Render the chart and return the Kubernetes documents."""
    result = _run(*set_args, values=values)
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    yaml = YAML(typ="safe", pure=True)
    return [document for document in yaml.load_all(io.StringIO(result.stdout)) if document]


def _render(*set_args: str) -> dict[str, dict[str, Any]]:
    """Render the chart and index Deployments by process label."""
    documents = _documents(*set_args)
    deployments = [document for document in documents if document.get("kind") == "Deployment"]
    indexed: dict[str, dict[str, Any]] = {}
    for label, suffix in _REGISTRY_DEPLOYMENT_SUFFIXES.items():
        indexed[label] = next(
            deployment
            for deployment in deployments
            if deployment["metadata"]["name"].endswith(suffix)
        )
    return indexed


def _pod_spec(deployment: dict[str, Any]) -> dict[str, Any]:
    return deployment["spec"]["template"]["spec"]


def _container(deployment: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in _pod_spec(deployment)["containers"] if item["name"] == name)


def _init_containers(deployment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in _pod_spec(deployment).get("initContainers", [])}


def _pythonpath_entries(container: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in container["env"] if item["name"] == "PYTHONPATH"]


def test_workflow_plugins_are_off_by_default() -> None:
    for label, deployment in _render().items():
        container = _container(deployment, _REGISTRY_PROCESSES[label])
        init_names = set(_init_containers(deployment))
        volume_names = {volume["name"] for volume in _pod_spec(deployment)["volumes"]}

        assert not any("workflow-plugin" in name for name in init_names), label
        assert not any("workflow-plugins" in name for name in volume_names), label
        assert not any(
            "/opt/workflow-plugins" in str(entry) for entry in _pythonpath_entries(container)
        ), label


@pytest.mark.parametrize("label", sorted(_REGISTRY_PROCESSES))
def test_every_registry_process_installs_workflow_plugins(label: str) -> None:
    deployment = _render(*_ENABLE)[label]
    container = _container(deployment, _REGISTRY_PROCESSES[label])
    init_containers = _init_containers(deployment)

    copy = init_containers["copy-workflow-plugin-acme"]
    assert copy["image"] == "registry.example.com/acme-workflows:1.0.0"
    install = init_containers["install-workflow-plugins"]
    assert list(init_containers).index("copy-workflow-plugin-acme") < list(init_containers).index(
        "install-workflow-plugins"
    )
    assert "--no-index" in install["args"][0]
    assert "--no-deps" in install["args"][0]

    mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
    assert mounts["workflow-plugins-installed"]["mountPath"] == "/opt/workflow-plugins"
    assert mounts["workflow-plugins-installed"]["readOnly"] is True
    volume_names = {volume["name"] for volume in _pod_spec(deployment)["volumes"]}
    assert {"workflow-plugins-source", "workflow-plugins-installed"} <= volume_names

    entries = _pythonpath_entries(container)
    assert [entry["value"] for entry in entries] == ["/opt/workflow-plugins:${PYTHONPATH:-}"]


@pytest.mark.parametrize("label", sorted(_REGISTRY_PROCESSES))
def test_multiple_plugin_images_install_in_one_pass(label: str) -> None:
    deployment = _render(*_ENABLE, *_SECOND_PLUGIN)[label]
    init_containers = _init_containers(deployment)
    names = list(init_containers)

    assert {"copy-workflow-plugin-acme", "copy-workflow-plugin-contoso"} <= set(names)
    assert names.count("install-workflow-plugins") == 1
    last_copy = max(names.index(name) for name in names if name.startswith("copy-workflow-plugin"))
    assert last_copy < names.index("install-workflow-plugins")


@pytest.mark.parametrize("label", sorted(_REGISTRY_PROCESSES))
def test_dcim_providers_and_workflow_plugins_share_one_pythonpath(label: str) -> None:
    deployment = _render(*_ENABLE, *_DCIM_ENABLE)[label]
    container = _container(deployment, _REGISTRY_PROCESSES[label])

    entries = _pythonpath_entries(container)
    assert [entry["value"] for entry in entries] == [
        "/opt/dcim-providers:/opt/workflow-plugins:${PYTHONPATH:-}"
    ]


def test_dcim_providers_alone_keep_their_pythonpath() -> None:
    for label, deployment in _render(*_DCIM_ENABLE).items():
        container = _container(deployment, _REGISTRY_PROCESSES[label])
        entries = _pythonpath_entries(container)
        assert [entry["value"] for entry in entries] == ["/opt/dcim-providers:${PYTHONPATH:-}"], (
            label
        )


def test_archive_does_not_load_workflow_plugins() -> None:
    result = _run(*_ENABLE, "temporal.archive.enabled=true")
    assert result.returncode == 0, result.stderr
    yaml = YAML(typ="safe", pure=True)
    archive = next(
        document
        for document in yaml.load_all(io.StringIO(result.stdout))
        if document
        and document.get("kind") == "Deployment"
        and document["metadata"]["name"].endswith(_ARCHIVE_SUFFIX)
    )

    assert "workflow-plugins" not in str(archive)


def test_enabling_workflow_plugins_requires_an_image() -> None:
    result = _run("temporal.workflowPlugins.enabled=true")

    assert result.returncode != 0
    assert "temporal.workflowPlugins.images is required" in result.stderr


def test_workflow_plugin_image_requires_a_name() -> None:
    result = _run("temporal.workflowPlugins.enabled=true", *_PLUGIN_IMAGE)

    assert result.returncode != 0
    assert "temporal.workflowPlugins.images[].name is required" in result.stderr


@pytest.mark.parametrize("label", sorted(_REGISTRY_PROCESSES))
def test_verify_step_runs_in_the_service_image_after_install(label: str) -> None:
    deployment = _render(*_ENABLE)[label]
    init_containers = _init_containers(deployment)
    names = list(init_containers)
    service_image = _container(deployment, _REGISTRY_PROCESSES[label])["image"]

    verify = init_containers["verify-workflow-plugins"]
    assert verify["image"] == service_image
    assert verify["command"] == ["python", "-c"]
    assert names.index("install-workflow-plugins") < names.index("verify-workflow-plugins")
    mounts = {mount["name"]: mount for mount in verify["volumeMounts"]}
    assert mounts["workflow-plugins-installed"]["readOnly"] is True


def _run_verify_script(
    deployment: dict[str, Any], plugin_dir: Path
) -> subprocess.CompletedProcess[str]:
    """Run the rendered verify script against a local plugin directory."""
    script = _init_containers(deployment)["verify-workflow-plugins"]["args"][0]
    script = script.replace("/opt/workflow-plugins", str(plugin_dir))
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )


def _write_dist(plugin_dir: Path, name: str, entry_points: str = "") -> None:
    dist_info = plugin_dir / f"{name.replace('-', '_')}-1.0.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0.0\n")
    if entry_points:
        (dist_info / "entry_points.txt").write_text(entry_points)


def test_verify_step_rejects_a_wheel_that_shadows_a_service_package(tmp_path: Path) -> None:
    deployment = _render(*_ENABLE)["worker"]
    _write_dist(tmp_path, "pydantic")

    result = _run_verify_script(deployment, tmp_path)

    assert result.returncode != 0
    assert "shadow packages in the service image: pydantic" in result.stderr


def test_verify_step_rejects_wheels_without_a_workflow_entry_point(tmp_path: Path) -> None:
    deployment = _render(*_ENABLE)["worker"]
    _write_dist(tmp_path, "acme-helpers")
    # Install metadata and scripts are not importable modules.
    (tmp_path / "bin").mkdir()
    (tmp_path / "acme_helpers-1.0.0.data").mkdir()

    result = _run_verify_script(deployment, tmp_path)

    assert result.returncode != 0
    assert "No nv_config_manager.workflows entry point found" in result.stderr


def _write_service_module_shadow(plugin_dir: Path, kind: str) -> None:
    """Create a top-level module named after a service module."""
    if kind == "package":
        (plugin_dir / "pydantic").mkdir()
    elif kind == "source module":
        (plugin_dir / "pydantic.py").write_text("")
    else:
        extension_suffix = importlib.machinery.EXTENSION_SUFFIXES[0]
        (plugin_dir / f"pydantic{extension_suffix}").write_bytes(b"")


@pytest.mark.parametrize("kind", ["package", "source module", "extension module"])
def test_verify_step_rejects_a_module_that_shadows_a_service_module(
    tmp_path: Path, kind: str
) -> None:
    deployment = _render(*_ENABLE)["worker"]
    # The distribution name differs, so only the module check can catch it.
    _write_dist(tmp_path, "acme-helpers")
    _write_service_module_shadow(tmp_path, kind)

    result = _run_verify_script(deployment, tmp_path)

    assert result.returncode != 0
    assert "shadow packages in the service image: pydantic" in result.stderr


def _run_install_script(
    deployment: dict[str, Any], tmp_path: Path, wheels: dict[str, list[str]]
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    """Run the rendered install script with a recording stand-in for pip."""
    packages = tmp_path / "packages"
    for image, names in wheels.items():
        wheel_dir = packages / image / "wheels"
        wheel_dir.mkdir(parents=True)
        for name in names:
            (wheel_dir / name).write_bytes(b"")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "pip-calls"
    pip = bin_dir / "pip"
    pip.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> {calls}\necho --- >> {calls}\n')
    pip.chmod(0o755)

    script = _init_containers(deployment)["install-workflow-plugins"]["args"][0]
    script = script.replace("/opt/workflow-plugin-packages", str(packages))
    script = script.replace("/opt/workflow-plugins", str(tmp_path / "installed"))
    result = subprocess.run(
        ["/bin/sh", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
    )
    if not calls.exists():
        return result, []
    invocations = calls.read_text().split("---\n")[:-1]
    return result, [invocation.split() for invocation in invocations]


def test_install_step_installs_all_plugin_wheels_in_one_pip_call(tmp_path: Path) -> None:
    deployment = _render(*_ENABLE, *_SECOND_PLUGIN)["worker"]

    result, calls = _run_install_script(
        deployment,
        tmp_path,
        {"acme": ["acme_a-1.0-py3-none-any.whl"], "contoso": ["acme_b-1.0-py3-none-any.whl"]},
    )

    assert result.returncode == 0, result.stderr
    assert len(calls) == 1
    wheel_args = [arg for arg in calls[0] if arg.endswith(".whl")]
    assert sorted(Path(arg).name for arg in wheel_args) == [
        "acme_a-1.0-py3-none-any.whl",
        "acme_b-1.0-py3-none-any.whl",
    ]
    assert {"--no-index", "--no-deps"} <= set(calls[0])


def test_install_step_fails_when_no_wheels_were_copied(tmp_path: Path) -> None:
    deployment = _render(*_ENABLE)["worker"]

    result, calls = _run_install_script(deployment, tmp_path, {})

    assert result.returncode != 0
    assert calls == []
    assert "No workflow plugin wheels found" in result.stderr


_LONG_NAME = "a" * 60
_COPY_PREFIX = "copy-workflow-plugin-"
_MAX_CONTAINER_NAME = 63


def test_long_plugin_names_still_give_valid_container_names() -> None:
    values = (
        "temporal.workflowPlugins.enabled=true",
        f"temporal.workflowPlugins.images[0].name={_LONG_NAME}",
        *_PLUGIN_IMAGE,
    )

    for label, deployment in _render(*values).items():
        copy_names = [
            name for name in _init_containers(deployment) if name.startswith(_COPY_PREFIX)
        ]
        assert len(copy_names) == 1, label
        assert len(copy_names[0]) <= _MAX_CONTAINER_NAME, label


def test_plugin_names_that_collide_after_truncation_are_rejected() -> None:
    prefix = "b" * 42
    result = _run(
        "temporal.workflowPlugins.enabled=true",
        f"temporal.workflowPlugins.images[0].name={prefix}one",
        "temporal.workflowPlugins.images[0].image=registry.example.com/one:1",
        f"temporal.workflowPlugins.images[1].name={prefix}two",
        "temporal.workflowPlugins.images[1].image=registry.example.com/two:1",
    )

    assert result.returncode != 0
    assert "collides with another image" in result.stderr


def _security_context(container: dict[str, Any]) -> dict[str, Any]:
    return container["securityContext"]


@pytest.mark.parametrize("label", sorted(_REGISTRY_PROCESSES))
def test_every_workflow_plugin_init_container_drops_privileges(label: str) -> None:
    deployment = _render(*_ENABLE, *_SECOND_PLUGIN)[label]
    init_containers = _init_containers(deployment)
    plugin_containers = [
        container for name, container in init_containers.items() if "workflow-plugin" in name
    ]

    assert len(plugin_containers) == 4
    for container in plugin_containers:
        context = _security_context(container)
        assert context["allowPrivilegeEscalation"] is False, container["name"]
        assert context["runAsNonRoot"] is True, container["name"]
        assert context["capabilities"] == {"drop": ["ALL"]}, container["name"]


@pytest.mark.parametrize("label", sorted(_REGISTRY_PROCESSES))
def test_verify_step_sees_dcim_providers_but_not_workflow_plugins_on_pythonpath(
    label: str,
) -> None:
    deployment = _render(*_ENABLE, *_DCIM_ENABLE)[label]
    verify = _init_containers(deployment)["verify-workflow-plugins"]

    assert verify["env"] == [{"name": "PYTHONPATH", "value": "/opt/dcim-providers:${PYTHONPATH:-}"}]
    mounts = {mount["name"]: mount for mount in verify["volumeMounts"]}
    assert mounts["dcim-provider-packages-installed"]["mountPath"] == "/opt/dcim-providers"
    assert mounts["dcim-provider-packages-installed"]["readOnly"] is True


@pytest.mark.parametrize("label", sorted(_REGISTRY_PROCESSES))
def test_verify_step_has_no_environment_without_dcim_providers(label: str) -> None:
    deployment = _render(*_ENABLE)[label]
    verify = _init_containers(deployment)["verify-workflow-plugins"]

    assert "env" not in verify
    assert "dcim-provider-packages-installed" not in {
        mount["name"] for mount in verify["volumeMounts"]
    }


_RBAC_VALUES = ("values-rbac-open.yaml",)
_PLUGIN_ROW = (
    "temporal.workflowPlugins.rbac[0].name=AcmeWorkflow",
    "temporal.workflowPlugins.rbac[0].read_roles={all}",
    "temporal.workflowPlugins.rbac[0].execute_roles={admin}",
)


def _rbac_workflows(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    config_map = next(
        document
        for document in documents
        if document.get("kind") == "ConfigMap"
        and document["metadata"]["name"].endswith("-rbac-config")
    )
    yaml = YAML(typ="safe", pure=True)
    return yaml.load(config_map["data"]["rbac.yaml"])["workflows"]


def test_plugin_rbac_rows_are_appended_to_platform_rows() -> None:
    platform = _rbac_workflows(_documents(values=_RBAC_VALUES))
    merged = _rbac_workflows(_documents(*_PLUGIN_ROW, values=_RBAC_VALUES))

    assert merged[: len(platform)] == platform
    assert merged[len(platform) :] == [
        {"name": "AcmeWorkflow", "read_roles": ["all"], "execute_roles": ["admin"]}
    ]


def test_plugin_rbac_rows_change_the_api_pod_checksum() -> None:
    def checksum(*set_args: str) -> str:
        documents = _documents(*set_args, values=_RBAC_VALUES)
        api = next(
            document
            for document in documents
            if document.get("kind") == "Deployment"
            and document["metadata"]["name"].endswith("-temporal-api")
        )
        return api["spec"]["template"]["metadata"]["annotations"]["checksum/rbac-config"]

    assert checksum() != checksum(*_PLUGIN_ROW)


def test_plugin_rbac_row_repeating_a_platform_workflow_is_rejected() -> None:
    result = _run(
        "temporal.workflowPlugins.rbac[0].name=DeployWorkflow",
        "temporal.workflowPlugins.rbac[0].read_roles={all}",
        "temporal.workflowPlugins.rbac[0].execute_roles={all}",
        values=_RBAC_VALUES,
    )

    assert result.returncode != 0
    assert 'repeats workflow "DeployWorkflow"' in result.stderr


def test_plugin_rbac_row_requires_roles() -> None:
    result = _run("temporal.workflowPlugins.rbac[0].name=AcmeWorkflow", values=_RBAC_VALUES)

    assert result.returncode != 0
    assert "require name, read_roles, and execute_roles" in result.stderr


def test_plugin_rbac_rows_require_platform_rbac() -> None:
    result = _run(*_PLUGIN_ROW)

    assert result.returncode != 0
    assert "temporal.workflowPlugins.rbac requires rbac to be set" in result.stderr
