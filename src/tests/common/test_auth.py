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
"""Tests for nv_config_manager.common.auth JWT and SPIFFE authentication."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from configparser import ConfigParser
from threading import Lock
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as StarletteClient

import nv_config_manager.common.auth as auth_mod
from nv_config_manager.common.auth import (
    JWKS_KEYSET_CACHE_LIFESPAN_SECONDS,
    SSOIdentity,
    _derive_jwks_uri,
    _get_jwks_client,
    _get_signing_key_from_jwks,
    _jwks_clients,
    _jwks_signing_key_locks,
    _spiffe_id_to_workload_name,
    extract_identity,
    identity_from_sso_headers,
    install_identity_probe,
    load_auth_config,
    require_authenticated_identity,
    require_group,
)
from nv_config_manager.common.config import clear_config_cache

# ── Helpers ───────────────────────────────────────────────────────────────


def _make_config(**sections: dict[str, str]) -> ConfigParser:
    """Build a ConfigParser from keyword arg sections.

    Example::

        _make_config(**{
            "auth": {"required": "true"},
            "auth.jwt.azure": {"issuer": "...", "audiences": "a,b"},
        })
    """
    cp = ConfigParser()
    for section, values in sections.items():
        cp.add_section(section)
        for k, v in values.items():
            cp.set(section, k, v)
    return cp


def _inject_config(config: ConfigParser):
    """Patch load_auth_config so it uses the given ConfigParser."""
    auth_mod._auth_config = None
    return patch.object(
        auth_mod, "load_auth_config", lambda cfg=None: auth_mod.load_auth_config(config)
    )


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_caches():
    """Reset module-level caches between tests."""
    _jwks_clients.clear()
    _jwks_signing_key_locks.clear()
    auth_mod._auth_config = None
    auth_mod._auth_config_source = None
    auth_mod._auth_config_tracks_file = False
    yield
    _jwks_clients.clear()
    _jwks_signing_key_locks.clear()
    auth_mod._auth_config = None
    auth_mod._auth_config_source = None
    auth_mod._auth_config_tracks_file = False


@pytest.fixture
def rsa_keypair():
    """Generate an RSA keypair for signing test JWTs."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def make_jwt(rsa_keypair):
    """Factory to create signed JWTs with given claims."""

    def _make(claims: dict, kid: str = "test-kid"):
        return pyjwt.encode(
            claims,
            rsa_keypair,
            algorithm="RS256",
            headers={"kid": kid},
        )

    return _make


@pytest.fixture
def app():
    """Minimal FastAPI app for testing."""
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(request: Request):
        identity = extract_identity(request)
        if identity:
            return {
                "email": identity.email,
                "user": identity.user,
                "groups": sorted(identity.groups),
                "source": identity.source,
            }
        return {"identity": None}

    return app


@pytest.fixture
def client(app):
    return TestClient(app)


# ── INI config loading tests ─────────────────────────────────────────────


class TestConfigLoading:
    """Tests for load_auth_config and INI section parsing."""

    def test_empty_config_defaults(self):
        """An empty INI returns safe defaults."""
        cfg = load_auth_config(ConfigParser())
        assert cfg.required is True
        assert cfg.accept_request_headers is False
        assert cfg.cookie_name == "NVConfigManagerAccessToken"
        assert cfg.jwt_providers == ()
        assert cfg.spiffe is None

    def test_auth_section_parsed(self):
        """[auth] section values are respected."""
        cp = _make_config(
            auth={
                "required": "false",
                "accept_request_headers": "true",
                "cookie_name": "MyCookie",
            }
        )
        cfg = load_auth_config(cp)
        assert cfg.required is False
        assert cfg.accept_request_headers is True
        assert cfg.cookie_name == "MyCookie"

    @pytest.mark.parametrize(
        ("environment_value", "expected"),
        [("true", True), ("1", True), ("false", False), ("0", False)],
    )
    def test_request_header_trust_environment_override(
        self, monkeypatch, environment_value, expected
    ):
        """A listener can override the shared INI header-trust policy."""
        monkeypatch.setenv("ACCEPT_REQUEST_HEADERS", environment_value)
        cp = _make_config(auth={"accept_request_headers": str(not expected)})

        cfg = load_auth_config(cp)

        assert cfg.accept_request_headers is expected

    def test_request_header_trust_rejects_invalid_environment_value(self, monkeypatch):
        monkeypatch.setenv("ACCEPT_REQUEST_HEADERS", "sometimes")

        with pytest.raises(ValueError, match="ACCEPT_REQUEST_HEADERS must be a boolean"):
            load_auth_config(ConfigParser())

    def test_single_jwt_provider(self):
        """A single [auth.jwt.*] section is parsed correctly."""
        cp = _make_config(
            **{
                "auth.jwt.azure": {
                    "issuer": "https://login.microsoftonline.com/tenant/v2.0",
                    "audiences": "api://my-app",
                },
            }
        )
        cfg = load_auth_config(cp)
        assert len(cfg.jwt_providers) == 1
        p = cfg.jwt_providers[0]
        assert p.name == "azure"
        assert p.issuer == "https://login.microsoftonline.com/tenant/v2.0"
        assert p.audiences == ["api://my-app"]
        assert "discovery/v2.0/keys" in p.jwks_uri

    def test_multiple_jwt_providers(self):
        """Multiple [auth.jwt.*] sections are all loaded."""
        cp = _make_config(
            **{
                "auth.jwt.azure": {
                    "issuer": "https://login.microsoftonline.com/t/v2.0",
                    "audiences": "api://app1",
                },
                "auth.jwt.service": {
                    "issuer": "https://service-idp.example.com",
                    "audiences": "service-api",
                    "jwks_uri": "https://service-idp.example.com/.well-known/jwks.json",
                    "claim_email": "sub",
                    "claim_user": "sub",
                    "claim_groups": "scopes",
                },
            }
        )
        cfg = load_auth_config(cp)
        assert len(cfg.jwt_providers) == 2
        names = {p.name for p in cfg.jwt_providers}
        assert names == {"azure", "service"}
        service = next(p for p in cfg.jwt_providers if p.name == "service")
        assert service.claim_email == "sub"
        assert service.jwks_uri == "https://service-idp.example.com/.well-known/jwks.json"

    def test_spiffe_section_parsed(self):
        """[auth.spiffe] section is parsed into SpiffeConfig."""
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://cluster.local",
                },
            }
        )
        cfg = load_auth_config(cp)
        assert cfg.spiffe is not None
        assert cfg.spiffe.jwks_uri == "https://spire-server:8443/keys"
        assert cfg.spiffe.audiences == ["spiffe://cluster.local"]
        assert cfg.spiffe.group_prefixes == ()

    def test_spiffe_missing_jwks_uri_returns_none(self):
        """[auth.spiffe] without jwks_uri yields no SpiffeConfig."""
        cp = _make_config(
            **{
                "auth.spiffe": {"audiences": "spiffe://domain"},
            }
        )
        cfg = load_auth_config(cp)
        assert cfg.spiffe is None

    def test_provider_without_issuer_skipped(self):
        """A [auth.jwt.*] section with no issuer is silently skipped."""
        cp = _make_config(
            **{
                "auth.jwt.broken": {"audiences": "aud"},
            }
        )
        cfg = load_auth_config(cp)
        assert len(cfg.jwt_providers) == 0

    def test_file_backed_auth_config_reloads_after_ini_update(self, monkeypatch, tmp_path):
        config_file = tmp_path / "nv-config-manager.ini"
        config_file.write_text("[auth]\nrequired = true\n")
        monkeypatch.setenv("NV_CONFIG_MANAGER_INI", str(config_file))
        clear_config_cache()

        first = load_auth_config()
        assert first.required is True

        config_file.write_text("[auth]\nrequired = false\n")
        second = load_auth_config()

        assert second is not first
        assert second.required is False


# ── install_identity_probe enforcement tests ─────────────────────────────


class TestInstallIdentityProbe:
    """Tests for shared identity probe auth enforcement middleware."""

    def _make_app(self):
        app = FastAPI()

        @app.get("/healthcheck")
        async def healthcheck():
            return "OK"

        @app.get("/protected")
        async def protected():
            return {"ok": True}

        @app.get("/state-user")
        async def state_user(request: Request):
            """Expose normalized request identity fields for middleware tests."""
            return {
                "user": request.state.user,
                "auth_source": request.state.auth_source,
            }

        install_identity_probe(app)
        return app

    def test_auth_required_by_default_except_healthchecks(self):
        auth_mod._auth_config = load_auth_config(
            _make_config(auth={"required": "true", "accept_request_headers": "true"})
        )
        client = TestClient(self._make_app())

        assert client.get("/healthcheck").status_code == 200
        assert client.get("/protected").status_code == 403

        resp = client.get("/protected", headers={"X-Auth-Request-Email": "alice@example.com"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        resp = client.get("/state-user", headers={"X-Auth-Request-Email": "alice@example.com"})
        assert resp.status_code == 200
        assert resp.json() == {"user": "alice", "auth_source": "sso"}

    def test_docs_are_protected(self):
        auth_mod._auth_config = load_auth_config(
            _make_config(auth={"required": "true", "accept_request_headers": "true"})
        )
        client = TestClient(self._make_app())

        assert client.get("/docs").status_code == 403

    def test_auth_disabled_allows_non_healthcheck_paths(self):
        auth_mod._auth_config = load_auth_config(_make_config(auth={"required": "false"}))
        client = TestClient(self._make_app())

        resp = client.get("/protected")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        resp = client.get("/state-user")
        assert resp.status_code == 200
        assert resp.json() == {"user": "anonymous", "auth_source": "anonymous"}

    def test_openapi_describes_default_bearer_auth_and_public_paths(self):
        schema = self._make_app().openapi()

        assert schema["components"]["securitySchemes"]["BearerAuth"] == {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": auth_mod.OPENAPI_BEARER_DESCRIPTION,
        }
        assert "security" not in schema
        assert schema["paths"]["/healthcheck"]["get"]["security"] == []
        assert schema["paths"]["/protected"]["get"]["security"] == [{"BearerAuth": []}]

    def test_openapi_marks_deferred_device_auth_as_optional_bearer(self):
        app = FastAPI()

        @app.get("/v1/device/{device_id}")
        async def device(device_id: str):
            return {"device_id": device_id}

        install_identity_probe(app, deferred_auth_prefixes=("/v1/device/",))
        schema = app.openapi()

        assert schema["paths"]["/v1/device/{device_id}"]["get"]["security"] == [
            {"BearerAuth": []},
            {},
        ]


# ── JWKS URI derivation tests ────────────────────────────────────────────


class TestJwksUriDerivation:
    """Tests for _derive_jwks_uri."""

    def test_azure_ad(self):
        uri = _derive_jwks_uri("https://login.microsoftonline.com/tenant-id/v2.0")
        assert uri == "https://login.microsoftonline.com/tenant-id/v2.0/discovery/v2.0/keys"

    def test_keycloak(self):
        uri = _derive_jwks_uri("https://keycloak.example.com/realms/myrealm")
        assert uri == "https://keycloak.example.com/realms/myrealm/protocol/openid-connect/certs"


# ── identity_from_jwt tests ──────────────────────────────────────────────


class TestIdentityFromJwt:
    """Tests for OIDC/browser JWT validation (multi-issuer)."""

    def test_no_config_returns_none(self, client):
        """When no JWT providers are configured, identity_from_jwt returns None."""
        auth_mod._auth_config = load_auth_config(ConfigParser())
        resp = client.get("/whoami")
        assert resp.json() == {"identity": None}

    def test_no_token_returns_none(self, client):
        """When no Authorization header, returns None."""
        cp = _make_config(
            **{
                "auth.jwt.test": {
                    "issuer": "https://idp.example.com",
                    "audiences": "my-app",
                    "jwks_uri": "https://idp.example.com/jwks",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)
        resp = client.get("/whoami")
        assert resp.json() == {"identity": None}

    def test_valid_jwt_returns_identity(self, rsa_keypair, make_jwt, client):
        """A valid JWT should return an identity with extracted claims."""
        claims = {
            "iss": "https://idp.example.com",
            "aud": "my-app",
            "sub": "user123",
            "email": "alice@example.com",
            "preferred_username": "alice",
            "roles": ["admin", "editor"],
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
        }
        token = make_jwt(claims)

        mock_jwk = MagicMock()
        mock_jwk.key = rsa_keypair.public_key()

        cp = _make_config(
            **{
                "auth.jwt.test": {
                    "issuer": "https://idp.example.com",
                    "audiences": "my-app",
                    "jwks_uri": "https://idp.example.com/jwks",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client

            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        data = resp.json()
        assert data["email"] == "alice@example.com"
        assert data["user"] == "alice"
        assert "admin" in data["groups"]
        assert "editor" in data["groups"]
        assert "all" in data["groups"]
        assert data["source"] == "jwt"

    def test_invalid_jwt_returns_none(self, client):
        """An invalid JWT should return None, not raise."""
        cp = _make_config(
            **{
                "auth.jwt.test": {
                    "issuer": "https://idp.example.com",
                    "audiences": "my-app",
                    "jwks_uri": "https://idp.example.com/jwks",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.side_effect = pyjwt.exceptions.PyJWKClientError(
                "No matching key found"
            )
            mock_get_client.return_value = mock_client

            resp = client.get("/whoami", headers={"Authorization": "Bearer invalid.token.here"})
            assert resp.json() == {"identity": None}

    def test_jwt_from_cookie(self, rsa_keypair, make_jwt, client):
        """JWT can be extracted from the NVConfigManagerAccessToken cookie."""
        claims = {
            "iss": "https://idp.example.com",
            "aud": "my-app",
            "email": "bob@example.com",
            "preferred_username": "bob",
            "roles": ["viewer"],
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
        }
        token = make_jwt(claims)

        mock_jwk = MagicMock()
        mock_jwk.key = rsa_keypair.public_key()

        cp = _make_config(
            **{
                "auth.jwt.test": {
                    "issuer": "https://idp.example.com",
                    "audiences": "my-app",
                    "jwks_uri": "https://idp.example.com/jwks",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client

            resp = client.get("/whoami", cookies={"NVConfigManagerAccessToken": token})

        assert resp.json()["user"] == "bob"
        assert resp.json()["source"] == "jwt"

    def test_custom_claim_mappings(self, rsa_keypair, make_jwt, client):
        """Custom claim mappings from INI should be respected."""
        claims = {
            "iss": "https://idp.example.com",
            "aud": "my-app",
            "sub": "svc-account",
            "scopes": ["read", "write"],
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
        }
        token = make_jwt(claims)

        mock_jwk = MagicMock()
        mock_jwk.key = rsa_keypair.public_key()

        cp = _make_config(
            **{
                "auth.jwt.custom": {
                    "issuer": "https://idp.example.com",
                    "audiences": "my-app",
                    "jwks_uri": "https://idp.example.com/jwks",
                    "claim_email": "sub",
                    "claim_user": "sub",
                    "claim_groups": "scopes",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client

            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        data = resp.json()
        assert data["email"] == "svc-account"
        assert data["user"] == "svc-account"
        assert "read" in data["groups"]
        assert "write" in data["groups"]

    def test_multi_issuer_only_fetches_matching_provider_jwks(self, rsa_keypair, make_jwt, client):
        """A token must not refresh JWKS sets for providers with another issuer."""
        claims = {
            "iss": "https://service-idp.example.com",
            "aud": "service-api",
            "sub": "bot-1",
            "scopes": ["deploy"],
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
        }
        token = make_jwt(claims)

        mock_jwk = MagicMock()
        mock_jwk.key = rsa_keypair.public_key()

        cp = _make_config(
            **{
                "auth.jwt.azure": {
                    "issuer": "https://login.microsoftonline.com/t/v2.0",
                    "audiences": "api://app",
                    "jwks_uri": "https://azure.jwks",
                },
                "auth.jwt.service": {
                    "issuer": "https://service-idp.example.com",
                    "audiences": "service-api",
                    "jwks_uri": "https://service-idp.example.com/jwks",
                    "claim_email": "sub",
                    "claim_user": "sub",
                    "claim_groups": "scopes",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client_fail = MagicMock()
            mock_client_fail.get_signing_key_from_jwt.side_effect = (
                pyjwt.exceptions.PyJWKClientError("wrong keys")
            )
            mock_client_ok = MagicMock()
            mock_client_ok.get_signing_key_from_jwt.return_value = mock_jwk

            def _pick(uri):
                if "service-idp" in uri:
                    return mock_client_ok
                return mock_client_fail

            mock_get_client.side_effect = _pick

            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        data = resp.json()
        assert data["email"] == "bot-1"
        assert data["source"] == "jwt"
        assert "deploy" in data["groups"]
        mock_get_client.assert_called_once_with("https://service-idp.example.com/jwks")

    def test_non_spiffe_jwt_does_not_fetch_spiffe_jwks(self, make_jwt, client):
        """Ordinary JWTs do not force a SPIFFE JWKS lookup before JWT auth."""
        token = make_jwt(
            {
                "iss": "https://idp.example.com",
                "sub": "operator",
                "exp": int(time.time()) + 300,
                "iat": int(time.time()),
            }
        )
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire.example.com/keys",
                    "audiences": "spiffe://cluster.local",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            response = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        assert response.json() == {"identity": None}
        mock_get_client.assert_not_called()


class TestJwksClientCache:
    def test_enables_keyset_cache(self):
        with patch.object(auth_mod.pyjwt, "PyJWKClient") as mock_client_class:
            mock_client_class.return_value = MagicMock()

            first = _get_jwks_client("https://idp.example.com/jwks")
            second = _get_jwks_client("https://idp.example.com/jwks")

        assert first is second
        mock_client_class.assert_called_once_with(
            "https://idp.example.com/jwks",
            cache_jwk_set=True,
            lifespan=JWKS_KEYSET_CACHE_LIFESPAN_SECONDS,
        )

    def test_serializes_cold_keyset_fetches(self):
        class ColdCacheClient:
            def __init__(self) -> None:
                self.cached = False
                self.fetches = 0
                self.lock = Lock()

            def get_signing_key_from_jwt(self, token: str) -> MagicMock:
                if not self.cached:
                    with self.lock:
                        self.fetches += 1
                    time.sleep(0.01)
                    self.cached = True
                return MagicMock()

        client = ColdCacheClient()
        with patch("nv_config_manager.common.auth._get_jwks_client", return_value=client):
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(
                    executor.map(
                        lambda _: _get_signing_key_from_jwks(
                            "https://idp.example.com/jwks", "token"
                        ),
                        range(8),
                    )
                )

        assert client.fetches == 1


# ── identity_from_spiffe tests ───────────────────────────────────────────


class TestIdentityFromSpiffe:
    """Tests for SPIFFE JWT-SVID validation via PyJWT + JWKS."""

    def _make_spiffe_jwt(self, make_jwt, sub: str, aud: str):
        """Create a signed JWT with SPIFFE-style claims."""
        claims = {
            "sub": sub,
            "aud": aud,
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
        }
        return make_jwt(claims)

    def test_no_spiffe_config_returns_none(self, client):
        """When [auth.spiffe] is not configured, returns None."""
        auth_mod._auth_config = load_auth_config(ConfigParser())
        resp = client.get("/whoami")
        assert resp.json() == {"identity": None}

    def test_valid_spiffe_jwt(self, rsa_keypair, make_jwt, client):
        """A valid SPIFFE JWT-SVID should return a workload identity with mapped group."""
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://cluster.local",
                },
                "auth.spiffe.groups": {
                    "spiffe://cluster.local/ns/nv-config-manager": "nv-config-manager",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        token = self._make_spiffe_jwt(
            make_jwt,
            sub="spiffe://cluster.local/ns/nv-config-manager/sa/render-service",
            aud="spiffe://cluster.local",
        )
        mock_jwk = MagicMock()
        mock_jwk.key = rsa_keypair.public_key()

        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client
            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        data = resp.json()
        assert data["email"] == "render-service"
        assert data["user"] == "render-service"
        assert data["source"] == "spiffe"
        assert "nv-config-manager" in data["groups"]
        assert "all" in data["groups"]

    def test_invalid_spiffe_jwt(self, client):
        """An invalid SPIFFE JWT should return None."""
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://cluster.local",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.side_effect = Exception("No matching key")
            mock_get_client.return_value = mock_client
            resp = client.get("/whoami", headers={"Authorization": "Bearer bad-token"})

        assert resp.json() == {"identity": None}

    def test_teleport_spiffe_id_format(self, rsa_keypair, make_jwt, client):
        """Teleport-style SPIFFE IDs should be parsed correctly."""
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://teleport-proxy:3080/v1/webapi/jwt/jwks",
                    "audiences": "spiffe://teleport.cluster",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        token = self._make_spiffe_jwt(
            make_jwt,
            sub="spiffe://teleport.cluster/svc/nv-config-manager/render",
            aud="spiffe://teleport.cluster",
        )
        mock_jwk = MagicMock()
        mock_jwk.key = rsa_keypair.public_key()

        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client
            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        data = resp.json()
        assert data["email"] == "svc-nv-config-manager-render"
        assert data["user"] == "svc-nv-config-manager-render"

    def test_group_prefix_mapping(self, rsa_keypair, make_jwt, client):
        """[auth.spiffe.groups] mapping controls the assigned group."""
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://domain",
                },
                "auth.spiffe.groups": {
                    "spiffe://domain/ns/infra": "infra",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        token = self._make_spiffe_jwt(
            make_jwt,
            sub="spiffe://domain/ns/infra/sa/worker",
            aud="spiffe://domain",
        )
        mock_jwk = MagicMock()
        mock_jwk.key = rsa_keypair.public_key()

        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client
            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        data = resp.json()
        assert "infra" in data["groups"]
        assert "all" in data["groups"]


# ── SPIFFE ID parsing tests ──────────────────────────────────────────────


class TestSpiffeIdParsing:
    """Tests for _spiffe_id_to_workload_name."""

    def test_spire_format(self):
        assert (
            _spiffe_id_to_workload_name("spiffe://cluster.local/ns/nv-config-manager/sa/render-api")
            == "render-api"
        )

    def test_teleport_format(self):
        assert (
            _spiffe_id_to_workload_name("spiffe://teleport.cluster/svc/nv-config-manager/render")
            == "svc-nv-config-manager-render"
        )

    def test_simple_path(self):
        assert _spiffe_id_to_workload_name("spiffe://domain/my-service") == "my-service"

    def test_no_scheme(self):
        assert _spiffe_id_to_workload_name("some-raw-id") == "some-raw-id"


# ── extract_identity priority tests ──────────────────────────────────────


class TestExtractIdentityPriority:
    """Tests that extract_identity tries methods in the correct order."""

    def test_spiffe_takes_priority_over_headers(self, rsa_keypair, make_jwt, client):
        """SPIFFE identity should take priority over SSO headers."""
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://domain",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        token = make_jwt(
            {
                "sub": "spiffe://domain/ns/nv-config-manager/sa/caller",
                "aud": "spiffe://domain",
                "exp": int(time.time()) + 300,
                "iat": int(time.time()),
            }
        )
        mock_jwk = MagicMock()
        mock_jwk.key = rsa_keypair.public_key()

        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client
            resp = client.get(
                "/whoami",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Auth-Request-Email": "should-be-ignored",
                },
            )

        data = resp.json()
        assert data["source"] == "spiffe"
        assert data["email"] == "caller"

    def test_sso_headers_used_as_fallback(self, client):
        """SSO headers should work when no JWT or SPIFFE is configured."""
        auth_mod._auth_config = load_auth_config(ConfigParser())
        resp = client.get(
            "/whoami",
            headers={
                "X-Auth-Request-Email": "user@example.com",
                "X-Auth-Request-Groups": "admin,viewer",
            },
        )

        data = resp.json()
        assert data["source"] == "sso"
        assert data["email"] == "user@example.com"
        assert data["user"] == "user"
        assert "admin" in data["groups"]
        assert "viewer" in data["groups"]


# ── Backward compatibility tests ─────────────────────────────────────────


class TestBackwardCompatibility:
    """Ensure existing SSO header-based auth still works."""

    def test_identity_from_sso_headers_unchanged(self):
        """identity_from_sso_headers should still work as before."""
        app = FastAPI()

        @app.get("/test")
        async def handler(request: Request):
            identity = identity_from_sso_headers(request)
            if identity:
                return {"email": identity.email, "source": identity.source}
            return {"identity": None}

        client = StarletteClient(app)
        resp = client.get("/test", headers={"X-Auth-Request-Email": "test@corp.com"})
        assert resp.json()["email"] == "test@corp.com"
        assert resp.json()["source"] == "sso"

    def test_require_authenticated_identity_with_auth_disabled(self):
        """require_authenticated_identity returns anonymous when auth is disabled."""
        cp = _make_config(auth={"required": "false"})
        auth_mod._auth_config = load_auth_config(cp)

        app = FastAPI()

        @app.get("/test")
        async def handler(
            identity: SSOIdentity = pytest.importorskip("fastapi").Depends(
                require_authenticated_identity
            ),
        ):
            return {"email": identity.email, "source": identity.source}

        client = TestClient(app)
        resp = client.get("/test")
        assert resp.json()["source"] == "anonymous"


# ── Per-service allowed_groups tests ─────────────────────────────────────


class TestAllowedGroups:
    """Tests for per-service allowed_groups enforcement via NV_CONFIG_MANAGER_SERVICE env var."""

    def _make_app(self):
        app = FastAPI()

        @app.get("/protected")
        async def protected(identity: SSOIdentity = Depends(require_authenticated_identity)):
            return {"email": identity.email, "groups": sorted(identity.groups)}

        return app

    def test_no_service_section_no_restriction(self):
        """Without NV_CONFIG_MANAGER_SERVICE env var, allowed_groups is empty — no restriction."""
        cp = _make_config(auth={"required": "true"})
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("NV_CONFIG_MANAGER_SERVICE", None)
            auth_mod._auth_config = load_auth_config(cp)

        assert auth_mod._auth_config.allowed_groups == ()

    def test_allowed_groups_parsed_from_service_section(self):
        """allowed_groups is read from the service's own INI section."""
        cp = _make_config(
            auth={"required": "true"},
            render={"allowed_groups": "group-a, group-b"},
        )
        with patch.dict("os.environ", {"NV_CONFIG_MANAGER_SERVICE": "render"}):
            auth_mod._auth_config = None
            cfg = load_auth_config(cp)

        assert cfg.allowed_groups == ("group-a", "group-b")

    def test_user_in_allowed_group_passes(self):
        """A user whose groups overlap with allowed_groups is permitted."""
        cp = _make_config(
            auth={"required": "true", "accept_request_headers": "true"},
            render={"allowed_groups": "eng-team"},
        )
        with patch.dict("os.environ", {"NV_CONFIG_MANAGER_SERVICE": "render"}):
            auth_mod._auth_config = None
            auth_mod._auth_config = load_auth_config(cp)

        app = self._make_app()
        client = TestClient(app)
        resp = client.get(
            "/protected",
            headers={
                "X-Auth-Request-Email": "alice@example.com",
                "X-Auth-Request-Groups": "eng-team,all-users",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "alice@example.com"

    def test_user_not_in_allowed_group_denied(self):
        """A user not in any allowed group gets 403."""
        cp = _make_config(
            auth={"required": "true", "accept_request_headers": "true"},
            render={"allowed_groups": "eng-team"},
        )
        with patch.dict("os.environ", {"NV_CONFIG_MANAGER_SERVICE": "render"}):
            auth_mod._auth_config = None
            auth_mod._auth_config = load_auth_config(cp)

        app = self._make_app()
        client = TestClient(app)
        resp = client.get(
            "/protected",
            headers={
                "X-Auth-Request-Email": "outsider@example.com",
                "X-Auth-Request-Groups": "other-team",
            },
        )
        assert resp.status_code == 403

    def test_spiffe_identity_bypasses_allowed_groups(self, rsa_keypair, make_jwt):
        """SPIFFE identities (source=spiffe) are not subject to allowed_groups."""
        cp = _make_config(
            **{
                "auth": {"required": "true"},
                "render": {"allowed_groups": "eng-team"},
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://cluster.local",
                },
            }
        )
        with patch.dict("os.environ", {"NV_CONFIG_MANAGER_SERVICE": "render"}):
            auth_mod._auth_config = None
            auth_mod._auth_config = load_auth_config(cp)

        token = make_jwt(
            {
                "sub": "spiffe://cluster.local/ns/nv-config-manager/sa/worker",
                "aud": "spiffe://cluster.local",
                "exp": int(time.time()) + 300,
                "iat": int(time.time()),
            }
        )
        mock_jwk = MagicMock()
        mock_jwk.key = rsa_keypair.public_key()

        app = self._make_app()
        client = TestClient(app)
        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client
            resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 200

    def test_empty_allowed_groups_no_restriction(self):
        """An empty allowed_groups in the service section means no restriction."""
        cp = _make_config(
            auth={"required": "true"},
            render={"api_service": "http://render:9000"},
        )
        with patch.dict("os.environ", {"NV_CONFIG_MANAGER_SERVICE": "render"}):
            auth_mod._auth_config = None
            cfg = load_auth_config(cp)

        assert cfg.allowed_groups == ()


# ── SPIFFE group prefix mapping tests ────────────────────────────────────


class TestSpiffeGroupPrefixes:
    """Tests for [auth.spiffe.groups] prefix-to-group mapping."""

    def _make_app(self):
        app = FastAPI()

        @app.get("/whoami")
        async def whoami(request: Request):
            identity = extract_identity(request)
            if identity:
                return {
                    "user": identity.user,
                    "groups": sorted(identity.groups),
                    "source": identity.source,
                }
            return {"identity": None}

        return app

    def _mock_spiffe_request(self, rsa_keypair, make_jwt, sub, aud):
        """Create a signed SPIFFE JWT and matching JWKS mock."""
        token = make_jwt(
            {
                "sub": sub,
                "aud": aud,
                "exp": int(time.time()) + 300,
                "iat": int(time.time()),
            }
        )
        mock_jwk = MagicMock()
        mock_jwk.key = rsa_keypair.public_key()
        return token, mock_jwk

    def test_no_mapping_only_all_group(self, rsa_keypair, make_jwt):
        """Without [auth.spiffe.groups], callers only get the 'all' group."""
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://cluster.local",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        token, mock_jwk = self._mock_spiffe_request(
            rsa_keypair,
            make_jwt,
            sub="spiffe://cluster.local/ns/nv-config-manager/sa/render-api",
            aud="spiffe://cluster.local",
        )

        app = self._make_app()
        client = TestClient(app)
        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client
            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        data = resp.json()
        assert data["user"] == "render-api"
        assert data["groups"] == ["all"]

    def test_matching_prefix_adds_mapped_group(self, rsa_keypair, make_jwt):
        """A SPIFFE ID matching a prefix gets the mapped group."""
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://cluster.local",
                },
                "auth.spiffe.groups": {
                    "spiffe://cluster.local/ns/nv-config-manager": "nv-config-manager",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        token, mock_jwk = self._mock_spiffe_request(
            rsa_keypair,
            make_jwt,
            sub="spiffe://cluster.local/ns/nv-config-manager/sa/render-api",
            aud="spiffe://cluster.local",
        )

        app = self._make_app()
        client = TestClient(app)
        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client
            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        data = resp.json()
        assert data["user"] == "render-api"
        assert "nv-config-manager" in data["groups"]
        assert "all" in data["groups"]

    def test_nonmatching_prefix_no_mapped_group(self, rsa_keypair, make_jwt):
        """A SPIFFE ID not matching any prefix does not get the mapped group."""
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://cluster.local",
                },
                "auth.spiffe.groups": {
                    "spiffe://cluster.local/ns/nv-config-manager": "nv-config-manager",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        token, mock_jwk = self._mock_spiffe_request(
            rsa_keypair,
            make_jwt,
            sub="spiffe://external.domain/svc/other-app",
            aud="spiffe://cluster.local",
        )

        app = self._make_app()
        client = TestClient(app)
        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client
            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        data = resp.json()
        assert data["user"] == "svc-other-app"
        assert "nv-config-manager" not in data["groups"]
        assert data["groups"] == ["all"]

    def test_multiple_prefixes_multiple_groups(self, rsa_keypair, make_jwt):
        """Multiple prefix mappings can coexist; matching ones all apply."""
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://cluster.local",
                },
                "auth.spiffe.groups": {
                    "spiffe://cluster.local/ns/nv-config-manager": "nv-config-manager",
                    "spiffe://cluster.local/ns/dgxc": "dgxc",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        token, mock_jwk = self._mock_spiffe_request(
            rsa_keypair,
            make_jwt,
            sub="spiffe://cluster.local/ns/dgxc/sa/workflow-runner",
            aud="spiffe://cluster.local",
        )

        app = self._make_app()
        client = TestClient(app)
        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client
            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        data = resp.json()
        assert data["user"] == "workflow-runner"
        assert "dgxc" in data["groups"]
        assert "nv-config-manager" not in data["groups"]

    def test_sibling_identity_not_granted_role(self, rsa_keypair, make_jwt):
        """A sibling identity sharing a common prefix must NOT inherit the role.

        Regression test for the prefix-matching footgun:
        ``spiffe://cluster.local/ns/nv-config-manager`` configured as the
        prefix for the ``nv-config-manager`` role must not match a sibling
        SPIFFE ID like ``spiffe://cluster.local/ns/nv-config-manager-admin``.
        Path matching is on segment boundaries, so ``nv-config-manager-admin``
        falls outside the ``ns/nv-config-manager`` path and gets only the
        baseline ``all`` group.
        """
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://cluster.local",
                },
                "auth.spiffe.groups": {
                    "spiffe://cluster.local/ns/nv-config-manager": "nv-config-manager",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        token, mock_jwk = self._mock_spiffe_request(
            rsa_keypair,
            make_jwt,
            sub="spiffe://cluster.local/ns/nv-config-manager-admin/sa/attacker",
            aud="spiffe://cluster.local",
        )

        app = self._make_app()
        client = TestClient(app)
        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client
            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        data = resp.json()
        assert "nv-config-manager" not in data["groups"], (
            f"sibling identity must not inherit the 'nv-config-manager' role; "
            f"got groups={data['groups']!r}"
        )
        assert data["groups"] == ["all"]

    def test_exact_match_grants_role(self, rsa_keypair, make_jwt):
        """An SVID equal to the prefix (no trailing path) still gets the role."""
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://cluster.local",
                },
                "auth.spiffe.groups": {
                    "spiffe://cluster.local/ns/nv-config-manager": "nv-config-manager",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        token, mock_jwk = self._mock_spiffe_request(
            rsa_keypair,
            make_jwt,
            sub="spiffe://cluster.local/ns/nv-config-manager",
            aud="spiffe://cluster.local",
        )

        app = self._make_app()
        client = TestClient(app)
        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client
            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        data = resp.json()
        assert "nv-config-manager" in data["groups"]
        assert "all" in data["groups"]

    def test_layered_prefixes_add_both_roles(self, rsa_keypair, make_jwt):
        """Per-service narrow prefix layers on top of a coarser one.

        A SPIFFE ID *below* ``…/nv-config-manager/render`` must pick up BOTH
        the broad ``nv-config-manager`` role and the narrow
        ``nv-config-manager-render`` role -- both matched via the
        ``prefix + "/"`` path-segment boundary (a descendant SVID, not an
        exact prefix match). This is what enables progressive scoping: keep
        coarse roles working while introducing tighter per-service roles to
        gate sensitive endpoints.
        """
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://cluster.local",
                },
                "auth.spiffe.groups": {
                    "spiffe://cluster.local/ns/nv-config-manager": "nv-config-manager",
                    "spiffe://cluster.local/ns/nv-config-manager/render": "nv-config-manager-render",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        # SVID sits a segment below the narrow prefix so that BOTH mapped
        # prefixes are hit via the descendant (``prefix + "/"``) branch, not an
        # exact match -- proving layered roles still accumulate for sub-workload
        # identities.
        token, mock_jwk = self._mock_spiffe_request(
            rsa_keypair,
            make_jwt,
            sub="spiffe://cluster.local/ns/nv-config-manager/render/sa/api",
            aud="spiffe://cluster.local",
        )

        app = self._make_app()
        client = TestClient(app)
        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client
            resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        data = resp.json()
        assert "nv-config-manager" in data["groups"]
        assert "nv-config-manager-render" in data["groups"]

    def test_group_prefixes_parsed(self):
        """[auth.spiffe.groups] section is parsed into group_prefixes."""
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://domain",
                },
                "auth.spiffe.groups": {
                    "spiffe://domain/ns/nv-config-manager": "nv-config-manager",
                    "spiffe://domain/ns/dgxc": "dgxc",
                },
            }
        )
        cfg = load_auth_config(cp)
        prefixes = dict(cfg.spiffe.group_prefixes)
        assert prefixes["spiffe://domain/ns/nv-config-manager"] == "nv-config-manager"
        assert prefixes["spiffe://domain/ns/dgxc"] == "dgxc"


# ── require_group dependency tests ───────────────────────────────────────


class TestRequireGroup:
    """Tests for the require_group() FastAPI dependency factory."""

    def _make_app_with_group(self, *groups):
        app = FastAPI()

        @app.get("/gated")
        async def gated(identity: SSOIdentity = Depends(require_group(*groups))):
            return {"email": identity.email, "groups": sorted(identity.groups)}

        return app

    def test_user_with_matching_group_passes(self):
        """A user in a required group passes the gate."""
        auth_mod._auth_config = load_auth_config(
            _make_config(auth={"accept_request_headers": "true"})
        )
        app = self._make_app_with_group("nv-config-manager")
        client = TestClient(app)
        resp = client.get(
            "/gated",
            headers={
                "X-Auth-Request-Email": "alice@example.com",
                "X-Auth-Request-Groups": "nv-config-manager,eng",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "alice@example.com"

    def test_user_without_required_group_denied(self):
        """A user missing all required groups gets 403."""
        auth_mod._auth_config = load_auth_config(
            _make_config(auth={"accept_request_headers": "true"})
        )
        app = self._make_app_with_group("nv-config-manager")
        client = TestClient(app)
        resp = client.get(
            "/gated",
            headers={
                "X-Auth-Request-Email": "bob@example.com",
                "X-Auth-Request-Groups": "other-team",
            },
        )
        assert resp.status_code == 403

    def test_any_of_multiple_groups_passes(self):
        """Having any one of the specified groups is sufficient."""
        auth_mod._auth_config = load_auth_config(
            _make_config(auth={"accept_request_headers": "true"})
        )
        app = self._make_app_with_group("nv-config-manager", "admin")
        client = TestClient(app)
        resp = client.get(
            "/gated",
            headers={
                "X-Auth-Request-Email": "carol@example.com",
                "X-Auth-Request-Groups": "admin",
            },
        )
        assert resp.status_code == 200

    def test_spiffe_non_allowed_denied_by_require_group_config_manager(self, rsa_keypair, make_jwt):
        """A SPIFFE workload not matching any prefix is denied by require_group('nv-config-manager')."""
        cp = _make_config(
            **{
                "auth.spiffe": {
                    "jwks_uri": "https://spire-server:8443/keys",
                    "audiences": "spiffe://cluster.local",
                },
                "auth.spiffe.groups": {
                    "spiffe://cluster.local/ns/nv-config-manager": "nv-config-manager",
                },
            }
        )
        auth_mod._auth_config = load_auth_config(cp)

        token = make_jwt(
            {
                "sub": "spiffe://external.domain/svc/other",
                "aud": "spiffe://cluster.local",
                "exp": int(time.time()) + 300,
                "iat": int(time.time()),
            }
        )
        mock_jwk = MagicMock()
        mock_jwk.key = rsa_keypair.public_key()

        app = self._make_app_with_group("nv-config-manager")
        client = TestClient(app)
        with patch("nv_config_manager.common.auth._get_jwks_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = mock_jwk
            mock_get_client.return_value = mock_client
            resp = client.get("/gated", headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 403
