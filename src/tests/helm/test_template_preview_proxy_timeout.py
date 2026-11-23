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
"""The SSO proxy in front of Template Preview must not cut requests off before the gateway does.

OAuth2 Proxy gives up on its upstream after 30 seconds unless told otherwise. Every other proxy in
oidc-proxy.yaml passes the gateway's request timeout; Template Preview's did not, so a slow request
(a large render, or an AI assistant answer) failed with a 502 behind the proxy while the
same request worked straight through the gateway.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[3] / "deploy/helm"


def proxy_configs(*settings: str) -> dict[str, str]:
    """The OAuth2 Proxy config files by name (for example tp-preview.cfg), with Template Preview on."""
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
        "--set",
        "templatePreview.enabled=true",
    ]
    for setting in settings:
        command.extend(["--set-string", setting])
    rendered = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    configs: dict[str, str] = {}
    for document in yaml.safe_load_all(rendered):
        if document and document.get("kind") == "ConfigMap":
            configs.update(
                {k: v for k, v in (document.get("data") or {}).items() if k.endswith(".cfg")}
            )
    return configs


def upstream_timeout(config: str) -> str | None:
    match = re.search(r'^upstream_timeout = "(.+)"$', config, re.MULTILINE)
    return match.group(1) if match else None


def test_the_template_preview_proxy_waits_as_long_as_the_gateway_does():
    configs = proxy_configs()

    assert upstream_timeout(configs["tp-preview.cfg"]) == "300s"


def test_it_follows_the_gateways_request_timeout_when_that_is_changed():
    configs = proxy_configs("gateway.timeout.requestTimeout=600s")

    assert upstream_timeout(configs["tp-preview.cfg"]) == "600s"


def test_it_matches_the_other_browser_facing_proxies():
    configs = proxy_configs()

    assert upstream_timeout(configs["tp-preview.cfg"]) == upstream_timeout(configs["render.cfg"])


def _rendered_documents() -> list[dict]:
    """Every object of the chart rendered with OIDC and Template Preview on."""
    if shutil.which("helm") is None or not (CHART / "charts").is_dir():
        pytest.skip("Helm and vendored chart dependencies are required")
    command = [
        "helm", "template", "test", str(CHART),
        "--values", str(CHART / "values-ci.yaml"),
        "--set", "oidc.enabled=true",
        "--set-string", "oidc.issuerUrl=https://issuer.example",
        "--set-string", "oidc.clientId=ci",
        "--set", "templatePreview.enabled=true",
    ]  # fmt: skip
    rendered = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    return [document for document in yaml.safe_load_all(rendered) if document]


def test_every_container_port_name_and_named_target_port_fits_the_kubernetes_limit():
    # IANA service names (container port names, and a Service's named targetPort) are at most 15 characters.
    # The SSO proxy named its port after the component, and "template-preview" is 16: the shared proxy
    # Deployment would be rejected by the API server, which `helm template` does not notice.
    too_long = []
    for document in _rendered_documents():
        name = (document.get("metadata") or {}).get("name", "?")
        if document.get("kind") == "Deployment":
            for container in document["spec"]["template"]["spec"]["containers"]:
                for port in container.get("ports", []):
                    if len(port.get("name", "")) > 15:
                        too_long.append(f"{name}/{container['name']} port {port['name']}")
        if document.get("kind") == "Service":
            for port in document["spec"].get("ports", []):
                target = port.get("targetPort")
                if isinstance(target, str) and len(target) > 15:
                    too_long.append(f"{name} targetPort {target}")

    assert too_long == []
