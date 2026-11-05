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
"""Old proxy defaults migrate while intentional image and pull overrides survive."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[3] / "deploy/helm"


def render(*settings: str) -> list[dict]:
    """Render OIDC-enabled chart fixtures using the CI values."""
    if shutil.which("helm") is None or not (CHART / "charts").is_dir():
        pytest.skip("Helm and vendored chart dependencies are required")
    command = [
        "helm",
        "template",
        "test",
        str(CHART),
        "--values",
        str(CHART / "values-ci.yaml"),
        "--set",
        "oidc.enabled=true",
        "--set-string",
        "oidc.issuerUrl=https://issuer.example",
        "--set-string",
        "oidc.clientId=ci",
    ]
    for setting in settings:
        command.extend(["--set-string", setting])
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def proxies(documents: list[dict]) -> list[dict]:
    """Extract OAuth2 Proxy containers from rendered Kubernetes deployments."""
    return [
        container
        for document in documents
        if document.get("kind") == "Deployment"
        for container in document["spec"]["template"]["spec"]["containers"]
        if any(
            str(arg).startswith("--config=/etc/oauth2-proxy/") for arg in container.get("args", [])
        )
    ]


def test_old_upstream_defaults_select_the_wrapper() -> None:
    """Migrate historical upstream defaults to the project CA wrapper."""
    containers = proxies(
        render(
            "oidc.proxy.image.repository=quay.io/oauth2-proxy/oauth2-proxy",
            "oidc.proxy.image.tag=v7.15.5",
        )
    )
    assert containers
    assert all("nv-config-manager-oidc-proxy:" in container["image"] for container in containers)


def test_old_values_without_the_new_global_key_reuse_the_project_mirror() -> None:
    """Infer the wrapper mirror when reused values predate its global image key."""
    containers = proxies(
        render(
            "oidc.proxy.image.repository=quay.io/oauth2-proxy/oauth2-proxy",
            "oidc.proxy.image.tag=v7.15.5",
            "global.images.oidcProxy=",
            "global.images.nvConfigManager.repository=mirror.example/project/nv-config-manager",
        )
    )
    assert containers
    assert all(
        container["image"].startswith("mirror.example/project/nv-config-manager-oidc-proxy:")
        for container in containers
    )


def test_default_wrapper_repository_follows_the_project_registry() -> None:
    """Use the configured project registry without a separate wrapper override."""
    containers = proxies(
        render("global.images.nvConfigManager.repository=mirror.example/project/nv-config-manager")
    )
    assert containers
    assert all(
        container["image"].startswith("mirror.example/project/nv-config-manager-oidc-proxy:")
        for container in containers
    )


def test_explicit_wrapper_repository_and_digest_take_precedence() -> None:
    """Keep explicit wrapper pins when the main image uses a different naming scheme."""
    digest = "sha256:" + "a" * 64
    containers = proxies(
        render(
            "global.images.nvConfigManager.repository=mirror.example/project/custom-main",
            "global.images.oidcProxy.repository=mirror.example/project/custom-proxy",
            f"global.images.oidcProxy.digest={digest}",
        )
    )
    assert containers
    assert all(
        container["image"] == f"mirror.example/project/custom-proxy@{digest}"
        for container in containers
    )


@pytest.mark.parametrize("policy", ["Never", "IfNotPresent"])
def test_legacy_pull_policy_is_preserved(policy: str) -> None:
    """Preserve explicit legacy pull policies during image migration."""
    containers = proxies(render(f"oidc.proxy.image.pullPolicy={policy}"))
    assert containers
    assert all(container["imagePullPolicy"] == policy for container in containers)


def test_custom_mirror_keeps_its_historical_tag() -> None:
    """Retain intentional custom repositories and their historical upstream tag."""
    containers = proxies(render("oidc.proxy.image.repository=custom.example/proxy"))
    assert containers
    assert all(container["image"] == "custom.example/proxy:v7.15.5" for container in containers)


@pytest.mark.parametrize(
    ("settings", "warn", "expected_image"),
    [
        ({}, False, None),
        ({"oidc.proxy.image.repository": "mirror.example/custom"}, True, None),
        (
            {
                "global.images.oidcProxy.repository": "quay.io/oauth2-proxy/oauth2-proxy",
                "global.images.oidcProxy.tag": "v7.15.5",
            },
            True,
            "quay.io/oauth2-proxy/oauth2-proxy:v7.15.5",
        ),
        (
            {
                "global.images.oidcProxy.repository": "quay.io/oauth2-proxy/oauth2-proxy",
                "global.images.oidcProxy.digest": "sha256:" + "a" * 64,
            },
            True,
            "quay.io/oauth2-proxy/oauth2-proxy@sha256:" + "a" * 64,
        ),
        (
            {"global.images.oidcProxy.repository": "mirror.example/nv-config-manager-oidc-proxy"},
            False,
            None,
        ),
        (
            {
                "global.images.oidcProxy.repository": "mirror.example/proxy",
                "global.images.oidcProxy.tag": "v7.15.5",
                "global.images.oidcProxy.installerImageOverride": True,
            },
            True,
            "mirror.example/proxy:v7.15.5",
        ),
        (
            {
                "global.images.oidcProxy.repository": "mirror.example/proxy",
                "global.images.oidcProxy.tag": "v7.15.5",
                "global.images.oidcProxy.installerImageOverride": False,
            },
            False,
            "mirror.example/proxy:v7.15.5",
        ),
        (
            {
                "global.images.oidcProxy.repository": "mirror.example/wrapper",
                "global.images.oidcProxy.tag": "1.4.2",
                "global.images.oidcProxy.installerImageOverride": True,
            },
            True,
            "mirror.example/wrapper:1.4.2",
        ),
        (
            {
                "global.images.oidcProxy.repository": "mirror.example/wrapper",
                "global.images.oidcProxy.tag": "1.4.2",
                "global.images.oidcProxy.installerImageOverride": True,
                "oidc.proxy.image.repository": "mirror.example/legacy",
                "oidc.proxy.image.tag": "v7.15.5",
            },
            True,
            "mirror.example/legacy:v7.15.5",
        ),
        (
            {
                "global.images.oidcProxy.repository": "mirror.example/wrapper",
                "global.images.oidcProxy.tag": "1.4.2",
                "global.images.oidcProxy.installerImageOverride": True,
                "oidc.proxy.image.repository": "",
                "oidc.proxy.image.tag": "",
            },
            True,
            "mirror.example/wrapper:1.4.2",
        ),
    ],
)
def test_custom_image_warning_is_visible_in_helm_notes(
    tmp_path: Path, settings: dict[str, str | bool], warn: bool, expected_image: str | None
) -> None:
    """Warn for installer overrides and known upstream images without changing image pins."""
    if shutil.which("helm") is None:
        pytest.skip("Helm is required")
    (tmp_path / "templates").mkdir()
    (tmp_path / "Chart.yaml").write_text("apiVersion: v2\nname: notes-test\nversion: 1.0.0\n")
    shutil.copyfile(CHART / "values.yaml", tmp_path / "values.yaml")
    shutil.copyfile(CHART / "templates/_helpers.tpl", tmp_path / "templates/_helpers.tpl")
    notes = (CHART / "templates/NOTES.txt").read_text()
    (tmp_path / "templates/check.yaml").write_text(
        '{{- define "reviewed-notes" -}}\n'
        + notes
        + "\n{{- end -}}\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: notes\ndata:\n"
        + '  text: |{{ include "reviewed-notes" . | nindent 4 }}\n'
        + '  image: {{ include "nv-config-manager.oidcProxyImage" . | quote }}\n'
    )
    command = [
        "helm",
        "template",
        "test",
        str(tmp_path),
        "--values",
        str(CHART / "values-ci.yaml"),
        "--set",
        "oidc.enabled=true",
    ]
    for key, value in settings.items():
        if isinstance(value, bool):
            command.extend(["--set", f"{key}={str(value).lower()}"])
        else:
            command.extend(["--set-string", f"{key}={value}"])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    rendered = yaml.safe_load(result.stdout)["data"]
    assert ("WARNING:" in rendered["text"]) is warn
    if settings.get("global.images.oidcProxy.installerImageOverride") is True:
        assert "Verify CA stores" in rendered["text"]
        assert "custom images.overrides.oidcProxy repository" in rendered["text"]
        assert "compatibility image/tag" in rendered["text"]
        assert "Clear the legacy" in rendered["text"]
        assert "oidc.proxy.image.repository and oidc.proxy.image.tag" in rendered["text"]
        assert "regenerate Helm values" in rendered["text"]
        assert "global.images.oidcProxy selects the wrapper" in rendered["text"]
    if expected_image:
        assert rendered["image"] == expected_image


@pytest.mark.parametrize(
    "settings",
    [
        (),
        ("gateway.enabled=true", "oidc.enabled=false"),
        ("gateway.enabled=false", "oidc.enabled=true"),
    ],
)
def test_disabled_oidc_notes_accept_minimal_chart_values(
    tmp_path: Path, settings: tuple[str, ...]
) -> None:
    """Render the shard-warning test's minimal chart without unrelated image defaults."""
    if shutil.which("helm") is None:
        pytest.skip("Helm is required")
    (tmp_path / "templates").mkdir()
    (tmp_path / "Chart.yaml").write_text("apiVersion: v2\nname: notes-test\nversion: 1.0.0\n")
    for filename in ("_helpers.tpl", "NOTES.txt"):
        shutil.copyfile(CHART / "templates" / filename, tmp_path / "templates" / filename)
    command = [
        "helm",
        "template",
        "test",
        str(tmp_path),
        "--values",
        str(CHART / "values-ci.yaml"),
        "--set",
        "temporal.enabled=true",
        "--set",
        "temporal.server.enabled=true",
    ]
    for setting in settings:
        command.extend(["--set", setting])
    subprocess.run(command, check=True, capture_output=True, text=True)


def test_managed_server_authorization_is_separate_from_workflow_rbac() -> None:
    """Keep managed Temporal gRPC authorization separate from workflow RBAC."""
    documents = render(
        "rbac.workflows[0].name=Example",
        "rbac.workflows[0].read_roles[0]=readers",
        "rbac.workflows[0].execute_roles[0]=writers",
    )
    server = next(
        document
        for document in documents
        if document.get("kind") == "ConfigMap"
        and "config_template.yaml" in document.get("data", {})
    )
    assert "authorization:" not in server["data"]["config_template.yaml"]
    assert any(
        document.get("kind") == "ConfigMap" and "rbac.yaml" in document.get("data", {})
        for document in documents
    )
