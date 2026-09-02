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
"""Vault-compatible PKI client tests."""

from __future__ import annotations

from configparser import ConfigParser
from datetime import UTC, datetime

import pytest
from aioresponses import aioresponses

from nv_config_manager.pki import (
    CertificateIssueRequest,
    PKIConfigurationError,
    PKISourceNotFoundError,
    VaultPKIClient,
    VaultPKISource,
    create_pki_client,
)

_CERTIFICATE = "-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----"
_CA = "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----"
_PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----"


def _client(tmp_path) -> VaultPKIClient:
    token_path = tmp_path / "token"
    token_path.write_text("workload-jwt", encoding="utf-8")
    return VaultPKIClient(
        address="https://vault.example",
        namespace="dgxc-dsx",
        auth_mount="jwt/k8s/test-cluster",
        auth_role="nv-config-manager-switch-certificate-issuer",
        token_path=str(token_path),
        pki_mount="pki/dev-dsx-nvidia-com",
        sources={
            "telemetry-client": VaultPKISource(
                issue_role="switch-client",
                ttl="168h",
                common_name_template="device-{device_id}.switches.example.com",
            ),
            "telemetry-ca": VaultPKISource(
                ca_path="pki/dev-dsx-nvidia-com/ca/pem",
            ),
        },
    )


@pytest.mark.asyncio
async def test_issue_certificate_uses_jwt_login_and_source_role(tmp_path) -> None:
    client = _client(tmp_path)
    expiration = 1_800_000_000
    with aioresponses() as mocked:
        mocked.post(
            "https://vault.example/v1/auth/jwt/k8s/test-cluster/login",
            payload={"auth": {"client_token": "vault-token", "lease_duration": 3600}},
        )
        mocked.post(
            "https://vault.example/v1/pki/dev-dsx-nvidia-com/issue/switch-client",
            payload={
                "data": {
                    "certificate": _CERTIFICATE,
                    "private_key": _PRIVATE_KEY,
                    "ca_chain": [_CA],
                    "serial_number": "01:02",
                    "expiration": expiration,
                }
            },
        )
        issued = await client.issue_certificate(
            CertificateIssueRequest(
                source="telemetry-client",
                device_id="1234",
                device_name="leaf-1",
            )
        )

    await client.close()
    assert issued.certificate_pem == _CERTIFICATE
    assert issued.private_key_pem == _PRIVATE_KEY
    assert issued.ca_chain_pem == (_CA,)
    assert issued.serial_number == "01:02"
    assert issued.expires_at == datetime.fromtimestamp(expiration, tz=UTC)

    login_request = next(
        request
        for key, requests in mocked.requests.items()
        if "/login" in str(key[1])
        for request in requests
    )
    assert login_request.kwargs["headers"]["X-Vault-Namespace"] == "dgxc-dsx"
    assert login_request.kwargs["json"] == {
        "role": "nv-config-manager-switch-certificate-issuer",
        "jwt": "workload-jwt",
    }

    issue_request = next(
        request
        for key, requests in mocked.requests.items()
        if "/issue/" in str(key[1])
        for request in requests
    )
    assert issue_request.kwargs["json"] == {
        "common_name": "device-1234.switches.example.com",
        "ttl": "168h",
    }


@pytest.mark.asyncio
async def test_get_ca_chain_reuses_login_token(tmp_path) -> None:
    client = _client(tmp_path)
    with aioresponses() as mocked:
        mocked.post(
            "https://vault.example/v1/auth/jwt/k8s/test-cluster/login",
            payload={"auth": {"client_token": "vault-token", "lease_duration": 3600}},
        )
        mocked.get(
            "https://vault.example/v1/pki/dev-dsx-nvidia-com/ca/pem",
            body=f"{_CA}\n{_CERTIFICATE}\n",
            content_type="application/x-pem-file",
        )
        first = await client.get_ca_chain("telemetry-ca")
        mocked.get(
            "https://vault.example/v1/pki/dev-dsx-nvidia-com/ca/pem",
            body=f"{_CA}\n",
            content_type="application/x-pem-file",
        )
        second = await client.get_ca_chain("telemetry-ca")

    await client.close()
    assert first == (_CA, _CERTIFICATE)
    assert second == (_CA,)
    login_calls = sum(
        len(requests) for key, requests in mocked.requests.items() if "/login" in str(key[1])
    )
    assert login_calls == 1


@pytest.mark.asyncio
async def test_source_capability_is_enforced_before_request(tmp_path) -> None:
    client = _client(tmp_path)
    with pytest.raises(PKISourceNotFoundError, match="does not support identity issuance"):
        await client.issue_certificate(
            CertificateIssueRequest(
                source="telemetry-ca",
                device_id="1234",
                device_name="leaf-1",
            )
        )
    await client.close()


def test_common_name_template_rejects_attribute_access() -> None:
    with pytest.raises(PKIConfigurationError, match="unsupported fields"):
        VaultPKISource(
            issue_role="role",
            common_name_template="{device_id.__class__}.switches.example.com",
        )


def test_factory_builds_registered_vault_provider(tmp_path) -> None:
    token_path = tmp_path / "token"
    config = ConfigParser(interpolation=None)
    config.read_string(
        f"""
[pki]
provider = vault

[pki.vault]
address = http://openbao.openbao.svc.cluster.local:8200
auth_mount = jwt/k8s/nv-config-manager-local
auth_role = issuer
token_path = {token_path}
pki_mount = pki/dev-dsx-nvidia-com
verify = false

[pki.source.telemetry-client]
issue_role = switch-client
ttl = 168h
common_name_template = device-{{device_id}}.switches.example.com
"""
    )

    client = create_pki_client(config)

    assert isinstance(client, VaultPKIClient)


def test_factory_rejects_caller_controlled_path_characters() -> None:
    config = ConfigParser(interpolation=None)
    config.read_string(
        """
[pki]
provider = vault
[pki.vault]
address = https://vault.example
auth_mount = ../unsafe
auth_role = issuer
token_path = /token
pki_mount = pki/example
[pki.source.client]
issue_role = role
"""
    )

    with pytest.raises(PKIConfigurationError, match="unsupported characters"):
        create_pki_client(config)
