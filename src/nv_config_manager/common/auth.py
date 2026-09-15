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
"""Server-side authentication and identity extraction for NVIDIA Config Manager services.

All configuration is read from ``nv-config-manager.ini`` (via :func:`nv_config_manager.common.config.load_config`).

INI sections::

    [auth]
    required = true
    accept_request_headers = false
    cookie_name = NVConfigManagerAccessToken

    [auth.jwt.azure-ad]
    issuer = https://login.microsoftonline.com/{tenant}/v2.0
    audiences = api://{client-id}
    # jwks_uri auto-derived for Azure AD when omitted
    claim_email = email
    claim_user = preferred_username
    claim_groups = roles

    [auth.jwt.service]
    issuer = https://service-idp.example.com
    audiences = service-api
    jwks_uri = https://service-idp.example.com/.well-known/jwks.json
    claim_email = sub
    claim_user = sub
    claim_groups = scopes

    [auth.spiffe]
    jwks_uri = https://spire-server.spire:8443/keys
    audiences = spiffe://trust-domain

    ; Map SPIFFE ID prefixes to group names.  Matching callers get the
    ; mapped group.
    [auth.spiffe.groups]
    spiffe://trust-domain/ns/nv-config-manager = nv-config-manager
    spiffe://trust-domain/ns/dgxc = dgxc

    ; Per-service section (e.g. [render], [dhcp], [temporal.api]):
    [render]
    allowed_groups = group-uuid-1, group-uuid-2

Authentication methods (checked in order):

1. **mTLS** -- ``ssl-client-cert`` header
2. **SPIFFE JWT-SVID** -- validated via PyJWT + JWKS
3. **OIDC / JWT** -- validated against JWKS (multi-issuer)
4. **Proxy headers** -- ``X-Auth-Request-*`` from the gateway

Usage (FastAPI dependency -- require auth)::

    from nv_config_manager.common.auth import require_authenticated_identity

    @router.post("/upload")
    async def upload(identity: SSOIdentity = Depends(require_authenticated_identity)):
        print(identity.email, identity.user, identity.groups)

Usage (FastAPI dependency -- optional identity)::

    from nv_config_manager.common.auth import get_sso_identity

    @router.get("/status")
    async def status(identity: SSOIdentity | None = Depends(get_sso_identity)):
        user = identity.user if identity else "anonymous"

Usage (extract user for audit trails)::

    from nv_config_manager.common.auth import get_sso_user

    user = get_sso_user(request)  # "jdoe" or "unknown"
"""

from __future__ import annotations

import base64
import json
import os
import threading
import urllib.parse
from collections.abc import Awaitable, Callable
from configparser import ConfigParser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jwt as pyjwt
from cryptography import x509
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from jwt.types import Options as JWTDecodeOptions
from pydantic import BaseModel

from nv_config_manager.common.config import load_config
from nv_config_manager.common.log import LogCategory, get_logger

logger = get_logger(__name__, category=LogCategory.AUTH)

# ── Header names ──────────────────────────────────────────────────────────

SSO_EMAIL_HEADER = "X-Auth-Request-Email"
SSO_USER_HEADER = "X-Auth-Request-User"
SSO_GROUPS_HEADER = "X-Auth-Request-Groups"
MTLS_CERT_HEADER = "ssl-client-cert"

DEFAULT_UNAUTHENTICATED_PATHS = frozenset(
    {
        "/health",
        "/healthz",
        "/ready",
        "/readyz",
        "/livez",
        "/ping",
        "/healthcheck",
        "/metrics",
        "/v1/codec/decode",
        "/v1/codec/encode",
    }
)

OPENAPI_BEARER_SCHEME_NAME = "BearerAuth"
OPENAPI_BEARER_DESCRIPTION = (
    "Bearer JWT used by CLI and machine clients on svc-* endpoints. Authentication is "
    "required by default, but deployments may disable it with [auth] required = false. "
    "Browser OIDC sessions, mTLS, SPIFFE identities, and trusted gateway identity headers "
    "may also satisfy authentication outside this generated client flow."
)


# ── Configuration dataclasses ────────────────────────────────────────────


@dataclass(frozen=True)
class JwtProviderConfig:
    """Configuration for a single JWT issuer / OIDC provider."""

    name: str
    issuer: str
    audiences: list[str]
    jwks_uri: str
    claim_email: str = "email"
    claim_user: str = "preferred_username"
    claim_groups: str = "roles"


@dataclass(frozen=True)
class SpiffeConfig:
    """Configuration for SPIFFE JWT-SVID validation via PyJWT + JWKS.

    ``group_prefixes`` maps SPIFFE ID *path-segment* prefixes to group names.
    When a caller's SPIFFE ID matches a prefix the mapped group is added to
    its identity.

    Match semantics: the prefix matches a SPIFFE ID iff either
    (a) the SPIFFE ID equals the prefix exactly, or
    (b) the SPIFFE ID starts with the prefix followed by ``/``.

    This is the standard hierarchical-path matching used elsewhere in SPIFFE
    tooling. It deliberately does **not** match across path-segment boundaries
    so that a sibling identity such as
    ``spiffe://td/ns/nv-config-manager-admin`` is not accidentally granted the
    role mapped to the ``spiffe://td/ns/nv-config-manager`` prefix. Use a
    longer/exact prefix to scope a role to a single identity.

    Configured via ``[auth.spiffe.groups]``::

        [auth.spiffe.groups]
        spiffe://trust-domain/ns/nv-config-manager = nv-config-manager
        spiffe://trust-domain/ns/dgxc = dgxc
    """

    jwks_uri: str
    audiences: list[str]
    group_prefixes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class AuthConfig:
    """Top-level auth configuration loaded from ``[auth]`` INI sections."""

    required: bool = True
    accept_request_headers: bool = False
    cookie_name: str = "NVConfigManagerAccessToken"
    jwt_providers: tuple[JwtProviderConfig, ...] = ()
    spiffe: SpiffeConfig | None = None
    allowed_groups: tuple[str, ...] = ()


# ── Identity container ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SSOIdentity:
    """Parsed identity from any authentication source.

    Attributes:
        email: Identifier (email, SPIFFE ID path, or service name).
        user: Short username derived from email or identity.
        groups: Set of group/role strings.  Always includes ``"all"``.
        source: How the identity was established
                (``"jwt"``, ``"spiffe"``, ``"sso"``, ``"mtls"``,
                or ``"anonymous"``).
    """

    email: str
    user: str
    groups: set[str] = field(default_factory=lambda: {"all"})
    source: str = "sso"


ANONYMOUS_IDENTITY = SSOIdentity(
    email="anonymous",
    user="anonymous",
    groups={"all"},
    source="anonymous",
)


# ── Configuration loading ────────────────────────────────────────────────

_auth_config: AuthConfig | None = None
_auth_config_lock = threading.Lock()
_auth_config_source: ConfigParser | None = None
_auth_config_tracks_file = False

_JWT_SECTION_PREFIX = "auth.jwt."


def _derive_jwks_uri(issuer: str) -> str:
    """Auto-derive the JWKS URI from an issuer URL."""
    base = issuer.rstrip("/")
    if "login.microsoftonline.com" in base:
        return f"{base}/discovery/v2.0/keys"
    return f"{base}/protocol/openid-connect/certs"


def _load_jwt_providers_from_ini(config: ConfigParser) -> list[JwtProviderConfig]:
    """Parse all ``[auth.jwt.<name>]`` sections into provider configs."""
    providers: list[JwtProviderConfig] = []
    for section in config.sections():
        if not section.startswith(_JWT_SECTION_PREFIX):
            continue
        name = section[len(_JWT_SECTION_PREFIX) :]
        issuer = config.get(section, "issuer", fallback="")
        if not issuer:
            continue
        audiences = [
            a.strip() for a in config.get(section, "audiences", fallback="").split(",") if a.strip()
        ]
        jwks_uri = config.get(section, "jwks_uri", fallback="") or _derive_jwks_uri(issuer)
        providers.append(
            JwtProviderConfig(
                name=name,
                issuer=issuer,
                audiences=audiences,
                jwks_uri=jwks_uri,
                claim_email=config.get(section, "claim_email", fallback="email"),
                claim_user=config.get(section, "claim_user", fallback="preferred_username"),
                claim_groups=config.get(section, "claim_groups", fallback="roles"),
            )
        )
    return providers


def _load_spiffe_from_ini(config: ConfigParser) -> SpiffeConfig | None:
    """Parse ``[auth.spiffe]`` and ``[auth.spiffe.groups]``."""
    section = "auth.spiffe"
    if not config.has_section(section):
        return None
    jwks_uri = config.get(section, "jwks_uri", fallback="")
    audiences_raw = config.get(section, "audiences", fallback="")
    if not jwks_uri or not audiences_raw:
        return None

    audiences = [a.strip() for a in audiences_raw.split(",") if a.strip()]

    group_prefixes: list[tuple[str, str]] = []
    groups_section = "auth.spiffe.groups"
    if config.has_section(groups_section):
        for prefix, group in config.items(groups_section):
            group_prefixes.append((prefix, group.strip()))

    return SpiffeConfig(
        jwks_uri=jwks_uri,
        audiences=audiences,
        group_prefixes=tuple(group_prefixes),
    )


def load_auth_config(config: ConfigParser | None = None) -> AuthConfig:
    """Load auth configuration from ``nv-config-manager.ini``.

    File-backed configuration is rebuilt automatically when ``load_config``
    observes a new INI version. Explicitly supplied configuration remains
    pinned until :func:`reload_auth_config` is called.
    """
    global _auth_config, _auth_config_source, _auth_config_tracks_file
    tracks_file = config is None

    if config is None:
        if _auth_config is not None and not _auth_config_tracks_file:
            return _auth_config
        try:
            config = load_config()
        except Exception:
            config = ConfigParser()
    elif _auth_config is not None:
        return _auth_config

    if _auth_config is not None and _auth_config_source is config:
        return _auth_config

    with _auth_config_lock:
        if _auth_config is not None and (not tracks_file or _auth_config_source is config):
            return _auth_config

        auth_section = "auth"

        # allowed_groups is per-service: read from the service's own INI
        # section (identified by NV_CONFIG_MANAGER_SERVICE env var, e.g. "render",
        # "dhcp", "temporal.api").
        service_section = os.getenv("NV_CONFIG_MANAGER_SERVICE", "")
        raw_groups = ""
        if service_section:
            raw_groups = config.get(service_section, "allowed_groups", fallback="")
        allowed_groups = tuple(g.strip() for g in raw_groups.split(",") if g.strip())

        _auth_config = AuthConfig(
            required=config.getboolean(auth_section, "required", fallback=True),
            accept_request_headers=config.getboolean(
                auth_section,
                "accept_request_headers",
                fallback=False,
            ),
            cookie_name=config.get(
                auth_section, "cookie_name", fallback="NVConfigManagerAccessToken"
            ),
            jwt_providers=tuple(_load_jwt_providers_from_ini(config)),
            spiffe=_load_spiffe_from_ini(config),
            allowed_groups=allowed_groups,
        )
        _auth_config_source = config
        _auth_config_tracks_file = tracks_file
        return _auth_config


def reload_auth_config() -> AuthConfig:
    """Force-reload the auth configuration (clears cache)."""
    global _auth_config, _auth_config_source, _auth_config_tracks_file
    _auth_config = None
    _auth_config_source = None
    _auth_config_tracks_file = False
    return load_auth_config()


# ── Internal helpers ──────────────────────────────────────────────────────


def _strip_email_domain(email: str) -> str:
    """Return the local part of an email address, or the value unchanged."""
    if "@" in email:
        return email.split("@", 1)[0]
    return email


def _parse_groups_header(value: str | None) -> set[str]:
    """Parse a comma-separated groups header into a set, always including 'all'."""
    groups: set[str] = {"all"}
    if value:
        groups.update(g.strip() for g in value.split(",") if g.strip())
    return groups


def _extract_bearer_token(request: Request) -> str | None:
    """Extract a Bearer token from the Authorization header or cookie."""
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    cfg = load_auth_config()
    return request.cookies.get(cfg.cookie_name)


def _unverified_jwt_claims(token: str) -> dict[str, Any] | None:
    """Return unverified claims used only to select a configured validator.

    The returned claims must never grant access.  Signature, issuer, audience,
    and expiry validation still happens in the selected provider below.  Reading
    the issuer first prevents a token from causing PyJWT to refresh every
    unrelated provider's JWKS key set when its ``kid`` is absent there.
    """
    try:
        claims = pyjwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": False,
                "verify_iat": False,
                "verify_iss": False,
                "verify_nbf": False,
            },
        )
    except pyjwt.exceptions.PyJWTError:
        return None
    return claims if isinstance(claims, dict) else None


def _unverified_jwt_issuer(token: str) -> str | None:
    """Return a token issuer for provider selection, without trusting it."""
    claims = _unverified_jwt_claims(token)
    issuer = claims.get("iss") if claims else None
    return issuer if isinstance(issuer, str) and issuer else None


def _is_spiffe_jwt(token: str) -> bool:
    """Return whether an unverified token has the required SPIFFE subject shape."""
    claims = _unverified_jwt_claims(token)
    subject = claims.get("sub") if claims else None
    return isinstance(subject, str) and subject.startswith("spiffe://")


# ── OIDC / JWT validation (multi-issuer) ─────────────────────────────────

_jwks_clients: dict[str, pyjwt.PyJWKClient] = {}
_jwks_lock = threading.Lock()
_jwks_signing_key_locks: dict[str, threading.Lock] = {}

# PyJWT owns the cached response and refreshes immediately for a previously
# unseen key ID, so key rotation does not wait for this normal cache lifetime.
# A per-URI lock prevents a cold or expired cache from stampeding a rate-limited
# issuer before its first response repopulates the shared key-set cache.
JWKS_KEYSET_CACHE_LIFESPAN_SECONDS = 600

_JWT_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]


def _get_jwks_client(jwks_uri: str) -> pyjwt.PyJWKClient:
    """Return a cached PyJWKClient for the given JWKS URI."""
    client = _jwks_clients.get(jwks_uri)
    if client is not None:
        return client
    with _jwks_lock:
        client = _jwks_clients.get(jwks_uri)
        if client is not None:
            return client
        client = pyjwt.PyJWKClient(
            jwks_uri,
            cache_jwk_set=True,
            lifespan=JWKS_KEYSET_CACHE_LIFESPAN_SECONDS,
        )
        _jwks_clients[jwks_uri] = client
        return client


def _get_jwks_signing_key_lock(jwks_uri: str) -> threading.Lock:
    """Return the lock that serializes signing-key resolution for one URI."""
    with _jwks_lock:
        lock = _jwks_signing_key_locks.get(jwks_uri)
        if lock is None:
            lock = threading.Lock()
            _jwks_signing_key_locks[jwks_uri] = lock
        return lock


def _try_jwt_provider(
    token: str,
    provider: JwtProviderConfig,
) -> SSOIdentity | None:
    """Attempt to validate *token* against a single JWT provider."""
    try:
        signing_key = _get_signing_key_from_jwks(provider.jwks_uri, token)

        options = JWTDecodeOptions()
        if not provider.audiences:
            options["verify_aud"] = False

        claims = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=_JWT_ALGORITHMS,
            issuer=provider.issuer,
            audience=provider.audiences or None,
            options=options,
        )

        email = claims.get(provider.claim_email, "")
        user_claim = claims.get(provider.claim_user, "")
        user = _strip_email_domain(user_claim or email) or "unknown"

        groups_val = claims.get(provider.claim_groups)
        groups: set[str] = {"all"}
        if isinstance(groups_val, list):
            groups.update(str(g) for g in groups_val if g)
        elif isinstance(groups_val, str) and groups_val:
            groups.update(g.strip() for g in groups_val.split(",") if g.strip())

        return SSOIdentity(email=email or user, user=user, groups=groups, source="jwt")
    except pyjwt.exceptions.PyJWTError:
        logger.debug("JWT validation failed for provider %s", provider.name, exc_info=True)
        return None
    except Exception:
        logger.warning(
            "Unexpected error validating JWT with provider %s",
            provider.name,
            exc_info=True,
        )
        return None


def identity_from_jwt(request: Request) -> SSOIdentity | None:
    """Validate JWT from Authorization header or cookie against all providers.

    The unverified issuer selects a configured ``[auth.jwt.*]`` provider;
    that provider then verifies the token and returns the identity.  Returns
    ``None`` when no provider is configured, no token is present, or no
    provider accepts the token.
    """
    cfg = load_auth_config()
    if not cfg.jwt_providers:
        return None

    token = _extract_bearer_token(request)
    if not token:
        return None

    issuer = _unverified_jwt_issuer(token)
    if issuer is None:
        return None

    for provider in cfg.jwt_providers:
        if provider.issuer != issuer:
            continue
        identity = _try_jwt_provider(token, provider)
        if identity is not None:
            return identity
    return None


# ── SPIFFE JWT-SVID validation ────────────────────────────────────────────


def _get_signing_key_from_jwks(jwks_uri: str, token: str) -> pyjwt.PyJWK:
    """Get the signing key for *token* from an HTTP JWKS endpoint or local file.

    File-based JWKS (from spiffe-helper ``jwt_bundle_file_name``) is
    re-read on each call so key rotations are picked up immediately.
    """
    if jwks_uri.startswith(("http://", "https://")):
        client = _get_jwks_client(jwks_uri)
        with _get_jwks_signing_key_lock(jwks_uri):
            return client.get_signing_key_from_jwt(token)

    bundle_path = Path(jwks_uri)
    jwks_data = json.loads(bundle_path.read_text())

    # spiffe-helper writes trust bundles as {domain: base64(JWKS), ...}
    # rather than raw JWKS {"keys": [...]}.  Decode and merge all domains.
    if "keys" not in jwks_data:
        merged_keys: list[dict[str, Any]] = []
        for _domain, encoded_jwks in jwks_data.items():
            domain_jwks = json.loads(base64.b64decode(encoded_jwks))
            merged_keys.extend(domain_jwks.get("keys", []))
        jwks_data = {"keys": merged_keys}

    jwk_set = pyjwt.PyJWKSet.from_dict(jwks_data)
    header = pyjwt.get_unverified_header(token)
    kid = header.get("kid")
    for key in jwk_set.keys:
        if kid and key.key_id == kid:
            return key
    if jwk_set.keys:
        return jwk_set.keys[0]
    raise pyjwt.exceptions.PyJWKClientError("No matching key in JWKS bundle")


def _spiffe_id_to_workload_name(spiffe_id: str) -> str:
    """Extract a workload name from a SPIFFE ID.

    Handles both SPIRE format (``spiffe://domain/ns/<ns>/sa/<sa>``)
    and Teleport format (``spiffe://domain/<path>/<name>``).
    """
    path = spiffe_id
    if path.startswith("spiffe://"):
        path = path[len("spiffe://") :]
        slash = path.find("/")
        path = path[slash + 1 :] if slash >= 0 else path

    if "/sa/" in path:
        return path.rsplit("/sa/", 1)[-1]

    return path.replace("/", "-") if path else spiffe_id


def identity_from_spiffe(request: Request) -> SSOIdentity | None:
    """Validate a SPIFFE JWT-SVID from the Authorization header.

    Validates the token directly via PyJWT + JWKS.  The SPIFFE ID is
    extracted from the ``sub`` claim.
    Returns ``None`` if SPIFFE is not configured, no token, or invalid.
    """
    cfg = load_auth_config()
    if cfg.spiffe is None:
        return None

    token = _extract_bearer_token(request)
    if not token:
        return None
    if not _is_spiffe_jwt(token):
        return None

    try:
        signing_key = _get_signing_key_from_jwks(cfg.spiffe.jwks_uri, token)

        claims = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=_JWT_ALGORITHMS,
            audience=cfg.spiffe.audiences,
            options={"verify_iss": False},
        )

        spiffe_id = claims.get("sub", "")
        if not spiffe_id:
            return None

        workload_name = _spiffe_id_to_workload_name(spiffe_id)

        groups: set[str] = {"all"}

        # Match prefixes on path-segment boundaries: either the SPIFFE ID is
        # exactly the prefix, or the prefix is followed by '/'. A naive
        # ``startswith`` would also match sibling identities — e.g. the
        # configured prefix ``spiffe://td/ns/nv-config-manager`` would
        # otherwise match ``spiffe://td/ns/nv-config-manager-admin`` and
        # silently grant it the ``nv-config-manager`` role. SPIFFE IDs are
        # hierarchical paths, so the boundary must be a ``/`` (or
        # end-of-string).
        for prefix, group in cfg.spiffe.group_prefixes:
            if spiffe_id == prefix or spiffe_id.startswith(prefix + "/"):
                groups.add(group)

        return SSOIdentity(
            email=workload_name,
            user=workload_name,
            groups=groups,
            source="spiffe",
        )
    except Exception:
        logger.debug("SPIFFE JWT-SVID validation failed", exc_info=True)
        return None


# ── Proxy header extraction (gateway-injected) ───────────────────────────


def identity_from_sso_headers(request: Request) -> SSOIdentity | None:
    """Extract an ``SSOIdentity`` from gateway-injected SSO headers.

    Returns ``None`` if no ``X-Auth-Request-Email`` header is present.
    """
    email = request.headers.get(SSO_EMAIL_HEADER)
    if not email:
        return None

    user_header = request.headers.get(SSO_USER_HEADER)
    user = _strip_email_domain(user_header or email)

    groups = _parse_groups_header(request.headers.get(SSO_GROUPS_HEADER))

    return SSOIdentity(email=email, user=user, groups=groups, source="sso")


# ── mTLS extraction ──────────────────────────────────────────────────────


def identity_from_mtls(request: Request) -> SSOIdentity | None:
    """Extract an ``SSOIdentity`` from the ``ssl-client-cert`` header.

    Returns ``None`` if the header is absent or the certificate cannot be parsed.
    """
    cert_data = request.headers.get(MTLS_CERT_HEADER)
    if not cert_data:
        return None

    try:
        cert_data = urllib.parse.unquote(cert_data.strip())
        cert = x509.load_pem_x509_certificate(cert_data.encode())

        org = cert.subject.get_attributes_for_oid(x509.NameOID.ORGANIZATION_NAME)
        ou = cert.subject.get_attributes_for_oid(x509.NameOID.ORGANIZATIONAL_UNIT_NAME)
        cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)

        roles: set[str] = {"all"}
        if ou:
            roles.add(str(ou[0].value))
        elif org:
            roles.add(str(org[0].value))
        else:
            roles.add("unknown")

        user = "unknown"
        if cn and cn[0].value:
            user = str(cn[0].value)
        elif ou:
            user = str(ou[0].value)
        elif org:
            user = str(org[0].value)

        return SSOIdentity(
            email=user,
            user=user,
            groups=roles,
            source="mtls",
        )
    except Exception:
        logger.warning("Failed to parse mTLS certificate from header", exc_info=True)
        return None


# ── Unified identity extraction ──────────────────────────────────────────


def extract_identity(
    request: Request,
    *,
    include_request_headers: bool = True,
) -> SSOIdentity | None:
    """Try all authentication methods in priority order.

    Order: mTLS → SPIFFE JWT-SVID → OIDC JWT → gateway headers.
    Returns the first successful identity, or ``None``.
    """
    identity = (
        identity_from_mtls(request) or identity_from_spiffe(request) or identity_from_jwt(request)
    )
    if identity is not None:
        return identity

    if include_request_headers:
        return identity_from_sso_headers(request)

    return None


# ── Convenience accessors ─────────────────────────────────────────────────


def get_sso_user(request: Request) -> str:
    """Return the short username from any auth source.

    Suitable for audit trails and commit messages where a full
    ``SSOIdentity`` is not needed.  Returns ``"anonymous"`` when auth is
    disabled and no identity is present, or ``"unknown"`` when auth is enabled
    but identity extraction failed.
    """
    identity = extract_identity(request, include_request_headers=accept_request_headers())
    if identity:
        return identity.user
    if not auth_required():
        return "anonymous"
    return "unknown"


# ── Auth-gating helpers ───────────────────────────────────────────────────


def auth_required() -> bool:
    """Return ``True`` if authentication enforcement is enabled.

    Reads ``[auth] required`` from the INI.  Defaults to ``True``
    (secure by default).
    """
    return load_auth_config().required


def accept_request_headers() -> bool:
    """Return ``True`` when proxy-injected identity headers should be trusted.

    Reads ``[auth] accept_request_headers`` from the INI.
    """
    return load_auth_config().accept_request_headers


def _normalize_request_path(path: str) -> str:
    """Normalize request paths for exact auth exemption checks."""
    if path == "/":
        return path
    return path.rstrip("/")


def _path_matches_prefix(path: str, prefix: str) -> bool:
    """Return True when *path* equals or is below *prefix*."""
    normalized_prefix = _normalize_request_path(prefix)
    return path == normalized_prefix or path.startswith(f"{normalized_prefix}/")


def _is_cors_preflight(request: Request) -> bool:
    """Return True for CORS preflight requests handled by CORS middleware."""
    return (
        request.method == "OPTIONS"
        and request.headers.get("access-control-request-method") is not None
    )


def _install_openapi_auth_schema(
    app: FastAPI,
    *,
    unauthenticated_paths: frozenset[str],
    deferred_auth_prefixes: tuple[str, ...],
) -> None:
    """Describe middleware-enforced authentication in the generated OpenAPI schema."""
    original_openapi = app.openapi

    def openapi_with_auth() -> dict[str, Any]:
        schema = original_openapi()
        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes[OPENAPI_BEARER_SCHEME_NAME] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": OPENAPI_BEARER_DESCRIPTION,
        }
        schema.pop("security", None)

        for path, path_item in schema.get("paths", {}).items():
            normalized_path = _normalize_request_path(path)
            if normalized_path in unauthenticated_paths:
                operation_security: list[dict[str, list[str]]] = []
            elif any(
                _path_matches_prefix(normalized_path, prefix) for prefix in deferred_auth_prefixes
            ):
                operation_security = [{OPENAPI_BEARER_SCHEME_NAME: []}, {}]
            else:
                operation_security = [{OPENAPI_BEARER_SCHEME_NAME: []}]

            for method, operation in path_item.items():
                if method in {"delete", "get", "head", "options", "patch", "post", "put", "trace"}:
                    operation["security"] = operation_security

        return schema

    app.openapi: Callable[[], dict[str, Any]] = openapi_with_auth  # type: ignore[misc,method-assign]


# ── FastAPI dependencies ──────────────────────────────────────────────────


def _enforce_allowed_groups(identity: SSOIdentity) -> SSOIdentity:
    """Apply per-service allowed_groups to user/JWT identities."""
    cfg = load_auth_config()
    if (
        cfg.allowed_groups
        and identity.source in ("jwt", "sso")
        and not identity.groups.intersection(cfg.allowed_groups)
    ):
        logger.warning("User %s denied: not in any allowed group", identity.user)
        raise HTTPException(
            status_code=403,
            detail="Access denied: user is not a member of any authorized group.",
        )
    return identity


async def require_authenticated_identity(request: Request) -> SSOIdentity:
    """Require a trusted identity from mTLS, SPIFFE JWT-SVID, JWT/OIDC, or headers.

    Validated mTLS, SPIFFE JWT-SVID, and configured JWT/OIDC providers are
    always accepted. Gateway-injected ``X-Auth-Request-*`` headers are only
    accepted when ``[auth] accept_request_headers`` is ``true``.

    When ``[auth] required`` is ``false``, returns
    :data:`ANONYMOUS_IDENTITY` instead of raising.

    Raises ``HTTPException(403)`` if auth is required but no valid
    identity can be extracted.
    """
    identity = extract_identity(request, include_request_headers=accept_request_headers())
    if identity is not None:
        return _enforce_allowed_groups(identity)

    if not auth_required():
        return ANONYMOUS_IDENTITY

    raise HTTPException(
        status_code=403,
        detail="This endpoint requires SSO authentication.",
    )


def require_group(*required: str) -> Callable[..., Any]:
    """FastAPI dependency factory: require identity with at least one matching group.

    Usage::

        @router.post("/render")
        async def render(identity: SSOIdentity = Depends(require_group("nv-config-manager"))):
            ...
    """
    required_set = set(required)

    async def _dependency(
        identity: SSOIdentity = Depends(require_authenticated_identity),
    ) -> SSOIdentity:
        if not identity.groups.intersection(required_set):
            logger.warning(
                "User %s denied: missing required group (needs one of %s, has %s)",
                identity.user,
                required_set,
                identity.groups,
            )
            raise HTTPException(
                status_code=403,
                detail="Access denied: insufficient group membership.",
            )
        return identity

    return _dependency


async def get_sso_identity(request: Request) -> SSOIdentity | None:
    """FastAPI dependency that optionally extracts SSO identity.

    Returns ``None`` when no identity headers are present (never raises).

    Usage::

        @router.get("/info")
        async def info(identity: SSOIdentity | None = Depends(get_sso_identity)):
            ...
    """
    return extract_identity(request, include_request_headers=accept_request_headers())


async def require_sso_or_device(request: Request) -> SSOIdentity | None:
    """FastAPI dependency for endpoints accessible by both SSO users and devices.

    Validated mTLS, SPIFFE JWT-SVID, and configured JWT/OIDC providers are
    always accepted.  Gateway-injected ``X-Auth-Request-*`` headers are only
    accepted when ``[auth] accept_request_headers`` is ``true``.  Returns
    ``None`` when no trusted service/user identity is present so the caller
    can fall back to device-level authorization (e.g. IP allow-list).

    When ``[auth] required`` is ``false``, returns :data:`ANONYMOUS_IDENTITY`
    immediately.
    """
    if not auth_required():
        return ANONYMOUS_IDENTITY

    identity = extract_identity(request, include_request_headers=accept_request_headers())
    if identity is not None:
        return _enforce_allowed_groups(identity)
    return None


# ── FastAPI identity probe ───────────────────────────────────────────────
#
# Installs a ``GET /whoami`` endpoint plus the ``normalize_auth_data``
# middleware on a FastAPI app.  Shared across nv-config-manager FastAPI services so the
# identity contract (request.state.user/roles/auth_source, /whoami response
# shape) is guaranteed identical — regressions in one service can't diverge
# from the others.  Used both by operational diagnostics ("who does X see me
# as?") and by the SPIFFE client-injection integration tests.


class WhoamiResponse(BaseModel):
    """Response shape for ``GET /whoami``."""

    user: str
    roles: list[str]


def install_identity_probe(
    app: FastAPI,
    *,
    require_auth: bool = True,
    enforce_auth: bool = True,
    unauthenticated_paths: frozenset[str] | set[str] | tuple[str, ...] = (
        DEFAULT_UNAUTHENTICATED_PATHS
    ),
    deferred_auth_prefixes: tuple[str, ...] = (),
) -> None:
    """Register ``normalize_auth_data`` middleware + ``GET /whoami`` on ``app``.

    Call this once from each FastAPI app's ``main.py``.  After installation:

    - When ``enforce_auth`` is true, every request requires a trusted identity
      unless the path is in ``unauthenticated_paths``.  This keeps healthchecks
      as the explicit unauthenticated exception instead of requiring every route
      author to remember an auth dependency.
    - Every request has ``request.state.user`` (str), ``request.state.roles``
      (set[str]), and ``request.state.auth_source`` (str) populated from
      :func:`extract_identity`, falling back to ``user="anonymous"`` and
      ``auth_source="anonymous"`` when auth is disabled, or ``user="unknown"``
      and ``auth_source="unknown"`` when auth is enabled but no identity can be derived.
    - ``GET /whoami`` returns the current caller's identity. By default this
      endpoint requires a trusted identity, matching the rest of the API
      surface; set ``require_auth=False`` only for local diagnostics.

    Middleware is appended via :meth:`FastAPI.middleware`; call order within
    the middleware stack follows Starlette's LIFO rule, so later
    ``app.middleware`` registrations run first.  This probe does not depend on
    any other middleware so call order is not important.
    """
    unauthenticated_path_set = frozenset(_normalize_request_path(p) for p in unauthenticated_paths)

    if enforce_auth:
        _install_openapi_auth_schema(
            app,
            unauthenticated_paths=unauthenticated_path_set,
            deferred_auth_prefixes=deferred_auth_prefixes,
        )

    if require_auth:

        @app.get(
            "/whoami",
            name="whoami",
            response_model=WhoamiResponse,
            summary="Current User Info",
        )
        async def whoami_authenticated(
            identity: SSOIdentity = Depends(require_authenticated_identity),
        ) -> WhoamiResponse:
            """Reflect the caller's identity as seen by this service."""
            return WhoamiResponse(
                user=identity.user,
                roles=sorted(identity.groups),
            )

    else:

        @app.get(
            "/whoami",
            name="whoami",
            response_model=WhoamiResponse,
            summary="Current User Info",
        )
        def whoami_diagnostic(request: Request) -> WhoamiResponse:
            """Reflect the caller's identity as seen by this service."""
            return WhoamiResponse(
                user=request.state.user,
                roles=sorted(request.state.roles),
            )

    @app.middleware("http")
    async def normalize_auth_data(
        request: Request, call_next: Callable[[Request], Any]
    ) -> Response:
        """Populate the request's normalized user, roles, and authentication source.

        Uses the consolidated :func:`extract_identity` so mTLS, SPIFFE
        JWT-SVIDs, OIDC JWTs, and trusted OIDC proxy identity headers are
        all recognised uniformly.
        """
        identity = extract_identity(request, include_request_headers=accept_request_headers())
        if identity is not None:
            request.state.user = identity.user
            request.state.roles = identity.groups
            request.state.auth_source = identity.source
        elif not auth_required():
            request.state.user = ANONYMOUS_IDENTITY.user
            request.state.roles = ANONYMOUS_IDENTITY.groups
            request.state.auth_source = ANONYMOUS_IDENTITY.source
        else:
            request.state.user = "unknown"
            request.state.roles = {"all"}
            request.state.auth_source = "unknown"

        response: Response = await call_next(request)
        return response

    if enforce_auth:

        @app.middleware("http")
        async def require_auth_by_default(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            """Require auth on every route except explicit unauthenticated paths."""
            path = _normalize_request_path(request.url.path)
            if (
                path in unauthenticated_path_set
                or _is_cors_preflight(request)
                or any(_path_matches_prefix(path, prefix) for prefix in deferred_auth_prefixes)
            ):
                return await call_next(request)

            try:
                await require_authenticated_identity(request)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

            return await call_next(request)
