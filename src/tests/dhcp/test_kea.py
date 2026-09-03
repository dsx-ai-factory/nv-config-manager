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

from configparser import ConfigParser

import pytest
from aiohttp import ClientResponseError
from aioresponses import aioresponses
from yarl import URL

from nv_config_manager.dhcp.kea import KEA_PORT_ENV, KEA_SERVER_ENV, KeaClient, KeaException


def _kea_config(server: str = "dhcp-internal", port: str = "8000") -> ConfigParser:
    """Build a minimal ConfigParser with a [dhcp.kea] section."""
    config = ConfigParser()
    config.read_dict({"dhcp.kea": {"server": server, "port": port}})
    return config


def test_from_config_uses_ini_endpoint_by_default(monkeypatch) -> None:
    """Without an override, the client targets the INI-configured Kea endpoint."""
    monkeypatch.delenv(KEA_SERVER_ENV, raising=False)
    monkeypatch.delenv(KEA_PORT_ENV, raising=False)

    client = KeaClient.from_config(_kea_config("dhcp-internal", "8000"))

    assert client.url == "http://dhcp-internal:8000/"


def test_from_config_env_override_targets_bootstrap_endpoint(monkeypatch) -> None:
    """The refresh container can retarget Kea via env vars without touching the INI.

    This is how config-refresh reaches Kea through the internal bootstrap Service
    (publishNotReadyAddresses) to run config-test before pods are Ready.
    """
    monkeypatch.setenv(KEA_SERVER_ENV, "dhcp-kea-bootstrap")
    monkeypatch.setenv(KEA_PORT_ENV, "9999")

    client = KeaClient.from_config(_kea_config("dhcp-internal", "8000"))

    assert client.url == "http://dhcp-kea-bootstrap:9999/"


def test_from_config_attached_ignores_env_override(monkeypatch) -> None:
    """Attached clients always target the co-located Kea over localhost."""
    monkeypatch.setenv(KEA_SERVER_ENV, "dhcp-kea-bootstrap")
    monkeypatch.setenv(KEA_PORT_ENV, "9999")

    client = KeaClient.from_config(_kea_config("dhcp-internal", "8000"), attached=True)

    assert client.url == "http://localhost:8000/"


@pytest.mark.parametrize(
    ("method_name", "operation"),
    [("get_lease", "get"), ("delete_lease", "del")],
)
async def test_lease_methods_wrap_kea_requests(method_name: str, operation: str) -> None:
    """Verify domain lease methods hide KEA's control-agent envelope."""
    response = [{"result": 0, "text": "success"}]

    with aioresponses() as mocked:
        mocked.post("http://kea.example.com:8000/", payload=response)
        async with KeaClient(host="kea.example.com", port=8000) as client:
            method = getattr(client, method_name)
            result = await method("2001:db8::5", version=6)

        request = mocked.requests[("POST", URL("http://kea.example.com:8000/"))][0]

    assert result == response
    assert request.kwargs["json"] == {
        "command": f"lease6-{operation}",
        "service": ["dhcp6"],
        "arguments": {
            "ip-address": "2001:db8::5",
            **({"type": "IA_NA"} if operation == "get" else {}),
        },
    }


async def test_get_lease_raises_for_kea_http_error() -> None:
    """Verify non-success KEA transport responses are not silently returned."""
    with aioresponses() as mocked:
        mocked.post("http://kea.example.com:8000/", status=503)
        async with KeaClient(host="kea.example.com", port=8000) as client:
            with pytest.raises(ClientResponseError) as exc_info:
                await client.get_lease("7.245.196.5")

    assert exc_info.value.status == 503


async def test_get_lease_page_forwards_bounded_request() -> None:
    """Verify lease inventory uses KEA pagination rather than get-all."""
    response = [{"result": 0, "arguments": {"leases": []}}]

    with aioresponses() as mocked:
        mocked.post("http://kea.example.com:8000/", payload=response)
        async with KeaClient(host="kea.example.com", port=8000) as client:
            result = await client.get_lease_page(
                limit=75,
                version=6,
                from_address="2001:db8::5",
            )

        request = mocked.requests[("POST", URL("http://kea.example.com:8000/"))][0]

    assert result == response
    assert request.kwargs["json"] == {
        "command": "lease6-get-page",
        "service": ["dhcp6"],
        "arguments": {"from": "2001:db8::5", "limit": 75},
    }


async def test_set_config_returns_effective_hash() -> None:
    """Verify set_config surfaces the SHA-256 hash returned by config-set."""
    config_set_response = [
        {"result": 0, "text": "Configuration successful.", "arguments": {"hash": "ABC123"}}
    ]
    config_write_response = [{"result": 0, "text": "Configuration written."}]

    with aioresponses() as mocked:
        mocked.post("http://kea.example.com:8000/", payload=config_set_response)
        mocked.post("http://kea.example.com:8000/", payload=config_write_response)
        async with KeaClient(host="kea.example.com", port=8000) as client:
            config_hash = await client.set_config({"Dhcp4": {}}, version=4)

        requests = mocked.requests[("POST", URL("http://kea.example.com:8000/"))]

    assert config_hash == "ABC123"
    assert requests[0].kwargs["json"]["command"] == "config-set"
    assert requests[1].kwargs["json"]["command"] == "config-write"


async def test_set_config_returns_none_when_hash_absent() -> None:
    """Verify set_config degrades gracefully on KEA releases without hashes."""
    config_set_response = [{"result": 0, "text": "Configuration successful."}]
    config_write_response = [{"result": 0, "text": "Configuration written."}]

    with aioresponses() as mocked:
        mocked.post("http://kea.example.com:8000/", payload=config_set_response)
        mocked.post("http://kea.example.com:8000/", payload=config_write_response)
        async with KeaClient(host="kea.example.com", port=8000) as client:
            config_hash = await client.set_config({"Dhcp4": {}}, version=4)

    assert config_hash is None


async def test_set_config_raises_on_failure() -> None:
    """Verify a non-zero config-set result raises rather than returning a hash."""
    config_set_response = [{"result": 1, "text": "boom"}]

    with aioresponses() as mocked:
        mocked.post("http://kea.example.com:8000/", payload=config_set_response)
        async with KeaClient(host="kea.example.com", port=8000) as client:
            with pytest.raises(KeaException, match="Failed to set configuration: boom"):
                await client.set_config({"Dhcp4": {}}, version=4)


async def test_get_config_hash_returns_running_hash() -> None:
    """Verify get_config_hash issues config-hash-get and returns the digest."""
    response = [
        {
            "result": 0,
            "text": "Hash of the currently running config",
            "arguments": {"hash": "DEF456"},
        }
    ]

    with aioresponses() as mocked:
        mocked.post("http://kea.example.com:8000/", payload=response)
        async with KeaClient(host="kea.example.com", port=8000) as client:
            config_hash = await client.get_config_hash(version=4)

        request = mocked.requests[("POST", URL("http://kea.example.com:8000/"))][0]

    assert config_hash == "DEF456"
    assert request.kwargs["json"] == {
        "command": "config-hash-get",
        "service": ["dhcp4"],
    }


async def test_get_config_hash_raises_on_failure() -> None:
    """Verify a non-zero config-hash-get result raises a KeaException."""
    response = [{"result": 1, "text": "unsupported"}]

    with aioresponses() as mocked:
        mocked.post("http://kea.example.com:8000/", payload=response)
        async with KeaClient(host="kea.example.com", port=8000) as client:
            with pytest.raises(KeaException, match="Failed to get configuration hash: unsupported"):
                await client.get_config_hash(version=4)


async def test_get_statistics_forwards_service_version() -> None:
    """Verify dashboard statistics target the requested KEA service."""
    response = [{"result": 0, "arguments": {"assigned-addresses": [[2, "timestamp"]]}}]

    with aioresponses() as mocked:
        mocked.post("http://kea.example.com:8000/", payload=response)
        async with KeaClient(host="kea.example.com", port=8000) as client:
            result = await client.get_statistics(version=4)

        request = mocked.requests[("POST", URL("http://kea.example.com:8000/"))][0]

    assert result == response
    assert request.kwargs["json"] == {
        "command": "statistic-get-all",
        "service": ["dhcp4"],
        "arguments": {},
    }
