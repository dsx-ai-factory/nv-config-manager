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
"""Live-cluster integration tests for SPIFFE Workload Identity.

These tests verify the end-to-end chain that the Helm chart, spiffe-helper
sidecar, and ``nv_config_manager.common.auth`` / ``nv_config_manager.common.config`` set up:

1.  spiffe-helper writes a valid JWT-SVID and trust bundle to the shared
    ``/var/run/secrets/spiffe/`` emptyDir in every nv-config-manager pod.
2.  ``get_internal_auth_headers()`` reads that JWT and attaches it as a
    Bearer header on service-to-service calls.
3.  The receiving service validates the JWT against the
    trust bundle, extracts the SPIFFE ID from the ``sub`` claim, and populates
    ``request.state.user`` / ``request.state.roles``.
4.  Forged tokens (bad signature, tampered payload, wrong audience) are
    rejected and the request falls through to the unauthenticated path.

All assertions run *inside* a nv-config-manager pod via ``kubectl exec`` so the probe shares
the same volumes, DNS, and network path that production app code uses.  The
test runner only needs a working ``kubectl`` context pointed at the cluster.

Enable with::

    pytest src/tests/integration/test_spiffe.py \\
        --spiffe \\
        --nv-config-manager-namespace nv-config-manager-test01 \\
        --spiffe-release nv-config-manager-test01
"""

from __future__ import annotations

import base64
import json
import subprocess
import time
from collections.abc import Callable

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.spiffe]


# =============================================================================
# Helpers
# =============================================================================


def _b64url_decode(data: str) -> bytes:
    """Decode unpadded base64url exactly as JWT encodes payload/header."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _b64url_encode(data: bytes) -> str:
    """Base64url-encode without padding (JWT style)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _decode_jwt(token: str) -> tuple[dict[str, object], dict[str, object]]:
    """Return (header, payload) of a JWT without verifying the signature."""
    header_b64, payload_b64, _ = token.split(".")
    header = json.loads(_b64url_decode(header_b64))
    payload = json.loads(_b64url_decode(payload_b64))
    return header, payload


def _whoami_script(url: str, token: str | None) -> str:
    """Return a python snippet that calls ``url`` and prints a JSON status + body line.

    Output format: ``<status> <body>`` on a single line, so the test can easily
    parse both the HTTP status and the JSON response from the service
    ``/whoami`` endpoint.
    """
    header_line = (
        f"headers = {{'Authorization': 'Bearer ' + {token!r}}}"
        if token is not None
        else "headers = {}"
    )
    return (
        "import urllib.request, json, sys\n"
        f"url = {url!r}\n"
        f"{header_line}\n"
        "req = urllib.request.Request(url, headers=headers)\n"
        "try:\n"
        "    with urllib.request.urlopen(req, timeout=8) as r:\n"
        "        print(r.status, r.read().decode())\n"
        "except urllib.error.HTTPError as e:\n"
        "    print(e.code, e.read().decode())\n"
    )


def _run_whoami(
    runner: Callable[[str], subprocess.CompletedProcess[str]],
    url: str,
    token: str | None,
) -> tuple[int, dict[str, object]]:
    """Execute a /whoami probe in-pod and return (http_status, parsed_body).

    On any non-zero exit code from kubectl exec the test calling this helper
    fails with a clear message; we keep the signature tight so call sites stay
    readable.
    """
    result = runner(_whoami_script(url, token))
    if result.returncode != 0:
        pytest.fail(f"kubectl exec failed: rc={result.returncode} stderr={result.stderr.strip()!r}")
    status_str, _, body = result.stdout.strip().partition(" ")
    return int(status_str), json.loads(body)


def _forge_jwt_with_payload_override(
    original_token: str,
    overrides: dict[str, object],
    *,
    keep_signature: bool = True,
) -> str:
    """Build a JWT with the original header+signature but a modified payload.

    ``keep_signature=True`` (default) produces a token that will fail RSA
    verification because the payload hash no longer matches the signature.
    ``keep_signature=False`` returns a garbage signature — also invalid, but
    useful to show that the server's rejection is not signature-specific.
    """
    header_b64, payload_b64, sig_b64 = original_token.split(".")
    payload = json.loads(_b64url_decode(payload_b64))
    payload.update(overrides)
    new_payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":")).encode(),
    )
    new_sig = sig_b64 if keep_signature else "A" * 86
    return f"{header_b64}.{new_payload_b64}.{new_sig}"


# =============================================================================
# On-disk artifact sanity
# =============================================================================


class TestSpiffeHelperWritesArtifacts:
    """spiffe-helper should keep a fresh JWT-SVID and trust bundle on disk."""

    def test_jwt_svid_file_is_populated(self, spiffe_jwt: str) -> None:
        """The ``spiffe_jwt`` fixture raises if the file is missing or empty.

        Reaching this assertion at all proves the file exists; we still
        validate structurally here so a malformed token yields a clearer
        diagnostic than "signature failed" later in the suite.
        """
        parts = spiffe_jwt.split(".")
        assert len(parts) == 3, f"JWT-SVID does not have 3 segments: got {len(parts)}"
        for i, part in enumerate(parts):
            assert part, f"JWT-SVID segment {i} is empty"

    def test_jwt_svid_claims_are_spiffe_shaped(
        self,
        spiffe_jwt: str,
        spiffe_trust_domain: str,
    ) -> None:
        """Decode the JWT and check it looks like a Teleport-issued SVID.

        We don't verify the signature here (that's what the *server* does in
        later tests); we only check the shape of the claims that the app layer
        depends on (``sub``, ``aud``, ``exp``).
        """
        header, claims = _decode_jwt(spiffe_jwt)

        assert header.get("alg") == "RS256", (
            f"Expected RS256 for SPIFFE JWT-SVIDs, got {header.get('alg')!r}"
        )
        assert header.get("kid"), "JWT-SVID header is missing 'kid'"

        sub = claims.get("sub", "")
        assert isinstance(sub, str) and sub.startswith("spiffe://"), (
            f"JWT 'sub' is not a SPIFFE ID: {sub!r}"
        )
        assert sub.startswith(f"spiffe://{spiffe_trust_domain}/"), (
            f"SPIFFE ID trust domain does not match audience: "
            f"sub={sub!r} trust_domain={spiffe_trust_domain!r}"
        )

        aud = claims.get("aud")
        if isinstance(aud, list):
            aud = aud[0]
        assert aud == f"spiffe://{spiffe_trust_domain}", (
            f"aud={aud!r} does not match trust_domain={spiffe_trust_domain!r}"
        )

        exp = claims.get("exp")
        assert isinstance(exp, int | float), f"exp claim missing or wrong type: {exp!r}"
        ttl = exp - time.time()
        assert ttl > 30, (
            f"JWT-SVID expires in {ttl:.0f}s — either spiffe-helper stopped "
            f"rotating or the system clock is off"
        )

    def test_trust_bundle_is_valid_json_with_keys(
        self,
        exec_python_in_spiffe_pod: Callable[[str], subprocess.CompletedProcess[str]],
        spiffe_trust_domain: str,
    ) -> None:
        """bundle.json should hold the JWKS the server uses to verify JWT-SVIDs.

        Teleport's spiffe-helper writes a SPIFFE-style trust bundle:
        ``{"<trust-domain>": "<base64 JWKS>"}``.  We check that the wrapper
        contains our trust domain and that the inner JWKS has at least one key
        with a ``kty`` field — that's the minimum shape the server's PyJWT-based
        validator depends on.
        """
        script = (
            "import pathlib,sys; "
            "sys.stdout.write(pathlib.Path('/var/run/secrets/spiffe/bundle.json').read_text())"
        )
        res = exec_python_in_spiffe_pod(script)
        assert res.returncode == 0, f"read bundle.json failed: {res.stderr!r}"

        bundle = json.loads(res.stdout)
        assert spiffe_trust_domain in bundle, (
            f"Trust bundle does not contain expected trust domain "
            f"{spiffe_trust_domain!r}: bundle keys={list(bundle)}"
        )

        inner = json.loads(_b64url_decode(bundle[spiffe_trust_domain]))
        keys = inner.get("keys", [])
        assert keys, "Inner JWKS has no keys — spiffe-helper has not populated the bundle"
        assert all(k.get("kty") for k in keys), "At least one JWK is missing 'kty'"


# =============================================================================
# End-to-end SPIFFE auth against API /whoami endpoints
#
# Every NVIDIA Config Manager FastAPI service wires the same authenticated /whoami endpoint via
# nv_config_manager.common.auth.install_identity_probe().  /whoami is the smallest endpoint
# that reflects request.state back to the caller, so it's the clean observation
# point for these tests.
# =============================================================================


_WHOAMI_SERVICE_CASES = [
    pytest.param("temporal", "spiffe_temporal_url", id="temporal"),
    pytest.param("render", "spiffe_render_url", id="render"),
    pytest.param("ztp", "spiffe_ztp_url", id="ztp"),
    pytest.param("dhcp", "spiffe_dhcp_url", id="dhcp"),
    pytest.param("config-store", "spiffe_config_store_url", id="config-store"),
]


class TestSpiffeAuthEnforcedAtApis:
    """All FastAPI services must accept valid SPIFFE JWT-SVIDs and reject forgeries."""

    @pytest.mark.parametrize("service,url_fixture", _WHOAMI_SERVICE_CASES)
    def test_valid_jwt_yields_spiffe_identity(
        self,
        request: pytest.FixtureRequest,
        exec_python_in_spiffe_pod: Callable[[str], subprocess.CompletedProcess[str]],
        spiffe_jwt: str,
        spiffe_expected_role: str | None,
        service: str,
        url_fixture: str,
    ) -> None:
        """A real JWT-SVID should authenticate as the workload named in ``sub``."""
        url = request.getfixturevalue(url_fixture)
        _, claims = _decode_jwt(spiffe_jwt)
        sub = claims["sub"]
        assert isinstance(sub, str)

        status, body = _run_whoami(
            exec_python_in_spiffe_pod,
            f"{url}/whoami",
            spiffe_jwt,
        )
        assert status == 200, f"[{service}] /whoami returned {status}: {body}"
        assert body["user"] != "unknown", (
            f"[{service}] did not trust the SPIFFE JWT; got user={body['user']!r}. "
            "Likely causes: wrong trust domain in nv-config-manager.ini audiences, bundle.json "
            "not reachable at jwks_uri, or identity_from_spiffe wiring regressed."
        )
        # The workload name follows _spiffe_id_to_workload_name(): the portion
        # of the SPIFFE ID path after the first '/' with '/' turned into '-'.
        # We don't re-implement the mapping — we just assert the user string is
        # derived from the SPIFFE ID and not some other auth source.
        assert body["user"] in sub.replace("/", "-"), (
            f"[{service}] user={body['user']!r} does not appear to come from sub={sub!r}"
        )
        # Every authenticated caller lands in the baseline `all` group per
        # identity_from_spiffe(); without that, the whole roles chain is
        # broken.
        assert "all" in body["roles"], (
            f"[{service}] expected 'all' in roles, got roles={body['roles']!r}"
        )
        if spiffe_expected_role is not None:
            assert spiffe_expected_role in body["roles"], (
                f"[{service}] expected role {spiffe_expected_role!r} missing from "
                f"roles={body['roles']!r}"
            )

    @pytest.mark.parametrize("service,url_fixture", _WHOAMI_SERVICE_CASES)
    def test_no_token_is_rejected(
        self,
        request: pytest.FixtureRequest,
        exec_python_in_spiffe_pod: Callable[[str], subprocess.CompletedProcess[str]],
        service: str,
        url_fixture: str,
    ) -> None:
        """Absent Bearer header is rejected by authenticated /whoami."""
        url = request.getfixturevalue(url_fixture)
        status, body = _run_whoami(
            exec_python_in_spiffe_pod,
            f"{url}/whoami",
            token=None,
        )
        assert status == 403, f"[{service}] /whoami without auth returned {status}: {body}"

    @pytest.mark.parametrize(
        "label,mutate",
        [
            pytest.param(
                "bad-signature",
                lambda tok: tok[:-4] + ("AAAA" if tok[-4:] != "AAAA" else "BBBB"),
                id="bad-signature",
            ),
            pytest.param(
                "tampered-payload",
                lambda tok: _forge_jwt_with_payload_override(
                    tok,
                    {"sub": "spiffe://attacker.example/evil"},
                ),
                id="tampered-payload",
            ),
            pytest.param(
                "wrong-audience",
                lambda tok: _forge_jwt_with_payload_override(
                    tok,
                    {"aud": "spiffe://other-trust-domain.example"},
                ),
                id="wrong-audience",
            ),
            pytest.param(
                "garbage",
                lambda _tok: "not.a.jwt",
                id="garbage",
            ),
        ],
    )
    @pytest.mark.parametrize("service,url_fixture", _WHOAMI_SERVICE_CASES)
    def test_invalid_jwt_is_rejected(
        self,
        request: pytest.FixtureRequest,
        exec_python_in_spiffe_pod: Callable[[str], subprocess.CompletedProcess[str]],
        spiffe_jwt: str,
        service: str,
        url_fixture: str,
        label: str,
        mutate: Callable[[str], str],
    ) -> None:
        """Every forgery path must be rejected by authenticated /whoami.

        The mutations cover all the rejection branches in
        ``identity_from_spiffe``:

        - bad-signature   -> PyJWT raises InvalidSignatureError
        - tampered-payload-> signature hash mismatch -> InvalidSignatureError
        - wrong-audience  -> PyJWT raises InvalidAudienceError
        - garbage         -> header parse fails -> DecodeError
        """
        url = request.getfixturevalue(url_fixture)
        bogus = mutate(spiffe_jwt)
        status, body = _run_whoami(
            exec_python_in_spiffe_pod,
            f"{url}/whoami",
            bogus,
        )
        assert status == 403, f"[{service}:{label}] /whoami returned {status}: {body}"


# =============================================================================
# RBAC: SPIFFE ID prefix -> role mapping
#
# identity_from_spiffe() walks the chart-configured `spiffe.rbac.groupPrefixes`
# map and adds any matching role to the caller's group set.  Without this, a
# valid SPIFFE identity only has the baseline `all` role, which is not enough
# for most temporal workflows (BackupWorkflow, DeployWorkflow, etc. require
# `nv-config-manager`).  These tests exist because the mapping is configured per-values
# file and is easy to omit or typo.
# =============================================================================


class TestSpiffePrefixRoleMapping:
    """Validate that spiffe.rbac.groupPrefixes actually grants the expected role."""

    def test_valid_jwt_gets_expected_role(
        self,
        exec_python_in_spiffe_pod: Callable[[str], subprocess.CompletedProcess[str]],
        spiffe_temporal_url: str,
        spiffe_jwt: str,
        spiffe_expected_role: str | None,
    ) -> None:
        """A validated SPIFFE identity must be granted the mapped role.

        This test catches the specific class of regression where SPIFFE
        authentication works end-to-end but the role mapping is missing from
        the Helm values — in that state nv-config-manager->temporal workflow calls succeed
        authentication but fail authorization (403 from temporal RBAC).
        """
        if spiffe_expected_role is None:
            pytest.skip(
                "Role-mapping assertion disabled (--spiffe-expected-role=''). "
                "Set --spiffe-expected-role=<role> to enable."
            )

        status, body = _run_whoami(
            exec_python_in_spiffe_pod,
            f"{spiffe_temporal_url}/whoami",
            spiffe_jwt,
        )
        assert status == 200, f"/whoami returned {status}: {body}"

        roles = body.get("roles", [])
        assert spiffe_expected_role in roles, (
            f"Expected role {spiffe_expected_role!r} not granted to SPIFFE "
            f"caller; got roles={roles!r}. Check that the deployed Helm values "
            f"contain a `spiffe.rbac.groupPrefixes` entry whose key is a prefix "
            f"of the JWT's `sub` claim and whose value is {spiffe_expected_role!r}."
        )

    def test_forged_jwt_does_not_get_expected_role(
        self,
        exec_python_in_spiffe_pod: Callable[[str], subprocess.CompletedProcess[str]],
        spiffe_temporal_url: str,
        spiffe_jwt: str,
        spiffe_expected_role: str | None,
    ) -> None:
        """Tampering the ``sub`` claim must not grant the mapped role.

        This is the adversarial counterpart to the positive test: if role
        mapping were applied before signature verification (or to the header
        claims instead of the validated payload) an attacker could swap in any
        SPIFFE ID they liked. We forge a token whose ``sub`` claims to be the
        nv-config-manager workload but use a garbage signature — PyJWT must reject it and
        authenticated /whoami must return 403.
        """
        if spiffe_expected_role is None:
            pytest.skip("Role-mapping assertion disabled (--spiffe-expected-role='').")

        _, claims = _decode_jwt(spiffe_jwt)
        legit_sub = claims["sub"]
        assert isinstance(legit_sub, str)

        # Re-assert the legit sub to make the forgery trivial: if nv-config-manager
        # ever skipped validation, this unchanged sub would pass role mapping.
        forged = _forge_jwt_with_payload_override(
            spiffe_jwt,
            {"sub": legit_sub},
            keep_signature=False,
        )

        status, body = _run_whoami(
            exec_python_in_spiffe_pod,
            f"{spiffe_temporal_url}/whoami",
            forged,
        )
        assert status == 403, f"/whoami accepted forged JWT: {body}"


# =============================================================================
# Client-side SPIFFE JWT injection
#
# The tests above use raw ``urllib`` to hit ``/whoami``.  Those prove the server
# side (identity extraction, trust-bundle validation, RBAC prefix mapping) but
# they cannot catch a bug where the production HTTP clients — TemporalClient,
# ZTPClient, RenderClient — silently stop injecting the JWT-SVID into outgoing
# requests.  Raw probes never go through the clients' session construction or
# header-resolution code, so a regression there can leave the whole suite green
# while every real service-to-service call drops its credentials.
#
# Each test below instantiates the real client (same code path ``from_config``
# wires in production) inside a nv-config-manager pod and calls its :meth:`whoami` method
# against the corresponding service's ``/whoami`` route.  The whoami method
# shares its session + header-resolution path with every other method on the
# client, so a regression in JWT injection fails these tests the same way it
# would fail ``invoke_backup_workflow`` or ``execute_render`` in production.
# =============================================================================


# ``kind`` drives the per-client fixture + script generation below.  Kept at
# module scope so the same tuple can parametrise both the positive and the
# negative control test in lock-step.
_CLIENT_CASES = [
    pytest.param("temporal", "spiffe_temporal_url", id="temporal"),
    pytest.param("ztp", "spiffe_ztp_url", id="ztp"),
    pytest.param("render", "spiffe_render_url", id="render"),
    pytest.param("config-store", "spiffe_config_store_url", id="config-store"),
]


def _client_whoami_script(kind: str, base_url: str, *, authenticated: bool) -> str:
    """Return a python snippet that exercises a real nv-config-manager client in-pod.

    The snippet mirrors production usage:

    - TemporalClient — instantiated with ``user_domain``, used as
      ``async with client:`` so the one persistent session is exercised.
    - ZTPClient / RenderClient / ConfigStoreClient — instantiated without a context manager
      because they create a fresh ``RetryClient`` per-call (the pattern
      ``check_file_exists`` / ``execute_render`` use).

    Output contract (single stdout line):
        ``<json>``  on success  — parsed JSON body of whoami().
        ``!<repr>`` on failure — the exception that escaped asyncio.run.
    """
    headers_expr = "get_internal_auth_headers" if authenticated else "{}"
    if kind == "temporal":
        body = (
            f"    c = TemporalClient(base_url={base_url!r}, "
            f"user_domain='nvidia.com', headers={headers_expr})\n"
            "    async with c:\n"
            "        return await c.whoami()\n"
        )
        import_line = "from nv_config_manager.common.client.temporal import TemporalClient\n"
    else:
        module, cls = {
            "ztp": ("nv_config_manager.common.client.ztp", "ZTPClient"),
            "render": ("nv_config_manager.common.client.render", "RenderClient"),
            "config-store": ("nv_config_manager.common.client.config_store", "ConfigStoreClient"),
        }[kind]
        if kind == "config-store":
            body = (
                f"    c = {cls}(target={base_url!r}, file_type=ConfigStoreType.INTENDED, "
                f"ui_url='https://config-manager.example.com', headers={headers_expr})\n"
                "    try:\n"
                "        return await c.whoami()\n"
                "    finally:\n"
                "        await c.close()\n"
            )
        else:
            body = (
                f"    c = {cls}(base_url={base_url!r}, headers={headers_expr})\n"
                "    return await c.whoami()\n"
            )
        imported_names = f"{cls}, ConfigStoreType" if kind == "config-store" else cls
        import_line = f"from {module} import {imported_names}\n"
    return (
        "import asyncio, json\n"
        f"{import_line}"
        "from nv_config_manager.common.config import get_internal_auth_headers\n"
        "async def _go():\n"
        f"{body}"
        "try:\n"
        "    print(json.dumps(asyncio.run(_go())))\n"
        "except Exception as exc:\n"
        "    print('!' + repr(exc))\n"
    )


def _run_client_script(
    runner: Callable[[str], subprocess.CompletedProcess[str]],
    script: str,
) -> dict[str, object]:
    """Execute ``script`` in-pod and return the parsed whoami JSON body.

    Fails the test with a readable message on kubectl error or on any
    exception raised inside the client snippet — the ``!<repr>`` convention
    from :func:`_client_whoami_script` surfaces the Python exception text
    without an extra round-trip through stderr parsing.
    """
    res = runner(script)
    if res.returncode != 0:
        pytest.fail(
            f"kubectl exec failed: rc={res.returncode} "
            f"stdout={res.stdout.strip()!r} stderr={res.stderr.strip()!r}"
        )
    line = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
    if line.startswith("!"):
        pytest.fail(f"client whoami raised inside pod: {line[1:]}")
    if not line:
        pytest.fail(f"client whoami produced no output; stderr={res.stderr.strip()!r}")
    return json.loads(line)


def _run_client_script_expect_error(
    runner: Callable[[str], subprocess.CompletedProcess[str]],
    script: str,
) -> str:
    """Execute ``script`` in-pod and return the printed exception repr."""
    res = runner(script)
    if res.returncode != 0:
        pytest.fail(
            f"kubectl exec failed: rc={res.returncode} "
            f"stdout={res.stdout.strip()!r} stderr={res.stderr.strip()!r}"
        )
    line = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
    if not line.startswith("!"):
        pytest.fail(f"client whoami unexpectedly succeeded: {line!r}")
    return line[1:]


class TestClientInjectsSpiffeJwt:
    """Production clients must inject SPIFFE JWT-SVIDs end-to-end.

    For each of the three inter-service clients, assert two things:

    1. ``from_config``-style wiring (``headers=get_internal_auth_headers``)
       results in the receiving service seeing a validated SPIFFE identity
       with the expected RBAC role.
    2. A deliberately empty ``headers={}`` configuration is rejected. This is
       the control: without it, a positive result in (1) could be explained by
       some other identity source (gateway-injected headers, residual mTLS)
       rather than the client correctly injecting the JWT.
    """

    @pytest.mark.parametrize("kind,url_fixture", _CLIENT_CASES)
    def test_client_injects_spiffe_jwt(
        self,
        request: pytest.FixtureRequest,
        exec_python_in_spiffe_pod: Callable[[str], subprocess.CompletedProcess[str]],
        spiffe_jwt: str,
        spiffe_expected_role: str | None,
        kind: str,
        url_fixture: str,
    ) -> None:
        """Client configured via internal-auth headers must authenticate."""
        url = request.getfixturevalue(url_fixture)
        _, claims = _decode_jwt(spiffe_jwt)
        sub = claims["sub"]
        assert isinstance(sub, str)

        body = _run_client_script(
            exec_python_in_spiffe_pod,
            _client_whoami_script(kind, url, authenticated=True),
        )

        assert body.get("user") != "unknown", (
            f"[{kind}] client whoami returned user='unknown'. The client is "
            "failing to inject the SPIFFE Bearer header; check `from_config` "
            "wiring and the session's headers= argument."
        )
        user = body["user"]
        assert isinstance(user, str) and user in sub.replace("/", "-"), (
            f"[{kind}] user={user!r} not derived from SPIFFE sub={sub!r}"
        )
        if spiffe_expected_role is not None:
            roles = body.get("roles", [])
            assert isinstance(roles, list), f"[{kind}] roles is not a list: {roles!r}"
            assert spiffe_expected_role in roles, (
                f"[{kind}] expected role {spiffe_expected_role!r} missing from "
                f"{roles!r}; check `spiffe.rbac.groupPrefixes` in the chart values."
            )

    @pytest.mark.parametrize("kind,url_fixture", _CLIENT_CASES)
    def test_client_without_headers_is_rejected(
        self,
        request: pytest.FixtureRequest,
        exec_python_in_spiffe_pod: Callable[[str], subprocess.CompletedProcess[str]],
        kind: str,
        url_fixture: str,
    ) -> None:
        """Sanity control: a client with empty headers must not authenticate.

        If this ever starts returning an authenticated identity, the
        positive test above is probably being short-circuited by something
        else on the internal network path (gateway-injected headers,
        ambient mTLS). In that case the positive result cannot be
        attributed to the client's JWT injection.
        """
        url = request.getfixturevalue(url_fixture)
        error = _run_client_script_expect_error(
            exec_python_in_spiffe_pod,
            _client_whoami_script(kind, url, authenticated=False),
        )
        assert "403" in error or "Forbidden" in error, (
            f"[{kind}] empty-headers client failed for an unexpected reason: {error}. "
            "Expected authenticated /whoami to reject the request."
        )
