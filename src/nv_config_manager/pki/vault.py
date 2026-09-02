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
"""Vault/OpenBao PKI client using workload JWT authentication."""

from __future__ import annotations

import asyncio
import json
import re
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Formatter
from typing import Any

import aiohttp

from nv_config_manager.pki.base import (
    CertificateIssueRequest,
    IssuedCertificate,
    PKIAuthenticationError,
    PKIAuthorizationError,
    PKIClient,
    PKIConfigurationError,
    PKIProviderError,
    PKISourceNotFoundError,
)

_PEM_CERTIFICATE_PATTERN = re.compile(
    r"-----BEGIN CERTIFICATE-----\s+.*?\s+-----END CERTIFICATE-----",
    re.DOTALL,
)
_ALLOWED_TEMPLATE_FIELDS = frozenset({"device_id", "device_name"})


@dataclass(frozen=True, slots=True)
class VaultPKISource:
    """Vault paths and issuance policy behind one logical certificate source."""

    issue_role: str | None = None
    ca_path: str | None = None
    ttl: str = "168h"
    common_name_template: str = "device-{device_id}.switches.dev.dsx.nvidia.com"

    def __post_init__(self) -> None:
        fields = {
            field_name
            for _, field_name, _, _ in Formatter().parse(self.common_name_template)
            if field_name is not None
        }
        unsupported = fields - _ALLOWED_TEMPLATE_FIELDS
        if unsupported:
            raise PKIConfigurationError(
                "common_name_template contains unsupported fields: "
                + ", ".join(sorted(unsupported))
            )


class VaultPKIClient(PKIClient):
    """Issue certificates from Vault-compatible PKI engines via JWT login."""

    def __init__(
        self,
        *,
        address: str,
        auth_mount: str,
        auth_role: str,
        token_path: str,
        pki_mount: str,
        sources: dict[str, VaultPKISource],
        namespace: str | None = None,
        verify: bool | str = True,
        timeout_seconds: float = 30.0,
    ) -> None:
        """Create a Vault PKI client without performing network I/O."""
        self._address = address.rstrip("/")
        self._auth_mount = auth_mount.strip("/")
        self._auth_role = auth_role
        self._token_path = Path(token_path)
        self._pki_mount = pki_mount.strip("/")
        self._sources = dict(sources)
        self._namespace = namespace or None
        self._verify = verify
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=10)
        self._session: aiohttp.ClientSession | None = None
        self._vault_token: str | None = None
        self._vault_token_valid_until = 0.0
        self._login_lock = asyncio.Lock()

    async def close(self) -> None:
        """Close the shared HTTP session and discard the cached Vault token."""
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._invalidate_token()

    def _connector(self) -> aiohttp.TCPConnector:
        if self._verify is False:
            return aiohttp.TCPConnector(ssl=False)
        if isinstance(self._verify, str):
            return aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=self._verify))
        return aiohttp.TCPConnector(ssl=ssl.create_default_context())

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=self._connector(),
                timeout=self._timeout,
                headers={"Accept": "application/json"},
            )
        return self._session

    def _base_headers(self) -> dict[str, str]:
        if self._namespace:
            return {"X-Vault-Namespace": self._namespace}
        return {}

    def _invalidate_token(self) -> None:
        self._vault_token = None
        self._vault_token_valid_until = 0.0

    async def _login(self) -> str:
        if self._vault_token and time.monotonic() < self._vault_token_valid_until:
            return self._vault_token

        async with self._login_lock:
            if self._vault_token and time.monotonic() < self._vault_token_valid_until:
                return self._vault_token
            try:
                workload_jwt = self._token_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise PKIAuthenticationError("Unable to read the projected workload JWT") from exc
            if not workload_jwt:
                raise PKIAuthenticationError("The projected workload JWT is empty")

            session = await self._ensure_session()
            url = f"{self._address}/v1/auth/{self._auth_mount}/login"
            try:
                async with session.post(
                    url,
                    headers=self._base_headers(),
                    json={"role": self._auth_role, "jwt": workload_jwt},
                ) as response:
                    payload = await self._json_response(response, "Vault JWT login")
                    if response.status >= 400:
                        raise PKIAuthenticationError(
                            f"Vault JWT login failed with status {response.status}"
                        )
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise PKIProviderError("Vault JWT login request failed") from exc

            try:
                token = str(payload["auth"]["client_token"])
                lease_duration = int(payload["auth"].get("lease_duration", 0))
            except (KeyError, TypeError, ValueError) as exc:
                raise PKIProviderError("Vault JWT login returned an invalid response") from exc
            if not token:
                raise PKIProviderError("Vault JWT login returned an empty token")

            self._vault_token = token
            refresh_after = max(1, lease_duration - min(30, lease_duration // 10))
            self._vault_token_valid_until = time.monotonic() + refresh_after
            return token

    async def _authorized_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, str] | None = None,
    ) -> tuple[bytes, str]:
        session = await self._ensure_session()
        url = f"{self._address}/v1/{path.lstrip('/')}"
        for attempt in range(2):
            token = await self._login()
            headers = self._base_headers()
            headers["X-Vault-Token"] = token
            try:
                async with session.request(method, url, headers=headers, json=payload) as response:
                    content = await response.read()
                    content_type = response.headers.get("Content-Type", "")
                    if response.status in {401, 403} and attempt == 0:
                        self._invalidate_token()
                        continue
                    if response.status == 403:
                        raise PKIAuthorizationError("Vault denied the PKI operation")
                    if response.status == 401:
                        raise PKIAuthenticationError("Vault rejected the workload token")
                    if response.status >= 400:
                        raise PKIProviderError(
                            f"Vault PKI request failed with status {response.status}"
                        )
                    return content, content_type
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise PKIProviderError("Vault PKI request failed") from exc
        raise PKIAuthenticationError("Vault authentication retry failed")

    @staticmethod
    async def _json_response(response: aiohttp.ClientResponse, operation: str) -> dict[str, Any]:
        try:
            payload = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, json.JSONDecodeError) as exc:
            raise PKIProviderError(f"{operation} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise PKIProviderError(f"{operation} returned an invalid response")
        return payload

    @staticmethod
    def _decode_json(content: bytes, operation: str) -> dict[str, Any]:
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PKIProviderError(f"{operation} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise PKIProviderError(f"{operation} returned an invalid response")
        return payload

    def _source(self, name: str) -> VaultPKISource:
        try:
            return self._sources[name]
        except KeyError as exc:
            raise PKISourceNotFoundError(f"Unknown certificate source: {name}") from exc

    async def issue_certificate(self, request: CertificateIssueRequest) -> IssuedCertificate:
        """Issue a device identity through one configured Vault PKI role."""
        source = self._source(request.source)
        if not source.issue_role:
            raise PKISourceNotFoundError(
                f"Certificate source {request.source} does not support identity issuance"
            )
        common_name = source.common_name_template.format(
            device_id=request.device_id,
            device_name=request.device_name,
        )
        content, _ = await self._authorized_request(
            "POST",
            f"{self._pki_mount}/issue/{source.issue_role}",
            payload={"common_name": common_name, "ttl": source.ttl},
        )
        payload = self._decode_json(content, "Vault PKI issuance")
        try:
            data = payload["data"]
            certificate = str(data["certificate"])
            private_key = str(data["private_key"])
            ca_chain = tuple(str(item) for item in data["ca_chain"])
            serial_number = str(data["serial_number"])
            expires_at = datetime.fromtimestamp(int(data["expiration"]), tz=UTC)
        except (KeyError, TypeError, ValueError) as exc:
            raise PKIProviderError("Vault PKI issuance returned an invalid response") from exc
        if not certificate or not private_key or not ca_chain:
            raise PKIProviderError("Vault PKI issuance returned incomplete certificate material")
        return IssuedCertificate(
            certificate_pem=certificate,
            private_key_pem=private_key,
            ca_chain_pem=ca_chain,
            serial_number=serial_number,
            expires_at=expires_at,
        )

    async def get_ca_chain(self, source: str) -> tuple[str, ...]:
        """Read a PEM CA bundle from a configured Vault PKI path."""
        source_config = self._source(source)
        if not source_config.ca_path:
            raise PKISourceNotFoundError(f"Certificate source {source} does not provide a CA chain")
        content, content_type = await self._authorized_request("GET", source_config.ca_path)
        if "json" in content_type.lower():
            payload = self._decode_json(content, "Vault CA chain read")
            data = payload.get("data", {})
            values = data.get("ca_chain") or [data.get("certificate")]
            certificates = tuple(str(value) for value in values if value)
        else:
            try:
                pem_bundle = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PKIProviderError("Vault CA chain returned invalid PEM data") from exc
            certificates = tuple(_PEM_CERTIFICATE_PATTERN.findall(pem_bundle))
        if not certificates:
            raise PKIProviderError("Vault CA chain response contained no certificates")
        return certificates
