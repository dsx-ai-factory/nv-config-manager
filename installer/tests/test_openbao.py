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
"""Tests for OpenBao KV v2 provisioning."""

from __future__ import annotations

import json

import pytest
import responses

from nv_config_manager_installer.openbao import (
    OpenBaoClient,
    OpenBaoError,
    OpenBaoPopulator,
)
from nv_config_manager_installer.schema import (
    ClusterConfig,
    DCIMConfig,
    GitTokenEntry,
    NetworkSecretEntry,
    NVConfigManagerInstallConfig,
    PasswordSource,
    SecretsConfig,
    SecretsMethod,
    ServicesConfig,
    SiteConfig,
    SSOConfig,
    VaultConfig,
    VaultPathConfig,
)


class InMemoryOpenBaoClient:
    """Minimal in-memory client implementing the populator's client contract."""

    def __init__(self):
        self.mounts: set[str] = set()
        self.data: dict[tuple[str, str], dict[str, str]] = {}

    def ensure_kv2_mount(self, mount: str) -> bool:
        created = mount not in self.mounts
        self.mounts.add(mount)
        return created

    def read_secret(self, mount: str, path: str) -> tuple[dict[str, str], int]:
        data = self.data.get((mount, path), {})
        return dict(data), 1 if data else 0

    def write_missing(self, mount: str, path: str, desired: dict[str, str]) -> list[str]:
        existing = self.data.setdefault((mount, path), {})
        additions = {key: value for key, value in desired.items() if key not in existing and value}
        existing.update(additions)
        return sorted(additions)


class TestOpenBaoClient:
    @responses.activate
    def test_creates_missing_kv2_mount(self):
        responses.get("https://openbao.test/v1/sys/mounts/nv-config-manager", status=404)
        responses.post(
            "https://openbao.test/v1/sys/mounts/nv-config-manager",
            status=204,
        )
        client = OpenBaoClient("https://openbao.test", "secret-token")

        assert client.ensure_kv2_mount("nv-config-manager") is True
        assert responses.calls[0].request.headers["X-Vault-Token"] == "secret-token"
        payload = json.loads(responses.calls[1].request.body)
        assert payload == {"type": "kv", "options": {"version": "2"}}

    @responses.activate
    def test_creates_mount_when_openbao_reports_missing_mount_as_400(self):
        url = "https://openbao.test/v1/sys/mounts/config-secrets"
        responses.get(
            url,
            json={"errors": ["No secret engine mount at config-secrets/"]},
            status=400,
        )
        responses.post(url, status=204)
        client = OpenBaoClient("https://openbao.test", "secret-token")

        assert client.ensure_kv2_mount("config-secrets") is True

    @responses.activate
    def test_write_missing_preserves_existing_values_with_cas(self):
        url = "https://openbao.test/v1/nv-config-manager/data/prod/nautobot"
        responses.get(
            url,
            json={
                "data": {
                    "data": {"token": "existing-token"},
                    "metadata": {"version": 4},
                }
            },
        )
        responses.post(url, status=204)
        client = OpenBaoClient("https://openbao.test", "secret-token")

        added = client.write_missing(
            "nv-config-manager",
            "prod/nautobot",
            {"token": "replacement", "nats_password": "new-password"},
        )

        assert added == ["nats_password"]
        payload = json.loads(responses.calls[1].request.body)
        assert payload == {
            "data": {
                "token": "existing-token",
                "nats_password": "new-password",
            },
            "options": {"cas": 4},
        }

    @responses.activate
    def test_rejects_non_kv2_mount(self):
        responses.get(
            "https://openbao.test/v1/sys/mounts/secrets",
            json={"type": "kv", "options": {"version": "1"}},
        )
        client = OpenBaoClient("https://openbao.test", "secret-token")

        with pytest.raises(OpenBaoError, match="not KV v2"):
            client.ensure_kv2_mount("secrets")


class TestOpenBaoPopulator:
    def _config(self) -> NVConfigManagerInstallConfig:
        return NVConfigManagerInstallConfig(
            cluster=ClusterConfig(environment="prod"),
            secrets=SecretsConfig(
                method=SecretsMethod.ESO,
                vault=VaultConfig(
                    server="https://openbao.test",
                    secrets_path="nv-config-manager",
                    config_secrets_path="secrets",
                ),
            ),
            sites=[SiteConfig(name="dc01")],
            network_secrets=[NetworkSecretEntry(name="Root password", secret_key="root_password")],
        )

    def test_populates_mounts_application_paths_and_site_secrets(self):
        config = self._config()
        client = InMemoryOpenBaoClient()
        client.data[("nv-config-manager", "prod/nautobot")] = {"token": "preserved-token"}

        result = OpenBaoPopulator(config, client).populate()

        assert client.mounts == {"nv-config-manager", "secrets"}
        assert client.data[("nv-config-manager", "prod/nautobot")]["token"] == ("preserved-token")
        assert (
            client.data[("nv-config-manager", "prod/nautobot-app")]["superuser_api_token"]
            == "preserved-token"
        )
        assert "nats_password" in client.data[("nv-config-manager", "prod/nautobot")]
        assert "temporal_password" in client.data[("nv-config-manager", "prod/postgres")]
        site_data = client.data[("secrets", "prod/site/dc01/config_secrets")]
        assert "root_password_r1" in site_data
        assert "hash_salt" in site_data
        assert result.keys_added > 0

    def test_populates_required_site_secrets_without_network_secret_config(self):
        config = self._config()
        config.network_secrets = []
        client = InMemoryOpenBaoClient()

        OpenBaoPopulator(config, client).populate()

        site_data = client.data[("secrets", "prod/site/dc01/config_secrets")]
        assert site_data["root_password_r1"]
        assert site_data["api_user_key_r1"]

    def test_populates_external_dcim_token_without_nautobot_secrets(self):
        config = self._config()
        config.dcim = DCIMConfig(provider="synthetic", server="https://synthetic.example")
        config.services = ServicesConfig(nautobot=False)
        config.secrets.vault.paths.dcim = VaultPathConfig(enabled=True)
        client = InMemoryOpenBaoClient()

        OpenBaoPopulator(config, client).populate()

        assert "token" in client.data[("nv-config-manager", "prod/dcim")]
        assert ("nv-config-manager", "prod/nautobot") not in client.data

    def test_rejects_existing_mismatched_nautobot_tokens(self):
        config = self._config()
        client = InMemoryOpenBaoClient()
        client.data[("nv-config-manager", "prod/nautobot")] = {"token": "client-token"}
        client.data[("nv-config-manager", "prod/nautobot-app")] = {
            "superuser_api_token": "different-superuser-token"
        }

        with pytest.raises(OpenBaoError, match="token.*differ"):
            OpenBaoPopulator(config, client).populate()

    def test_requires_external_oidc_secret_when_not_already_present(self):
        config = self._config()
        config.sso = SSOConfig(enabled=True, client_secret="")
        client = InMemoryOpenBaoClient()

        with pytest.raises(OpenBaoError, match="client_secret"):
            OpenBaoPopulator(config, client).populate()

    def test_merges_default_vault_keys_with_partial_overrides(self):
        config = self._config()
        config.secrets.vault.paths.nautobot = VaultPathConfig(keys={"token": "api_token"})
        client = InMemoryOpenBaoClient()

        OpenBaoPopulator(config, client).populate()

        nautobot = client.data[("nv-config-manager", "prod/nautobot")]
        assert "api_token" in nautobot
        assert "read_only_token" in nautobot
        assert "readOnlyToken" not in nautobot

    def test_rejects_empty_existing_application_secret(self):
        config = self._config()
        config.sso = SSOConfig(enabled=True, client_secret="")
        client = InMemoryOpenBaoClient()
        client.data[("nv-config-manager", "prod/oidc")] = {"client_secret": ""}

        with pytest.raises(OpenBaoError, match="client_secret"):
            OpenBaoPopulator(config, client).populate()

    def test_rejects_empty_existing_git_token(self):
        config = self._config()
        config.git_tokens = [GitTokenEntry(name="repo", vault_path="prod/git/repo", token="")]
        client = InMemoryOpenBaoClient()
        client.data[("nv-config-manager", "prod/git/repo")] = {"token": ""}

        with pytest.raises(OpenBaoError, match="Git token 'repo'"):
            OpenBaoPopulator(config, client).populate()

    def test_rejects_empty_existing_vault_sourced_site_secret(self):
        config = self._config()
        config.network_secrets = [
            NetworkSecretEntry(
                name="RADIUS password",
                secret_key="radius_password",
                source=PasswordSource.VAULT,
            )
        ]
        client = InMemoryOpenBaoClient()
        client.data[("secrets", "prod/site/dc01/config_secrets")] = {"radius_password_r1": ""}

        with pytest.raises(OpenBaoError, match="radius_password_r1"):
            OpenBaoPopulator(config, client).populate()
