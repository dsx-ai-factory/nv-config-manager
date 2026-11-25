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
"""Who may restart the render Deployments when a dynamic plugin set is activated.

Template Preview restarts them, so it holds that permission (``get`` and ``patch`` on exactly the
three render Deployments). The render service itself holds none: it only records the activation in
its ConfigMap.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"
GATE = (
    "renderService.unsafeDynamicTemplatePlugins.enabled=true",
    "renderService.unsafeDynamicTemplatePlugins.acknowledgeArbitraryCodeExecution=true",
)
WITH_TEMPLATE_PREVIEW = (
    *GATE,
    "templatePreview.enabled=true",
    "templatePreview.unsafeDynamicTemplatePlugins.enabled=true",
    "templatePreview.unsafeDynamicTemplatePlugins.acknowledgeArbitraryCodeExecution=true",
)

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _render(*sets: str) -> list[dict]:
    command = ["helm", "template", "test", ".", "--values", "values-ci.yaml"]
    for value in sets:
        command += ["--set", value]
    output = subprocess.run(
        command, cwd=HELM_DIR, capture_output=True, text=True, check=True
    ).stdout
    return [doc for doc in yaml.safe_load_all(output) if doc]


def _named(docs: list[dict], kind: str, suffix: str) -> dict | None:
    return next(
        (d for d in docs if d["kind"] == kind and d["metadata"]["name"].endswith(suffix)), None
    )


def _api_env(docs: list[dict]) -> dict[str, str]:
    api = _named(docs, "Deployment", "-render-api")
    return {
        e["name"]: e.get("value") for e in api["spec"]["template"]["spec"]["containers"][0]["env"]
    }


def test_the_render_service_has_no_permission_on_deployments() -> None:
    role = _named(_render(*GATE), "Role", "-render-activation-role")

    assert role is not None
    resources = {r for rule in role["rules"] for r in rule["resources"]}
    assert resources == {"configmaps"}
    assert all(set(rule["verbs"]) == {"get", "update"} for rule in role["rules"])


def test_the_render_service_is_not_told_which_deployments_to_restart() -> None:
    env = _api_env(_render(*GATE))

    assert "RENDER_ACTIVATION_DEPLOYMENTS" not in env
    assert "RENDER_ACTIVATION_SELF_DEPLOYMENT" not in env
    assert env["RENDER_ACTIVATION_CONFIGMAP"].endswith("-render-activation-state")


def test_template_preview_may_get_and_patch_exactly_the_three_render_deployments() -> None:
    docs = _render(*WITH_TEMPLATE_PREVIEW)
    role = _named(docs, "Role", "-render-restart-role")

    (rule,) = role["rules"]
    assert (rule["apiGroups"], rule["resources"], sorted(rule["verbs"])) == (
        ["apps"],
        ["deployments"],
        ["get", "patch"],
    )
    assert [n.removeprefix("test-nv-config-manager-render-") for n in rule["resourceNames"]] == [
        "api",
        "consumer-nautobot",
        "consumer-device",
    ]


def test_that_role_is_bound_to_template_previews_backend_service_account() -> None:
    binding = _named(_render(*WITH_TEMPLATE_PREVIEW), "RoleBinding", "-render-restart-rolebinding")

    (subject,) = binding["subjects"]
    assert subject["kind"] == "ServiceAccount"
    assert subject["name"] == "template-preview-backend"
    assert binding["roleRef"]["name"].endswith("-render-restart-role")


def test_without_template_preview_nobody_is_granted_the_restart_permission() -> None:
    assert _named(_render(*GATE), "Role", "-render-restart-role") is None


def test_with_the_gate_closed_no_activation_rbac_exists_at_all() -> None:
    docs = _render()

    assert _named(docs, "Role", "-render-activation-role") is None
    assert _named(docs, "Role", "-render-restart-role") is None
