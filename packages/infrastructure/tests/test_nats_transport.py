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
"""Real NATS transports; Docker cases opt in with NVCM_TEST_NATS_DOCKER=1."""

import asyncio
import base64
import contextlib
import ipaddress
import json
import os
import secrets
import socket
import ssl
import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import nkeys
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from nats.aio.client import Client
from nats.aio.transport import WebSocketTransport
from nats.errors import Error as NatsError
from nats.errors import NoServersError

from nv_config_manager_infrastructure.nats import NatsClient

NATS_IMAGE = "nats:2.10.26-alpine"
DOCKER_TEST = pytest.mark.skipif(
    os.environ.get("NVCM_TEST_NATS_DOCKER") != "1",
    reason="Set NVCM_TEST_NATS_DOCKER=1 to run disposable local NATS servers",
)


def _key(prefix: int) -> nkeys.KeyPair:
    return nkeys.from_seed(nkeys.encode_seed(secrets.token_bytes(32), prefix))


def _jwt(issuer: nkeys.KeyPair, subject: nkeys.KeyPair, kind: str) -> str:
    """Mint short-lived, test-only NATS claims; no external credentials needed."""
    limits = {"subs": -1, "data": -1, "payload": -1}
    nats_claims: dict[str, Any] = {"type": kind, "version": 2}
    if kind == "user":
        nats_claims.update(limits)
    elif kind == "account":
        nats_claims["limits"] = {
            **limits,
            "conn": -1,
            "imports": -1,
            "exports": -1,
            "wildcards": True,
        }
    claims = {
        "jti": secrets.token_hex(16),
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        "iss": issuer.public_key.decode(),
        "sub": subject.public_key.decode(),
        "nats": nats_claims,
    }
    header = {"typ": "JWT", "alg": "ed25519-nkey"}
    parts = [
        base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=")
        for value in (header, claims)
    ]
    payload = b".".join(parts)
    signature = base64.urlsafe_b64encode(issuer.sign(payload)).rstrip(b"=")
    return (payload + b"." + signature).decode()


@pytest.fixture(scope="module")
def credentials(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("nats-auth")
    operator = _key(nkeys.PREFIX_BYTE_OPERATOR)
    account = _key(nkeys.PREFIX_BYTE_ACCOUNT)
    user = _key(nkeys.PREFIX_BYTE_USER)
    (directory / "operator.jwt").write_text(_jwt(operator, operator, "operator"))
    (directory / "account.jwt").write_text(_jwt(operator, account, "account"))
    (directory / "account.pub").write_text(account.public_key.decode())
    creds = directory / "user.creds"
    creds.write_text(
        "-----BEGIN NATS USER JWT-----\n"
        + _jwt(account, user, "user")
        + "\n------END NATS USER JWT------\n"
        + "-----BEGIN USER NKEY SEED-----\n"
        + user.seed.decode()
        + "\n------END USER NKEY SEED------\n"
    )
    creds.chmod(0o600)
    return directory


async def _real_connect(*args: Any, **kwargs: Any) -> Client:
    conn = Client()
    try:
        await conn.connect(*args, **kwargs)
    except BaseException:
        # Bound cleanup after a failed TLS handshake; retain the connection error.
        transport = conn._transport
        with contextlib.suppress(TimeoutError, OSError, AttributeError):
            await asyncio.wait_for(conn.close(), timeout=1)
        # A failed WebSocket handshake may leave no socket for nats-py to close,
        # but its aiohttp session still needs cleanup.
        if isinstance(transport, WebSocketTransport) and transport._client is not None:
            await transport._client.close()
        raise
    return conn


async def _connect_once(*args: Any, **kwargs: Any) -> Client:
    kwargs.update(connect_timeout=1, allow_reconnect=False, max_reconnect_attempts=1)
    return await _real_connect(*args, **kwargs)


@pytest.fixture(autouse=True)
def bounded_connections() -> Iterator[None]:
    """Exercise real nats-py I/O but bound retries for intentional failures."""

    async def connect(*args: Any, **kwargs: Any) -> Client:
        kwargs.update(
            connect_timeout=1,
            max_reconnect_attempts=50,
            reconnect_time_wait=0.05,
            ping_interval=0.2,
            max_outstanding_pings=2,
        )
        return await _real_connect(*args, **kwargs)

    with patch("nv_config_manager_infrastructure.nats.client.nats.connect", new=connect):
        yield


def _client(url: str, auth_method: str, credentials: Path) -> NatsClient:
    return NatsClient(
        url,
        auth_method=auth_method,
        creds_path=str(credentials / "user.creds"),
        user="test-user",
        password="test-password",
    )


@pytest.mark.parametrize("auth_method", ["password", "JWT"])
@pytest.mark.parametrize("tls_required", [None, False])
async def test_untrusted_info_cannot_elicit_plaintext_auth(
    auth_method: str,
    tls_required: bool | None,
    credentials: Path,
) -> None:
    """A downgrade peer sees a TLS ClientHello, never a NATS CONNECT payload."""
    received: list[bytes] = []
    finished = asyncio.Event()
    release = asyncio.Event()

    async def malicious_peer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        info: dict[str, Any] = {"auth_required": True, "nonce": "test-nonce"}
        if tls_required is not None:
            info["tls_required"] = tls_required
        try:
            writer.write(b"INFO " + json.dumps(info).encode() + b"\r\n")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(65536), timeout=3)
            received.append(data)
            if data.startswith(b"CONNECT "):
                # Let an insecure client finish connecting so the test can close
                # it cleanly and fail on the captured wire bytes.
                writer.write(b"PONG\r\n")
                await writer.drain()
                await asyncio.wait_for(release.wait(), timeout=5)
        except (ConnectionError, TimeoutError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            finished.set()

    async with await asyncio.start_server(malicious_peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = _client(f"tls://127.0.0.1:{port}", auth_method, credentials)

        # Disable retries so the assertion covers exactly one connection.
        try:
            with (
                patch(
                    "nv_config_manager_infrastructure.nats.client.nats.connect", new=_connect_once
                ),
                contextlib.suppress(ssl.SSLError, ConnectionError, NatsError),
            ):
                await asyncio.wait_for(client.connect(), timeout=5)
        finally:
            if client.conn is not None:
                await client.conn.close()
            release.set()
        await asyncio.wait_for(finished.wait(), timeout=5)

    wire = b"".join(received)
    # Assert booleans so pytest does not include credential-bearing wire bytes
    # in failure reports if the downgrade vulnerability is reintroduced.
    starts_with_tls = wire.startswith(b"\x16\x03")
    contains_connect = b"CONNECT " in wire
    contains_password = b"test-password" in wire
    assert starts_with_tls, "Expected a TLS handshake record"
    assert not contains_connect
    assert not contains_password


@pytest.fixture(scope="module")
def tls_files(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("nats-tls")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    (directory / "server.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / "server.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (directory / "server.key").chmod(0o600)
    return directory


def _docker(*args: str) -> str:
    result = subprocess.run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return (result.stdout + (result.stderr if args[0] == "logs" else "")).strip()


def _unused_port() -> int:
    # Explicit host ports survive Docker restarts; Docker-assigned ports may change.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture(scope="module", params=["password", "JWT"])
def nats_server(
    request: pytest.FixtureRequest,
    tls_files: Path,
    credentials: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[str, dict[str, str], Path, str]]:
    """Publish only loopback ports, mount ephemeral fixtures read-only, always clean up."""
    auth_method = request.param
    directory = tmp_path_factory.mktemp("nats-server")
    if auth_method == "JWT":
        auth = (
            'operator: "/auth/operator.jwt"\nresolver: MEMORY\nresolver_preload: { '
            + credentials.joinpath("account.pub").read_text()
            + ': "'
            + credentials.joinpath("account.jwt").read_text()
            + '" }'
        )
    else:
        auth = 'authorization { user: "test-user", password: "test-password" }'
    tls = 'cert_file: "/tls/server.crt", key_file: "/tls/server.key"'
    directory.joinpath("server.conf").write_text(
        f"port: 4222\n{auth}\ntls {{ {tls}, handshake_first: true }}\n"
        f"websocket {{ port: 8443, tls {{ {tls} }}, compression: false }}\n"
    )
    container = _docker(
        "run",
        "--detach",
        "--rm",
        "--network",
        "bridge",
        "--publish",
        f"127.0.0.1:{_unused_port()}:4222",
        "--publish",
        f"127.0.0.1:{_unused_port()}:8443",
        "--volume",
        f"{directory}:/config:ro",
        "--volume",
        f"{tls_files}:/tls:ro",
        "--volume",
        f"{credentials}:/auth:ro",
        NATS_IMAGE,
        "--config",
        "/config/server.conf",
    )
    try:
        for _ in range(50):
            if "Server is ready" in _docker("logs", container):
                break
            time.sleep(0.1)
        else:
            pytest.fail("NATS test server did not become ready")
        urls = {}
        for scheme, port in (("tls", "4222/tcp"), ("wss", "8443/tcp")):
            address = _docker("port", container, port)
            urls[scheme] = f"{scheme}://{address}"
        yield auth_method, urls, tls_files / "server.crt", container
    finally:
        _docker("rm", "--force", container)


@DOCKER_TEST
@pytest.mark.parametrize("scheme", ["tls", "wss"])
async def test_authenticated_transport_roundtrip_and_reconnect(
    nats_server: tuple[str, dict[str, str], Path, str],
    scheme: str,
    credentials: Path,
) -> None:
    auth_method, urls, ca, container = nats_server
    client = _client(urls[scheme], auth_method, credentials)
    client.ssl_context.load_verify_locations(cafile=str(ca))
    reconnected = asyncio.Event()

    async def on_reconnect() -> None:
        reconnected.set()

    with patch.object(client, "_reconnected_cb", new=on_reconnect):
        conn = await client.connect()
    try:
        subject = "test.transport." + secrets.token_hex(8)
        subscription = await conn.subscribe(subject)
        for reconnect in (False, True):
            if reconnect:
                await asyncio.to_thread(_docker, "restart", "--time", "0", container)
                await asyncio.wait_for(reconnected.wait(), timeout=5)
            await conn.flush(timeout=3)
            await conn.publish(subject, b"encrypted roundtrip")
            await conn.flush(timeout=3)
            msg = await subscription.next_msg(timeout=3)
            assert msg.data == b"encrypted roundtrip"
    finally:
        await conn.close()


@DOCKER_TEST
@pytest.mark.parametrize("scheme", ["tls", "wss"])
async def test_untrusted_server_certificate_is_rejected(
    nats_server: tuple[str, dict[str, str], Path, str],
    scheme: str,
    credentials: Path,
) -> None:
    auth_method, urls, _, _ = nats_server
    client = _client(urls[scheme], auth_method, credentials)
    errors: list[Exception] = []

    async def on_error(error: Exception) -> None:
        errors.append(error)

    with (
        patch("nv_config_manager_infrastructure.nats.client.nats.connect", new=_connect_once),
        patch.object(client, "_error_cb", new=on_error),
        pytest.raises((ssl.SSLCertVerificationError, NoServersError)),
    ):
        await asyncio.wait_for(client.connect(), timeout=5)
    assert any("CERTIFICATE_VERIFY_FAILED" in str(error) for error in errors)
