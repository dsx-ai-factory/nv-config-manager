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
import asyncio
import json
import os
import time
from configparser import ConfigParser
from copy import deepcopy
from unittest.mock import AsyncMock, call, patch

import jwt as pyjwt
import pytest
from aiohttp import ClientConnectionError, ClientResponseError, RequestInfo
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from multidict import CIMultiDict
from yarl import URL

from nv_config_manager.common.auth import AuthConfig, JwtProviderConfig
from nv_config_manager.dhcp.api import (
    _fetch_summary_sources,
    _install_cors,
    app,
)
from nv_config_manager.dhcp.kea import KeaClient

_HEADERS_TRUSTED = AuthConfig(accept_request_headers=True)
_AUTH_DISABLED = AuthConfig(required=False)


def make_client_response_error(message: str) -> ClientResponseError:
    """Create a ClientResponseError for testing."""
    request_info = RequestInfo(
        url=URL("http://test"),
        method="POST",
        headers=CIMultiDict(),
        real_url=URL("http://test"),
    )
    return ClientResponseError(
        request_info=request_info,
        history=(),
        message=message,
    )


# Get the directory containing this test file
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

MIN_READY_CONFIG = [
    {
        "arguments": {
            "Dhcp4": {
                "lease-database": {
                    "host": "dhcp-db.example.local",
                    "name": "kea_dhcp",
                    "type": "postgresql",
                }
            }
        },
        "result": 0,
    }
]

MIN_UNSYNCED_CONFIG = [
    {
        "arguments": {
            "Dhcp4": {
                "lease-database": {
                    "type": "memfile",
                }
            }
        },
        "result": 0,
    }
]

LEASE_GET_RESPONSE = [
    {
        "arguments": {
            "cltt": int(time.time()) - 60,
            "hostname": "",
            "hw-address": "02:05:91:48:df:cf",
            "ip-address": "10.0.0.10",
            "state": 0,
            "subnet-id": 7,
            "valid-lft": 7200,
        },
        "result": 0,
        "text": "IPv4 lease found.",
    }
]


def lease_page(*leases: dict[str, object]) -> list[dict[str, object]]:
    """Wrap raw leases in a successful KEA page response."""
    return [
        {
            "arguments": {"count": len(leases), "leases": list(leases)},
            "result": 0,
        }
    ]


def active_lease(ip: str, hostname: str, subnet_id: int = 7) -> dict[str, object]:
    """Return one active lease row for API pagination tests."""
    return {
        "cltt": int(time.time()) - 60,
        "hostname": hostname,
        "hw-address": f"02:00:00:00:00:{int(ip.rsplit('.', maxsplit=1)[1]):02x}",
        "ip-address": ip,
        "state": 0,
        "subnet-id": subnet_id,
        "valid-lft": 3600,
    }


LEASE_DASHBOARD_CONFIG = [
    {
        "result": 0,
        "arguments": {
            "Dhcp4": {
                "reservations": [
                    {
                        "hostname": "reserved-switch",
                        "hw-address": "02:00:00:00:00:01",
                        "ip-address": "10.0.0.2",
                    }
                ],
                "subnet4": [
                    {
                        "id": 7,
                        "subnet": "10.0.0.0/24",
                        "pools": [{"pool": "10.0.0.10-10.0.0.19"}],
                    }
                ],
            }
        },
    }
]

LEASE_DASHBOARD_STATISTICS = [
    {
        "result": 0,
        "arguments": {
            "assigned-addresses": [[1, "2026-07-10 00:00:00"]],
        },
    }
]


def make_jwt_token(claims: dict) -> tuple[str, rsa.RSAPublicKey]:
    """Create a signed JWT and return it with the public key for JWKS mocking."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = pyjwt.encode(
        claims,
        key,
        algorithm="RS256",
        headers={"kid": "dhcp-test"},
    )
    return token, key.public_key()


def make_auth_config_with_jwt_provider() -> AuthConfig:
    """Return auth config with a non-OIDC JWT provider."""
    return AuthConfig(
        jwt_providers=(
            JwtProviderConfig(
                name="service",
                issuer="https://service-idp.example.com",
                audiences=["s:nv-config-manager"],
                jwks_uri="https://service-idp.example.com/jwks",
                claim_email="sub",
                claim_user="sub",
                claim_groups="scopes",
            ),
        )
    )


def test_cors_allows_configured_ui_origin() -> None:
    """Verify browser preflight and GET responses allow the configured UI origin."""
    config = ConfigParser()
    config.read_dict({"dhcp": {"cors_origins": "https://nvcm.example.com"}})
    cors_app = FastAPI()

    @cors_app.get("/resource")
    async def resource() -> dict[str, list[object]]:
        return {"items": []}

    with patch("nv_config_manager.dhcp.api.load_config", return_value=config):
        _install_cors(cors_app)

    client = TestClient(cors_app)
    origin = "https://nvcm.example.com"
    preflight = client.options(
        "/resource",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    response = client.get("/resource", headers={"Origin": origin})

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert preflight.headers["access-control-allow-credentials"] == "true"
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_rejects_wildcard_origin() -> None:
    """Verify credentialed CORS cannot be enabled for arbitrary origins."""
    config = ConfigParser()
    config.read_dict({"dhcp": {"cors_origins": " https://nvcm.example.com, * "}})

    with (
        patch("nv_config_manager.dhcp.api.load_config", return_value=config),
        pytest.raises(
            ValueError,
            match="must contain explicit origins when credentials are enabled",
        ),
    ):
        _install_cors(FastAPI())


def test_healthcheck_success():
    """Verify healthcheck success case."""
    client = TestClient(app)

    with patch(
        "nv_config_manager.dhcp.api.KeaClient.status", new_callable=AsyncMock
    ) as mock_status:
        mock_status.return_value = [
            {"arguments": {"pid": 9, "reload": 63173, "uptime": 63173}, "result": 0}
        ]
        with patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config", new_callable=AsyncMock
        ) as mock_get_config:
            mock_get_config.return_value = MIN_READY_CONFIG
            with patch(
                "nv_config_manager.dhcp.api.RedisClient.load_kea_config", new_callable=AsyncMock
            ) as mock_load_kea:
                mock_load_kea.return_value = {"some": "data"}
                rsp = client.get("/healthcheck")
                assert rsp.status_code == 200
                assert rsp.json() == "OK"


def test_healthcheck_unsynced():
    """Verify healthcheck with unsynced config."""
    client = TestClient(app)

    with patch(
        "nv_config_manager.dhcp.api.KeaClient.status", new_callable=AsyncMock
    ) as mock_status:
        mock_status.return_value = [
            {"arguments": {"pid": 9, "reload": 63173, "uptime": 63173}, "result": 0}
        ]
        with patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config", new_callable=AsyncMock
        ) as mock_get_config:
            mock_get_config.return_value = MIN_UNSYNCED_CONFIG
            with patch(
                "nv_config_manager.dhcp.api.RedisClient.load_kea_config", new_callable=AsyncMock
            ) as mock_load_kea:
                mock_load_kea.return_value = {"some": "data"}
                rsp = client.get("/healthcheck")
                assert rsp.status_code == 500
                assert rsp.json() == {"detail": "Lease database not present in Dhcp4 config"}


def test_healthcheck_status_error():
    """Verify healthcheck with status error."""
    client = TestClient(app)

    with patch(
        "nv_config_manager.dhcp.api.KeaClient.status", new_callable=AsyncMock
    ) as mock_status:
        mock_status.return_value = [
            {"arguments": {"pid": 9, "reload": 63173, "uptime": 63173}, "result": 1}
        ]
        with patch(
            "nv_config_manager.dhcp.api.RedisClient.load_kea_config", new_callable=AsyncMock
        ) as mock_load_kea:
            mock_load_kea.return_value = {"some": "data"}
            rsp = client.get("/healthcheck")
            assert rsp.status_code == 500
            assert rsp.json() == {
                "detail": [{"arguments": {"pid": 9, "reload": 63173, "uptime": 63173}, "result": 1}]
            }


def test_healthcheck_http_error():
    """Verify healthcheck with HTTP error."""
    client = TestClient(app)

    with patch(
        "nv_config_manager.dhcp.api.KeaClient.status", new_callable=AsyncMock
    ) as mock_status:
        mock_status.side_effect = make_client_response_error("HTTP ERROR")
        with patch(
            "nv_config_manager.dhcp.api.RedisClient.load_kea_config", new_callable=AsyncMock
        ) as mock_load_kea:
            mock_load_kea.return_value = {"some": "data"}
            rsp = client.get("/healthcheck")
            assert rsp.status_code == 500
            assert rsp.json() == {"detail": "HTTP ERROR"}


def test_healthcheck_not_ready_when_redis_has_no_config():
    """Strict readiness: an unconfigured pod must NOT be ready even if Redis is empty.

    This is the core of "preserve strict DHCP traffic gating": a freshly started
    Kea on the bootstrap config (memfile lease-db) with no desired config in
    Redis previously returned "OK" so the refresh process could reach it. That
    escape hatch is removed -- the bootstrap path now reaches Kea via the
    internal validation Service instead, so readiness stays strict.
    """
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.status",
            new_callable=AsyncMock,
            return_value=[{"arguments": {"pid": 9}, "result": 0}],
        ),
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=MIN_UNSYNCED_CONFIG,
        ),
        patch(
            "nv_config_manager.dhcp.api.RedisClient.load_kea_config",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        rsp = client.get("/healthcheck")

    assert rsp.status_code == 500
    assert rsp.json() == {"detail": "Lease database not present in Dhcp4 config"}


def test_healthcheck_ready_when_kea_online_and_config_applied():
    """Readiness reflects Kea-online AND desired config applied (postgresql lease-db)."""
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.status",
            new_callable=AsyncMock,
            return_value=[{"arguments": {"pid": 9}, "result": 0}],
        ),
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=MIN_READY_CONFIG,
        ),
    ):
        rsp = client.get("/healthcheck")

    assert rsp.status_code == 200
    assert rsp.json() == "OK"


def test_healthcheck_reports_config_get_failure():
    """A KEA config-get error surfaces as an unready (500) response."""
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.status",
            new_callable=AsyncMock,
            return_value=[{"arguments": {"pid": 9}, "result": 0}],
        ),
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=[{"result": 1, "text": "configuration unavailable"}],
        ),
    ):
        rsp = client.get("/healthcheck")

    assert rsp.status_code == 500
    assert rsp.json() == {"detail": "Failed to get KEA config: configuration unavailable"}


def test_livez_ready_when_kea_online_without_applied_config():
    """Liveness only checks Kea is alive; an unapplied config must NOT fail it.

    A config mismatch must never restart a live Kea, so /livez returns OK for a
    bootstrap-only Kea (memfile lease-db) and never inspects the running config.
    """
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.status",
            new_callable=AsyncMock,
            return_value=[{"arguments": {"pid": 9}, "result": 0}],
        ),
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
        ) as mock_get_config,
    ):
        rsp = client.get("/livez")

    assert rsp.status_code == 200
    assert rsp.json() == "OK"
    # Liveness must not depend on the applied configuration.
    mock_get_config.assert_not_awaited()


def test_livez_fails_when_kea_process_offline():
    """Liveness fails when a Kea process reports unhealthy so kubelet recycles Kea."""
    client = TestClient(app)

    with patch(
        "nv_config_manager.dhcp.api.KeaClient.status",
        new_callable=AsyncMock,
        return_value=[{"arguments": {"pid": 9}, "result": 1}],
    ):
        rsp = client.get("/livez")

    assert rsp.status_code == 500
    assert rsp.json() == {"detail": [{"arguments": {"pid": 9}, "result": 1}]}


def test_livez_reports_timeout():
    """Kea control-channel timeouts surface as a liveness failure."""
    client = TestClient(app)

    with patch(
        "nv_config_manager.dhcp.api.KeaClient.status",
        new_callable=AsyncMock,
        side_effect=TimeoutError("KEA Request timed out"),
    ):
        rsp = client.get("/livez")

    assert rsp.status_code == 500
    assert rsp.json() == {"detail": "KEA Request timed out"}


def test_metrics():
    """Verify /metrics returns Prometheus metrics without auth."""
    client = TestClient(app)

    with patch(
        "nv_config_manager.dhcp.api.RedisClient.load_refresh_timestamp",
        new_callable=AsyncMock,
        side_effect=lambda v: 1700000000.0 if v == 4 else None,
    ):
        rsp = client.get("/metrics")
        assert rsp.status_code == 200
        assert "text/plain" in rsp.headers["content-type"]
        body = rsp.text
        assert "nv_config_manager_dhcp_cache_last_refresh_timestamp_seconds" in body
        assert 'ip_version="4"' in body
        assert 'ip_version="6"' not in body

    with patch(
        "nv_config_manager.dhcp.api.RedisClient.load_refresh_timestamp",
        new_callable=AsyncMock,
        return_value=None,
    ):
        rsp = client.get("/metrics")
        assert rsp.status_code == 200
        assert "nv_config_manager_dhcp_cache_last_refresh_timestamp_seconds" in rsp.text


def test_whoami_requires_auth():
    """Verify /whoami is protected on the DHCP API."""
    client = TestClient(app)

    with patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED):
        rsp = client.get("/whoami")
        assert rsp.status_code == 403

        rsp = client.get("/whoami", headers={"X-Auth-Request-Email": "admin@example.com"})
        assert rsp.status_code == 200
        assert rsp.json() == {"user": "admin", "roles": ["all"]}


def test_flush_cache():
    """Verify DELETE /admin/cache."""
    client = TestClient(app)

    with patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED):
        rsp = client.delete("/admin/cache")
        assert rsp.status_code == 403

        with patch(
            "nv_config_manager.dhcp.api.RedisClient.flush_kea_config",
            new_callable=AsyncMock,
            return_value=True,
        ):
            rsp = client.delete(
                "/admin/cache",
                headers={"X-Auth-Request-Email": "admin@example.com"},
            )
            assert rsp.status_code == 200
            assert rsp.json() == {"detail": "DHCPv4 cached configuration flushed"}

        with patch(
            "nv_config_manager.dhcp.api.RedisClient.flush_kea_config",
            new_callable=AsyncMock,
            return_value=True,
        ):
            rsp = client.delete(
                "/admin/cache?ip_version=6",
                headers={"X-Auth-Request-Email": "admin@example.com"},
            )
            assert rsp.status_code == 200
            assert rsp.json() == {"detail": "DHCPv6 cached configuration flushed"}

        with patch(
            "nv_config_manager.dhcp.api.RedisClient.flush_kea_config",
            new_callable=AsyncMock,
            return_value=False,
        ):
            rsp = client.delete(
                "/admin/cache",
                headers={"X-Auth-Request-Email": "admin@example.com"},
            )
            assert rsp.status_code == 404
            assert rsp.json() == {"detail": "No cached configuration found for DHCPv4"}


def test_get_config_success():
    """Verify /config GET success."""
    client = TestClient(app)
    with open(os.path.join(_THIS_DIR, "resources/config_get.json")) as f:
        mock_response = json.load(f)

    with patch(
        "nv_config_manager.dhcp.api.KeaClient.get_config", new_callable=AsyncMock
    ) as mock_get_config:
        mock_get_config.return_value = mock_response
        with patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED):
            # Did not come in through nginx sso
            rsp = client.get("/config")
            assert rsp.status_code == 403
            assert rsp.json() == {"detail": "This endpoint requires SSO authentication."}

            # Came in through nginx sso
            rsp = client.get("/config", headers={"X-Auth-Request-Email": "test@example.com"})
            assert rsp.status_code == 200
            assert rsp.json() == MIN_READY_CONFIG


def test_get_config_accepts_non_oidc_jwt_provider():
    """Verify DHCP accepts a valid Bearer JWT from any configured JWT provider."""
    client = TestClient(app)
    with open(os.path.join(_THIS_DIR, "resources/config_get.json")) as f:
        mock_response = json.load(f)

    token, public_key = make_jwt_token(
        {
            "iss": "https://service-idp.example.com",
            "aud": "s:nv-config-manager",
            "sub": "service-account",
            "scopes": ["nv-config-manager"],
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
        }
    )
    mock_jwk = type("JWK", (), {"key": public_key})()

    with patch(
        "nv_config_manager.dhcp.api.KeaClient.get_config", new_callable=AsyncMock
    ) as mock_get_config:
        mock_get_config.return_value = mock_response
        with patch(
            "nv_config_manager.common.auth._auth_config", make_auth_config_with_jwt_provider()
        ):
            with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
                mock_client = type(
                    "JWKSClient",
                    (),
                    {"get_signing_key_from_jwt": lambda self, jwt: mock_jwk},
                )()
                mock_get_client.return_value = mock_client

                rsp = client.get("/config", headers={"Authorization": f"Bearer {token}"})

    assert rsp.status_code == 200
    assert rsp.json() == MIN_READY_CONFIG


def test_get_config_auth_disabled():
    """Verify /config GET succeeds without auth headers when auth is disabled."""
    client = TestClient(app)
    with open(os.path.join(_THIS_DIR, "resources/config_get.json")) as f:
        mock_response = json.load(f)

    with patch(
        "nv_config_manager.dhcp.api.KeaClient.get_config", new_callable=AsyncMock
    ) as mock_get_config:
        mock_get_config.return_value = mock_response
        with patch("nv_config_manager.common.auth._auth_config", _AUTH_DISABLED):
            rsp = client.get("/config")
            assert rsp.status_code == 200
            assert rsp.json() == MIN_READY_CONFIG


def test_get_config_error():
    """Verify /config GET with error."""
    client = TestClient(app)

    with patch(
        "nv_config_manager.dhcp.api.KeaClient.get_config", new_callable=AsyncMock
    ) as mock_get_config:
        mock_get_config.side_effect = make_client_response_error("HTTP ERROR")
        with patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED):
            rsp = client.get("/config", headers={"X-Auth-Request-Email": "test@example.com"})
            assert rsp.status_code == 500
            assert rsp.json() == {"detail": "HTTP ERROR"}


def test_get_lease():
    """Return one normalized lease without exposing KEA's command envelope."""
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_lease",
            new_callable=AsyncMock,
            return_value=LEASE_GET_RESPONSE,
        ) as mock_get_lease,
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=LEASE_DASHBOARD_CONFIG,
        ) as mock_get_config,
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        rsp = client.get("/lease/10.0.0.10?ip_version=4")
        assert rsp.status_code == 403

        rsp = client.get(
            "/lease/10.0.0.10",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert rsp.status_code == 200
    assert rsp.json()["ip_address"] == "10.0.0.10"
    assert rsp.json()["subnet"] == "10.0.0.0/24"
    assert "result" not in rsp.json()
    assert "subnet_id" not in rsp.json()
    mock_get_lease.assert_awaited_once_with("10.0.0.10", version=4)
    mock_get_config.assert_awaited_once_with(4)


def test_get_lease_infers_ipv6_from_path_address():
    """Select DHCPv6 when the item route contains an IPv6 address."""
    client = TestClient(app)
    config = [
        {
            "result": 0,
            "arguments": {
                "Dhcp6": {
                    "subnet6": [{"id": 9, "subnet": "2001:db8::/64"}],
                }
            },
        }
    ]
    lease = [
        {
            "result": 0,
            "arguments": {
                "leases": [
                    {
                        "cltt": int(time.time()) - 60,
                        "duid": "00:01:00:01:11:22:33:44",
                        "ip-address": "2001:db8::10",
                        "state": 0,
                        "subnet-id": 9,
                        "valid-lft": 3600,
                    }
                ]
            },
        }
    ]

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_lease",
            new_callable=AsyncMock,
            return_value=lease,
        ) as mock_get_lease,
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=config,
        ) as mock_get_config,
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        rsp = client.get(
            "/lease/2001:db8::10",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert rsp.status_code == 200
    assert rsp.json()["ip_address"] == "2001:db8::10"
    mock_get_lease.assert_awaited_once_with("2001:db8::10", version=6)
    mock_get_config.assert_awaited_once_with(6)


def test_item_openapi_documents_not_found() -> None:
    """Advertise domain-level missing resource responses."""
    lease_operations = app.openapi()["paths"]["/lease/{ip_address}"]

    for method in ("get", "delete"):
        assert lease_operations[method]["responses"]["404"] == {"description": "Lease not found"}
    assert app.openapi()["paths"]["/reservation/{ip_address}"]["get"]["responses"]["404"] == {
        "description": "Reservation not found"
    }


def test_lease_openapi_version_parameters() -> None:
    """Advertise collection defaults while item routes infer address version."""
    operations = (
        app.openapi()["paths"]["/lease"]["get"],
        app.openapi()["paths"]["/pool"]["get"],
        app.openapi()["paths"]["/reservation"]["get"],
        app.openapi()["paths"]["/summary"]["get"],
    )

    for operation in operations:
        parameter = next(
            parameter for parameter in operation["parameters"] if parameter["name"] == "ip_version"
        )
        assert parameter["required"] is False
        assert parameter["schema"]["default"] == 4

    item_operations = (
        app.openapi()["paths"]["/lease/{ip_address}"]["get"],
        app.openapi()["paths"]["/lease/{ip_address}"]["delete"],
        app.openapi()["paths"]["/reservation/{ip_address}"]["get"],
    )
    for operation in item_operations:
        parameter = next(
            parameter for parameter in operation["parameters"] if parameter["name"] == "ip_version"
        )
        assert parameter["required"] is False
        assert "default" not in parameter["schema"]


def test_collection_openapi_subnet_parameters() -> None:
    """Advertise the optional subnet filter on every collection route."""
    for path in ("/lease", "/reservation", "/pool"):
        parameter = next(
            parameter
            for parameter in app.openapi()["paths"][path]["get"]["parameters"]
            if parameter["name"] == "subnet"
        )
        assert parameter["required"] is False


def test_ip_version_openapi_exports_enum_names() -> None:
    """Publish stable names for generated address-family constants."""
    schema = app.openapi()["components"]["schemas"]["IpVersion"]
    assert schema["x-enum-varnames"] == ["V4", "V6"]


def test_get_lease_not_found():
    """Translate KEA's empty result into a RESTful not-found response."""
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_lease",
            new_callable=AsyncMock,
            return_value=[{"result": 3, "text": "Lease not found."}],
        ),
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=LEASE_DASHBOARD_CONFIG,
        ),
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        rsp = client.get(
            "/lease/10.0.0.99?ip_version=4",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert rsp.status_code == 404
    assert rsp.json() == {"detail": "Lease 10.0.0.99 was not found"}


def test_list_leases():
    """Return a bounded normalized lease collection."""
    client = TestClient(app)
    lease_page = [{"result": 0, "arguments": {"leases": [LEASE_GET_RESPONSE[0]["arguments"]]}}]

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_lease_page",
            new_callable=AsyncMock,
            return_value=lease_page,
        ) as mock_get_lease_page,
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=LEASE_DASHBOARD_CONFIG,
        ),
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        rsp = client.get(
            "/lease?limit=25",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert rsp.status_code == 200
    payload = rsp.json()
    assert len(payload["leases"]) == 1
    assert payload["leases"][0]["subnet"] == "10.0.0.0/24"
    assert payload["next_cursor"] is None
    mock_get_lease_page.assert_awaited_once_with(
        25,
        version=4,
        from_address="start",
    )


def test_list_leases_filters_by_subnet():
    """Return only active leases assigned to the requested subnet."""
    client = TestClient(app)
    config = deepcopy(LEASE_DASHBOARD_CONFIG)
    config[0]["arguments"]["Dhcp4"]["subnet4"].append(
        {
            "id": 8,
            "subnet": "10.0.1.0/24",
            "pools": [{"pool": "10.0.1.10-10.0.1.19"}],
        }
    )
    lease_payload = lease_page(
        active_lease("10.0.0.10", "leaf-01"),
        active_lease("10.0.1.10", "leaf-02", subnet_id=8),
    )

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_lease_page",
            new_callable=AsyncMock,
            return_value=lease_payload,
        ),
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=config,
        ),
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        rsp = client.get(
            "/lease",
            params={"limit": 25, "subnet": "10.0.1.0/24"},
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert rsp.status_code == 200
    assert [lease["hostname"] for lease in rsp.json()["leases"]] == ["leaf-02"]
    assert rsp.json()["leases"][0]["subnet"] == "10.0.1.0/24"
    assert rsp.json()["next_cursor"] is None


def test_list_leases_follows_opaque_cursor():
    """Continue the normalized collection from KEA's last page address."""
    client = TestClient(app)
    first_page = lease_page(
        active_lease("10.0.0.10", "leaf-01"),
        active_lease("10.0.0.11", "leaf-02"),
    )
    second_page = lease_page(active_lease("10.0.0.12", "leaf-03"))

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_lease_page",
            new_callable=AsyncMock,
            side_effect=(first_page, second_page),
        ) as mock_get_lease_page,
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=LEASE_DASHBOARD_CONFIG,
        ),
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        first_rsp = client.get(
            "/lease?limit=2",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )
        cursor = first_rsp.json()["next_cursor"]
        second_rsp = client.get(
            "/lease",
            params={"cursor": cursor, "limit": 2},
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert first_rsp.status_code == 200
    assert [lease["hostname"] for lease in first_rsp.json()["leases"]] == [
        "leaf-01",
        "leaf-02",
    ]
    assert cursor is not None
    assert second_rsp.status_code == 200
    assert [lease["hostname"] for lease in second_rsp.json()["leases"]] == ["leaf-03"]
    assert second_rsp.json()["next_cursor"] is None
    assert mock_get_lease_page.await_args_list == [
        call(2, version=4, from_address="start"),
        call(2, version=4, from_address="10.0.0.11"),
    ]


def test_list_leases_bounds_search_across_backend_pages():
    """Search 1,000 leases while bounding KEA work behind a continuation cursor."""
    client = TestClient(app)
    pages = [
        lease_page(
            *(
                active_lease(
                    f"10.{page_index}.0.{lease_index}",
                    "target-switch"
                    if page_index == 9 and lease_index == 100
                    else f"leaf-{page_index:02d}-{lease_index:03d}",
                )
                for lease_index in range(1, 101)
            )
        )
        for page_index in range(10)
    ]
    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_lease_page",
            new_callable=AsyncMock,
            side_effect=pages,
        ) as mock_get_lease_page,
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=LEASE_DASHBOARD_CONFIG,
        ),
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        rsp = client.get(
            "/lease?limit=100&search=target",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert rsp.status_code == 200
    assert [lease["hostname"] for lease in rsp.json()["leases"]] == ["target-switch"]
    assert rsp.json()["next_cursor"] is not None
    assert mock_get_lease_page.await_count == 10
    assert mock_get_lease_page.await_args_list[0] == call(
        100,
        version=4,
        from_address="start",
    )
    assert mock_get_lease_page.await_args_list[-1] == call(
        100,
        version=4,
        from_address="10.8.0.100",
    )


def test_list_leases_rejects_invalid_cursor():
    """Reject malformed cursors before contacting the DHCP server."""
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_lease_page",
            new_callable=AsyncMock,
        ) as mock_get_lease_page,
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        rsp = client.get(
            "/lease?cursor=not-a-cursor",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert rsp.status_code == 422
    assert rsp.json() == {"detail": "Invalid lease cursor"}
    mock_get_lease_page.assert_not_awaited()


def test_get_reservation_by_ip_address():
    """Return one normalized reservation and infer its IP version."""
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=LEASE_DASHBOARD_CONFIG,
        ) as mock_get_config,
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        rsp = client.get(
            "/reservation/10.0.0.2",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert rsp.status_code == 200
    assert rsp.json() == {
        "hostname": "reserved-switch",
        "identifier": "02:00:00:00:00:01",
        "identifier_type": "hw-address",
        "ip_address": "10.0.0.2",
        "subnet": None,
    }
    mock_get_config.assert_awaited_once_with(4)


def test_get_reservation_not_found():
    """Return 404 when no reservation is configured for the address."""
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=LEASE_DASHBOARD_CONFIG,
        ),
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        rsp = client.get(
            "/reservation/10.0.0.99",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert rsp.status_code == 404
    assert rsp.json() == {"detail": "Reservation 10.0.0.99 was not found"}


def test_get_reservation_rejects_mismatched_version():
    """Reject an explicit version that conflicts with the reservation address."""
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
        ) as mock_get_config,
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        rsp = client.get(
            "/reservation/10.0.0.2?ip_version=6",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert rsp.status_code == 422
    assert rsp.json() == {"detail": "IP address version does not match ip_version=6"}
    mock_get_config.assert_not_awaited()


def test_list_reservations_paginates_and_filters_with_exact_total():
    """Return bounded reservation pages with exact filtered totals."""
    client = TestClient(app)
    config = deepcopy(LEASE_DASHBOARD_CONFIG)
    dhcp_config = config[0]["arguments"]["Dhcp4"]
    dhcp_config["reservations"].append(
        {
            "hostname": "reserved-switch-02",
            "hw-address": "02:00:00:00:00:02",
            "ip-address": "10.0.0.3",
        }
    )
    dhcp_config["subnet4"][0]["reservations"] = [
        {
            "client-id": "01:02:03:04",
            "hostname": "reserved-switch-03",
            "ip-address": "10.0.0.4",
        }
    ]

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=config,
        ) as mock_get_config,
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        first_rsp = client.get(
            "/reservation?limit=2",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )
        cursor = first_rsp.json()["next_cursor"]
        second_rsp = client.get(
            "/reservation",
            params={"cursor": cursor, "limit": 2},
            headers={"X-Auth-Request-Email": "test@example.com"},
        )
        search_rsp = client.get(
            "/reservation?limit=2&search=0200.0000.0002",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert first_rsp.status_code == 200
    assert first_rsp.json()["total_count"] == 3
    assert [item["hostname"] for item in first_rsp.json()["reservations"]] == [
        "reserved-switch",
        "reserved-switch-02",
    ]
    assert cursor is not None
    assert second_rsp.status_code == 200
    assert second_rsp.json()["total_count"] == 3
    assert [item["hostname"] for item in second_rsp.json()["reservations"]] == [
        "reserved-switch-03"
    ]
    assert second_rsp.json()["next_cursor"] is None
    assert search_rsp.status_code == 200
    assert search_rsp.json()["total_count"] == 1
    assert search_rsp.json()["reservations"][0]["hostname"] == "reserved-switch-02"
    assert mock_get_config.await_count == 3
    assert all(call.args == (4,) for call in mock_get_config.await_args_list)


def test_list_reservations_rejects_mismatched_cursor_version():
    """Reject reservation cursors created for another address family."""
    client = TestClient(app)
    config = deepcopy(LEASE_DASHBOARD_CONFIG)
    config[0]["arguments"]["Dhcp4"]["reservations"].append(
        {
            "hostname": "reserved-switch-02",
            "ip-address": "10.0.0.3",
        }
    )
    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=config,
        ) as mock_get_config,
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        first_rsp = client.get(
            "/reservation?limit=1",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )
        rsp = client.get(
            "/reservation",
            params={"cursor": first_rsp.json()["next_cursor"], "ip_version": 6},
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert rsp.status_code == 422
    assert rsp.json()["detail"] == "Reservation cursor does not match ip_version=6"
    mock_get_config.assert_awaited_once_with(4)


def test_list_reservations_filters_by_subnet_before_pagination():
    """Filter reservations by configured subnet before slicing exact-total pages."""
    client = TestClient(app)
    config = deepcopy(LEASE_DASHBOARD_CONFIG)
    dhcp_config = config[0]["arguments"]["Dhcp4"]
    dhcp_config["subnet4"][0]["reservations"] = [
        {"hostname": "leaf-a-01", "ip-address": "10.0.0.2"},
        {"hostname": "leaf-a-02", "ip-address": "10.0.0.3"},
    ]
    dhcp_config["subnet4"].append(
        {
            "id": 8,
            "subnet": "10.0.1.0/24",
            "pools": [],
            "reservations": [{"hostname": "leaf-b-01", "ip-address": "10.0.1.2"}],
        }
    )

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=config,
        ),
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        first_rsp = client.get(
            "/reservation",
            params={"limit": 1, "subnet": "10.0.0.0/24"},
            headers={"X-Auth-Request-Email": "test@example.com"},
        )
        second_rsp = client.get(
            "/reservation",
            params={
                "cursor": first_rsp.json()["next_cursor"],
                "limit": 1,
                "subnet": "10.0.0.0/24",
            },
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert first_rsp.status_code == 200
    assert first_rsp.json()["total_count"] == 2
    assert [item["hostname"] for item in first_rsp.json()["reservations"]] == ["leaf-a-01"]
    assert first_rsp.json()["next_cursor"] is not None
    assert second_rsp.status_code == 200
    assert second_rsp.json()["total_count"] == 2
    assert [item["hostname"] for item in second_rsp.json()["reservations"]] == ["leaf-a-02"]
    assert second_rsp.json()["next_cursor"] is None


def test_list_pools_paginates_and_filters_with_exact_total():
    """Return bounded pool pages with exact filtered totals."""
    client = TestClient(app)
    config = deepcopy(LEASE_DASHBOARD_CONFIG)
    config[0]["arguments"]["Dhcp4"]["subnet4"][0]["pools"].append({"pool": "10.0.1.0/30"})

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=config,
        ) as mock_get_config,
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        first_rsp = client.get(
            "/pool?limit=1",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )
        second_rsp = client.get(
            "/pool",
            params={"cursor": first_rsp.json()["next_cursor"], "limit": 1},
            headers={"X-Auth-Request-Email": "test@example.com"},
        )
        search_rsp = client.get(
            "/pool?limit=1&search=10.0.1.0/30",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert first_rsp.status_code == 200
    assert first_rsp.json()["total_count"] == 2
    assert first_rsp.json()["pools"][0]["pool"] == "10.0.0.10-10.0.0.19"
    assert second_rsp.status_code == 200
    assert second_rsp.json()["total_count"] == 2
    assert second_rsp.json()["pools"][0]["pool"] == "10.0.1.0/30"
    assert second_rsp.json()["next_cursor"] is None
    assert search_rsp.status_code == 200
    assert search_rsp.json()["total_count"] == 1
    assert search_rsp.json()["pools"] == [{"subnet": "10.0.0.0/24", "pool": "10.0.1.0/30"}]
    assert mock_get_config.await_count == 3


def test_list_pools_filters_by_subnet():
    """Return only pools configured in the requested subnet."""
    client = TestClient(app)
    config = deepcopy(LEASE_DASHBOARD_CONFIG)
    dhcp_config = config[0]["arguments"]["Dhcp4"]
    dhcp_config["subnet4"][0]["pools"].append({"pool": "10.0.0.20-10.0.0.29"})
    dhcp_config["subnet4"].append(
        {
            "id": 8,
            "subnet": "10.0.1.0/24",
            "pools": [{"pool": "10.0.1.10-10.0.1.19"}],
        }
    )

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=config,
        ) as mock_get_config,
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        first_rsp = client.get(
            "/pool",
            params={"limit": 1, "subnet": "10.0.0.0/24"},
            headers={"X-Auth-Request-Email": "test@example.com"},
        )
        second_rsp = client.get(
            "/pool",
            params={
                "cursor": first_rsp.json()["next_cursor"],
                "limit": 1,
                "subnet": "10.0.0.0/24",
            },
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert first_rsp.status_code == 200
    assert first_rsp.json()["total_count"] == 2
    assert first_rsp.json()["pools"] == [{"subnet": "10.0.0.0/24", "pool": "10.0.0.10-10.0.0.19"}]
    assert first_rsp.json()["next_cursor"] is not None
    assert second_rsp.status_code == 200
    assert second_rsp.json()["total_count"] == 2
    assert second_rsp.json()["pools"] == [{"subnet": "10.0.0.0/24", "pool": "10.0.0.20-10.0.0.29"}]
    assert second_rsp.json()["next_cursor"] is None
    assert mock_get_config.await_count == 2
    assert all(call.args == (4,) for call in mock_get_config.await_args_list)


@pytest.mark.parametrize("resource", ("lease", "reservation", "pool"))
def test_list_collections_reject_mismatched_subnet_version(resource: str):
    """Reject a collection version that conflicts with its subnet filter."""
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
        ) as mock_get_config,
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_lease_page",
            new_callable=AsyncMock,
        ) as mock_get_lease_page,
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        rsp = client.get(
            f"/{resource}",
            params={"ip_version": 6, "subnet": "10.0.0.0/24"},
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert rsp.status_code == 422
    assert rsp.json() == {"detail": "IP subnet version does not match ip_version=6"}
    mock_get_config.assert_not_awaited()
    mock_get_lease_page.assert_not_awaited()


def test_config_collections_bound_thousand_record_pages():
    """Keep reservation and pool responses bounded with thousands of records."""
    client = TestClient(app)
    config = deepcopy(LEASE_DASHBOARD_CONFIG)
    dhcp_config = config[0]["arguments"]["Dhcp4"]
    dhcp_config["reservations"] = [
        {
            "hostname": f"reserved-switch-{index:04d}",
            "hw-address": f"02:00:{index // 256:02x}:{index % 256:02x}:00:01",
        }
        for index in range(1000)
    ]
    dhcp_config["subnet4"][0]["pools"] = [
        {"pool": f"10.{index // 256}.{index % 256}.1"} for index in range(1000)
    ]

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=config,
        ),
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        reservation_rsp = client.get(
            "/reservation?limit=100",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )
        pool_rsp = client.get(
            "/pool?limit=100",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert reservation_rsp.status_code == 200
    assert reservation_rsp.json()["total_count"] == 1000
    assert len(reservation_rsp.json()["reservations"]) == 100
    assert reservation_rsp.json()["next_cursor"] is not None
    assert pool_rsp.status_code == 200
    assert pool_rsp.json()["total_count"] == 1000
    assert len(pool_rsp.json()["pools"]) == 100
    assert pool_rsp.json()["next_cursor"] is not None


def test_delete_lease():
    """Delete a lease through the domain API without returning KEA's body."""
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.delete_lease",
            new_callable=AsyncMock,
            return_value=[{"result": 0, "text": "Lease deleted."}],
        ) as mock_delete_lease,
    ):
        with patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED):
            rsp = client.delete(
                "/lease/10.0.0.10",
                headers={"X-Auth-Request-Email": "test@example.com"},
            )

    assert rsp.status_code == 204
    assert not rsp.content
    mock_delete_lease.assert_awaited_once_with("10.0.0.10", version=4)


def test_delete_lease_enforces_allowed_groups():
    """Reject lease deletion when the caller is outside DHCP's allowed groups."""
    client = TestClient(app)
    auth_config = AuthConfig(
        accept_request_headers=True,
        allowed_groups=("dhcp-admins",),
    )

    with patch(
        "nv_config_manager.dhcp.api.KeaClient.delete_lease",
        new_callable=AsyncMock,
    ) as mock_delete_lease:
        with patch("nv_config_manager.common.auth._auth_config", auth_config):
            rsp = client.delete(
                "/lease/10.0.0.10?ip_version=4",
                headers={
                    "X-Auth-Request-Email": "test@example.com",
                    "X-Auth-Request-Groups": "dhcp-viewers",
                },
            )

    assert rsp.status_code == 403
    mock_delete_lease.assert_not_awaited()


def test_delete_lease_not_found():
    """Return 404 when the selected DHCP service has no matching lease."""
    client = TestClient(app)

    with patch(
        "nv_config_manager.dhcp.api.KeaClient.delete_lease",
        new_callable=AsyncMock,
        return_value=[{"result": 3, "text": "Lease not found."}],
    ):
        with patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED):
            rsp = client.delete(
                "/lease/10.0.0.99?ip_version=4",
                headers={"X-Auth-Request-Email": "test@example.com"},
            )

    assert rsp.status_code == 404


def test_lease_address_must_match_ip_version():
    """Reject addresses that do not match the selected DHCP service."""
    client = TestClient(app)

    with patch(
        "nv_config_manager.dhcp.api.KeaClient.get_lease",
        new_callable=AsyncMock,
    ) as mock_get_lease:
        with patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED):
            rsp = client.get(
                "/lease/2001:db8::1?ip_version=4",
                headers={"X-Auth-Request-Email": "test@example.com"},
            )

    assert rsp.status_code == 422
    mock_get_lease.assert_not_awaited()


def test_get_lease_http_error():
    """Verify KEA HTTP errors are surfaced by the DHCP API."""
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_lease",
            new_callable=AsyncMock,
            side_effect=make_client_response_error("HTTP ERROR"),
        ),
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=LEASE_DASHBOARD_CONFIG,
        ),
        patch(
            "nv_config_manager.dhcp.api.KeaClient.close",
            new_callable=AsyncMock,
        ) as mock_close,
    ):
        with patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED):
            rsp = client.get(
                "/lease/10.0.0.10?ip_version=4",
                headers={"X-Auth-Request-Email": "test@example.com"},
            )

    assert rsp.status_code == 500
    assert rsp.json() == {"detail": "HTTP ERROR"}
    mock_close.assert_awaited_once()


def test_get_lease_timeout():
    """Verify KEA timeouts are surfaced by the DHCP API."""
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_lease",
            new_callable=AsyncMock,
            side_effect=TimeoutError("KEA Request timed out"),
        ),
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=LEASE_DASHBOARD_CONFIG,
        ),
    ):
        with patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED):
            rsp = client.get(
                "/lease/10.0.0.10?ip_version=4",
                headers={"X-Auth-Request-Email": "test@example.com"},
            )

    assert rsp.status_code == 500
    assert rsp.json() == {"detail": "KEA Request timed out"}


def test_delete_lease_connection_error():
    """Verify other KEA transport errors are surfaced by the DHCP API."""
    client = TestClient(app)

    with patch(
        "nv_config_manager.dhcp.api.KeaClient.delete_lease",
        new_callable=AsyncMock,
        side_effect=ClientConnectionError("KEA connection failed"),
    ):
        with patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED):
            rsp = client.delete(
                "/lease/10.0.0.10?ip_version=4",
                headers={"X-Auth-Request-Email": "test@example.com"},
            )

    assert rsp.status_code == 500
    assert rsp.json() == {"detail": "KEA connection failed"}


def test_get_summary():
    """Verify GET /summary combines KEA summary data."""
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=LEASE_DASHBOARD_CONFIG,
        ) as mock_get_config,
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_statistics",
            new_callable=AsyncMock,
            return_value=LEASE_DASHBOARD_STATISTICS,
        ) as mock_get_statistics,
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        rsp = client.get("/summary?ip_version=4")
        assert rsp.status_code == 403

        rsp = client.get(
            "/summary",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert rsp.status_code == 200
    payload = rsp.json()
    assert payload["active_lease_count"] == 1
    assert payload["reservation_count"] == 1
    assert payload["pool_count"] == 1
    assert "leases" not in payload
    assert "reservations" not in payload
    assert "pools" not in payload
    mock_get_config.assert_awaited_once_with(4)
    mock_get_statistics.assert_awaited_once_with(4)


def test_get_summary_kea_error():
    """Surface logical KEA failures as DHCP API errors."""
    client = TestClient(app)

    with (
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_config",
            new_callable=AsyncMock,
            return_value=[{"result": 1, "text": "configuration unavailable"}],
        ),
        patch(
            "nv_config_manager.dhcp.api.KeaClient.get_statistics",
            new_callable=AsyncMock,
            return_value=LEASE_DASHBOARD_STATISTICS,
        ),
        patch("nv_config_manager.common.auth._auth_config", _HEADERS_TRUSTED),
    ):
        rsp = client.get(
            "/summary",
            headers={"X-Auth-Request-Email": "test@example.com"},
        )

    assert rsp.status_code == 500
    assert rsp.json() == {"detail": "KEA config-get failed: configuration unavailable"}


async def test_summary_source_failure_cancels_and_drains_siblings() -> None:
    """Cancel and await sibling KEA requests before propagating a failure."""
    statistics_started = asyncio.Event()
    cancelled: set[str] = set()

    async def fail_config(version: int) -> list[dict]:
        """Fail after the sibling request has started."""
        assert version == 4
        await statistics_started.wait()
        raise ClientConnectionError("KEA connection failed")

    async def block_statistics(version: int) -> list[dict]:
        """Record cancellation of the in-flight statistics request."""
        assert version == 4
        statistics_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.add("statistics")
            raise

    client = KeaClient(host="kea.example.com", port=8000)
    with (
        patch.object(client, "get_config", new=AsyncMock(side_effect=fail_config)),
        patch.object(client, "get_statistics", new=AsyncMock(side_effect=block_statistics)),
        pytest.raises(ClientConnectionError, match="KEA connection failed"),
    ):
        await _fetch_summary_sources(client, ip_version=4)

    assert cancelled == {"statistics"}
