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
"""Keep an optional Vault inventory token independent from the regular DCIM token."""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

CHART = Path(__file__).resolve().parents[3] / "deploy/helm"


def _render(*settings: str) -> list[dict[str, Any]]:
    if shutil.which("helm") is None or not (CHART / "charts").is_dir():
        pytest.skip("Helm and vendored chart dependencies are required")
    command = ["helm", "template", "test", str(CHART), "--values", str(CHART / "values-ci.yaml")]
    for setting in settings:
        command += ["--set", setting]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _migration_read_only_ref(documents: list[dict[str, Any]]) -> dict[str, Any]:
    deployment = next(
        doc
        for doc in documents
        if doc["kind"] == "Deployment"
        and doc["metadata"]["name"] == "test-nv-config-manager-nautobot"
    )
    migration = next(
        container
        for container in deployment["spec"]["template"]["spec"]["initContainers"]
        if container["name"] == "run-migrations"
    )
    env = next(
        entry for entry in migration["env"] if entry["name"] == "NAUTOBOT_READ_ONLY_API_TOKEN"
    )
    return env["valueFrom"]["secretKeyRef"]


@pytest.mark.parametrize("method", ["eso", "vault-agent"])
@pytest.mark.parametrize("mcp_enabled", [True, False])
def test_optional_vault_lookup_is_isolated_from_regular_dcim_token(
    method: str, mcp_enabled: bool
) -> None:
    documents = _render(
        f"secrets.method={method}",
        "secrets.vaultAgent.esoForOperators=true",
        f"mcp.enabled={str(mcp_enabled).lower()}",
        "dcim.tokenSecret.name=custom-dcim-token",
        "secrets.vault.paths.nautobot.keys.token=rw_token",
        "secrets.vault.paths.nautobot.keys.readOnlyToken=token_ro",
    )
    secrets = {
        doc["spec"]["target"]["name"]: doc for doc in documents if doc["kind"] == "ExternalSecret"
    }
    reader = secrets["custom-dcim-token-read-only"]
    assert reader["spec"]["data"] == [
        {
            "secretKey": "read-only-token",
            "remoteRef": {"key": "nv-config-manager/ci/nautobot", "property": "token_ro"},
        }
    ]
    assert _migration_read_only_ref(documents) == {
        "name": "custom-dcim-token-read-only",
        "key": "read-only-token",
        "optional": True,
    }
    if method == "eso":
        regular = secrets["custom-dcim-token"]
        # Only the regular property is required for this resource to reconcile;
        # a missing optional Vault key affects the separate resource alone.
        assert regular["spec"]["data"] == [
            {
                "secretKey": "token",
                "remoteRef": {"key": "nv-config-manager/ci/nautobot", "property": "rw_token"},
            }
        ]
        assert regular["metadata"]["name"] != reader["metadata"]["name"]
    else:
        # Vault Agent already reads the regular token directly; do not add an
        # unnecessary DCIM-token ExternalSecret for that authentication path.
        assert "custom-dcim-token" not in secrets


def test_kubernetes_method_retains_installer_secret_and_optional_read_only_key() -> None:
    documents = _render("secrets.method=kubernetes", "dcim.tokenSecret.name=custom-dcim-token")
    assert _migration_read_only_ref(documents) == {
        "name": "custom-dcim-token",
        "key": "read-only-token",
        "optional": True,
    }
    assert not any(doc["kind"] == "ExternalSecret" for doc in documents)
    assembler = next(
        doc
        for doc in documents
        if doc["kind"] == "Job"
        and doc["metadata"]["name"] == "test-nv-config-manager-secret-assembler"
    )
    assert "/secrets/dcim-token/read-only-token" in str(assembler)


def test_external_nautobot_does_not_gain_a_local_reader_secret() -> None:
    documents = _render(
        "externalServices.nautobot.local=false",
        "externalServices.nautobot.server=https://nautobot.example",
    )
    secrets = [doc for doc in documents if doc["kind"] == "ExternalSecret"]
    assert any(doc["spec"]["target"]["name"] == "nautobot-token" for doc in secrets)
    assert not any(doc["spec"]["target"]["name"] == "nautobot-token-read-only" for doc in secrets)


def test_long_custom_secret_name_keeps_the_reader_target_distinct() -> None:
    regular_name = "n" * 63
    documents = _render(f"dcim.tokenSecret.name={regular_name}")
    ref = _migration_read_only_ref(documents)
    assert len(ref["name"]) <= 63
    assert ref["name"].endswith("-read-only")
    assert ref["name"] != regular_name
    assert any(
        doc["kind"] == "ExternalSecret" and doc["spec"]["target"]["name"] == ref["name"]
        for doc in documents
    )


def test_vault_agent_without_operator_eso_accepts_a_manually_supplied_reader_secret() -> None:
    documents = _render(
        "secrets.method=vault-agent",
        "secrets.vaultAgent.esoForOperators=false",
    )
    assert _migration_read_only_ref(documents) == {
        "name": "nautobot-token-read-only",
        "key": "read-only-token",
        "optional": True,
    }
    assert not any(doc["kind"] == "ExternalSecret" for doc in documents)
