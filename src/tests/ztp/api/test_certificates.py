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
"""ZTP certificate-serving endpoint tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from httpx import ASGITransport, AsyncClient

from nv_config_manager.dcim.models import CertificateKind, DeviceCertificate
from nv_config_manager.pki import CertificateIssueRequest, IssuedCertificate, PKIClient
from nv_config_manager.ztp.api.main import app
from nv_config_manager.ztp.device import DeviceData


def _certificate_material() -> IssuedCertificate:
    now = datetime.now(tz=UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "switch-1")]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=7))
        .sign(ca_key, hashes.SHA256())
    )
    return IssuedCertificate(
        certificate_pem=leaf_certificate.public_bytes(serialization.Encoding.PEM).decode(),
        private_key_pem=leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        ca_chain_pem=(ca_certificate.public_bytes(serialization.Encoding.PEM).decode(),),
        serial_number=format(leaf_certificate.serial_number, "x"),
        expires_at=now + timedelta(days=7),
    )


class FakePKIClient(PKIClient):
    def __init__(self, issued: IssuedCertificate) -> None:
        self.issued = issued
        self.issue_requests: list[CertificateIssueRequest] = []
        self.ca_sources: list[str] = []
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def issue_certificate(self, request: CertificateIssueRequest) -> IssuedCertificate:
        self.issue_requests.append(request)
        return self.issued

    async def get_ca_chain(self, source: str) -> tuple[str, ...]:
        self.ca_sources.append(source)
        return self.issued.ca_chain_pem


def _device() -> DeviceData:
    return DeviceData(
        id="device-1",
        name="leaf-1",
        addresses=["testclient"],
        platform_name="Cumulus Linux",
        version="5.16.1",
        config_store_instance=None,
        certificates=(
            DeviceCertificate(id="otel-ca", source="telemetry-ca", kind=CertificateKind.CA),
            DeviceCertificate(
                id="otel-client",
                source="telemetry-client",
                kind=CertificateKind.IDENTITY,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_direct_listener_rejects_spoofed_identity_header(monkeypatch) -> None:
    """Direct clients cannot use gateway identity headers to bypass device IP auth."""
    monkeypatch.setenv("ACCEPT_REQUEST_HEADERS", "false")
    with patch(
        "nv_config_manager.ztp.api.device_v1._get_device_data",
        new=AsyncMock(return_value=_device()),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("198.51.100.10", 12345)),
            base_url="https://testserver",
            headers={"X-Auth-Request-Email": "attacker@example.com"},
        ) as client:
            response = await client.get("/v1/device/device-1/certificates/otel-client")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_direct_listener_enforces_ip_auth_when_global_auth_is_disabled(monkeypatch) -> None:
    """Disabling user auth does not make direct device endpoints public."""
    monkeypatch.setenv("ACCEPT_REQUEST_HEADERS", "false")
    with (
        patch("nv_config_manager.ztp.api.device_v1.auth_required", return_value=False),
        patch(
            "nv_config_manager.ztp.api.device_v1._get_device_data",
            new=AsyncMock(return_value=_device()),
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("198.51.100.10", 12345)),
            base_url="https://testserver",
        ) as client:
            response = await client.get("/v1/device/device-1/certificates/otel-client")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_serves_assigned_ca_with_no_store_headers() -> None:
    fake = FakePKIClient(_certificate_material())
    with (
        patch("nv_config_manager.ztp.api.device_v1._authorize_request", new=AsyncMock()),
        patch(
            "nv_config_manager.ztp.api.device_v1._get_device_data",
            new=AsyncMock(return_value=_device()),
        ),
        patch("nv_config_manager.ztp.api.device_v1.create_pki_client", return_value=fake),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/v1/device/device-1/certificates/otel-ca")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-pem-file")
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content.startswith(b"-----BEGIN CERTIFICATE-----")
    assert fake.ca_sources == ["telemetry-ca"]
    assert fake.closed


@pytest.mark.asyncio
async def test_issues_assigned_identity_as_unencrypted_pkcs12() -> None:
    issued = _certificate_material()
    fake = FakePKIClient(issued)
    with (
        patch("nv_config_manager.ztp.api.device_v1._authorize_request", new=AsyncMock()),
        patch(
            "nv_config_manager.ztp.api.device_v1._get_device_data",
            new=AsyncMock(return_value=_device()),
        ),
        patch("nv_config_manager.ztp.api.device_v1.create_pki_client", return_value=fake),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            response = await client.get("/v1/device/device-1/certificates/otel-client")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-pkcs12")
    key, certificate, additional = pkcs12.load_key_and_certificates(response.content, None)
    assert key is not None
    assert certificate is not None
    assert len(additional) == 1
    assert fake.issue_requests == [
        CertificateIssueRequest(
            source="telemetry-client",
            device_id="device-1",
            device_name="leaf-1",
        )
    ]
    assert fake.closed


@pytest.mark.asyncio
async def test_rejects_identity_certificate_over_http() -> None:
    fake = FakePKIClient(_certificate_material())
    with (
        patch("nv_config_manager.ztp.api.device_v1._authorize_request", new=AsyncMock()),
        patch(
            "nv_config_manager.ztp.api.device_v1._get_device_data",
            new=AsyncMock(return_value=_device()),
        ),
        patch("nv_config_manager.ztp.api.device_v1.create_pki_client", return_value=fake),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/v1/device/device-1/certificates/otel-client")

    assert response.status_code == 426
    assert response.headers["upgrade"] == "TLS/1.2"
    assert fake.issue_requests == []
    assert not fake.closed


@pytest.mark.asyncio
async def test_rejects_certificate_not_assigned_to_device() -> None:
    fake = FakePKIClient(_certificate_material())
    with (
        patch("nv_config_manager.ztp.api.device_v1._authorize_request", new=AsyncMock()),
        patch(
            "nv_config_manager.ztp.api.device_v1._get_device_data",
            new=AsyncMock(return_value=_device()),
        ),
        patch("nv_config_manager.ztp.api.device_v1.create_pki_client", return_value=fake),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/v1/device/device-1/certificates/not-assigned")

    assert response.status_code == 404
    assert fake.issue_requests == []
    assert fake.ca_sources == []
    assert not fake.closed
