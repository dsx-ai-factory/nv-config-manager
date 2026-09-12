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
"""Docker-backed tests for the Kea hash contract that drift detection rests on.

The reconcile loop decides whether to reapply configuration by comparing a hash
it stored earlier against one it reads back from Kea later. Every unit test in
``test_cli.py`` mocks both sides of that comparison with sentinel strings, so
none of them can tell us whether real Kea actually behaves the way the loop
assumes. These tests pin the assumptions against a real server:

* ``config-set`` and ``config-hash-get`` agree on the hash for the same
  effective config. ``_apply_and_verify_kea_config`` raises when they disagree,
  so if this were false every sync would fail.
* The hash is stable when nothing changes, or the loop would report drift on
  every pass.
* The hash changes when the config changes, or real drift would go unnoticed.
* The hash is content-addressed, not a counter or a timestamp, which is what
  makes a hash stored before a restart comparable to one read after it.
* The reconcile loop itself, given a real Kea whose config changed out of
  band, reapplies the Redis desired config and leaves Kea running it again.

These use testcontainers inline, following ``test_kea_dhcp_confgen.py``.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections.abc import AsyncIterator, Iterator
from configparser import ConfigParser
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests
from testcontainers.core.container import DockerContainer

from nv_config_manager.dhcp import cli
from nv_config_manager.dhcp.kea import KeaClient
from nv_config_manager.dhcp.metrics import DHCP_CONFIG_HASH_MISMATCHES, SyncState

KEA_IMAGE = "docker.cloudsmith.io/isc/docker/kea-dhcp4:2.6.2"
KEA_CONTROL_PORT = 8000

# The ISC image ships /etc/kea root-owned while supervisord runs kea-dhcp4 as
# user "kea", so the config-write half of KeaClient.set_config cannot replace
# the file. Granting the group write access mirrors the deployed image, where
# the sidecar's applied config has to survive a Kea restart.
_MAKE_CONFIG_WRITABLE = (
    "sh -c 'chgrp -R kea /etc/kea && chmod -R g+w /etc/kea && "
    "exec supervisord -c /etc/supervisor/supervisord.conf'"
)

# The ISC image keeps its hooks under a different prefix than the deployed
# Ubuntu image, so the path is pinned here rather than taken from confgen.
_ISC_HOOK = "/usr/lib/kea/hooks/libdhcp_lease_cmds.so"

pytestmark = pytest.mark.timeout(300)


def _counter_value(metric: Any, **labels: str) -> float:
    """Return the current value of a labeled counter (0.0 if never touched)."""
    return metric.labels(**labels)._value.get()  # noqa: SLF001


@pytest.fixture(scope="module")
def kea_endpoint() -> Iterator[tuple[str, int]]:
    """Run one Kea server for the module and yield its control endpoint."""
    with (
        DockerContainer(KEA_IMAGE)
        .with_exposed_ports(KEA_CONTROL_PORT)
        .with_command(_MAKE_CONFIG_WRITABLE) as container
    ):
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(KEA_CONTROL_PORT))
        _wait_for_control_agent(host, port)
        yield host, port


def _wait_for_control_agent(host: str, port: int, timeout: int = 120) -> None:
    """Block until the control agent answers status-get, or fail the test."""
    url = f"http://{host}:{port}/"
    deadline = time.monotonic() + timeout
    last_error: Exception | str = "no attempt made"
    while time.monotonic() < deadline:
        try:
            response = requests.post(url, json={"command": "status-get"}, timeout=5)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # noqa: BLE001 - container is still starting
            last_error = exc
        time.sleep(1)
    pytest.fail(f"Kea control agent never became ready within {timeout}s: {last_error}")


@pytest.fixture
async def kea_client(kea_endpoint: tuple[str, int]) -> AsyncIterator[KeaClient]:
    """Yield a client bound to the running Kea, closing its session after."""
    host, port = kea_endpoint
    client = KeaClient(host=host, port=port)
    try:
        yield client
    finally:
        await client.close()


async def _running_dhcp4(kea_client: KeaClient) -> dict[str, Any]:
    """Return the effective Dhcp4 config as Kea currently reports it.

    Building test configs from the running one keeps the control socket and
    interface settings intact. config-set replaces the whole config, so dropping
    the control socket would cut off the channel these tests run over.

    Note that config-get returns a "hash" key alongside "Dhcp4"; only the Dhcp4
    block is usable as config-set input, since Kea rejects the extra key.
    """
    response = await kea_client.get_config(version=4)
    dhcp4: dict[str, Any] = response[0]["arguments"]["Dhcp4"]
    return dhcp4


def _config_with_subnet(
    base_dhcp4: dict[str, Any], subnet_id: int, subnet: str, pool: str
) -> dict[str, Any]:
    """Build a valid config that differs from others only by subnet."""
    dhcp4 = copy.deepcopy(base_dhcp4)
    # memfile keeps the test hermetic; the deployed config uses PostgreSQL.
    dhcp4["lease-database"] = {"type": "memfile", "persist": False}
    dhcp4["hooks-libraries"] = [{"library": _ISC_HOOK}]
    dhcp4["subnet4"] = [{"id": subnet_id, "subnet": subnet, "pools": [{"pool": pool}]}]
    return {"Dhcp4": dhcp4}


async def _config_a(kea_client: KeaClient) -> dict[str, Any]:
    return _config_with_subnet(
        await _running_dhcp4(kea_client), 101, "10.10.0.0/24", "10.10.0.100 - 10.10.0.200"
    )


async def _config_b(kea_client: KeaClient) -> dict[str, Any]:
    return _config_with_subnet(
        await _running_dhcp4(kea_client), 202, "10.20.0.0/24", "10.20.0.100 - 10.20.0.200"
    )


async def test_config_set_and_hash_get_agree_on_the_same_config(kea_client: KeaClient) -> None:
    """The hash from config-set must equal the one config-hash-get reports.

    _apply_and_verify_kea_config treats a disagreement as a failed apply and
    raises KeaException. If real Kea computed these over different things (the
    submitted config versus the effective one, say) every single sync would
    fail, and no mocked test would show it.
    """
    applied_hash = await kea_client.set_config(await _config_a(kea_client), version=4)
    effective_hash = await kea_client.get_config_hash(version=4)

    assert applied_hash is not None, "Kea 2.6 is expected to return a hash from config-set"
    assert effective_hash == applied_hash


async def test_running_hash_is_stable_while_config_is_unchanged(kea_client: KeaClient) -> None:
    """Repeated reads of an unchanged config return the same hash.

    The reconcile loop re-reads this hash every refresh interval and treats any
    difference as drift, so an unstable digest would reapply the config and
    count a mismatch on every pass.
    """
    await kea_client.set_config(await _config_a(kea_client), version=4)

    first = await kea_client.get_config_hash(version=4)
    await asyncio.sleep(1)
    second = await kea_client.get_config_hash(version=4)

    assert first is not None
    assert first == second


async def test_hash_is_content_addressed(kea_client: KeaClient) -> None:
    """A different config hashes differently, and returning to one restores it.

    The second half is what makes the stored hash meaningful across a Kea
    restart: the digest depends only on the effective config, so it is not a
    counter or a timestamp that would drift on its own.
    """
    config_a = await _config_a(kea_client)
    config_b = await _config_b(kea_client)

    await kea_client.set_config(config_a, version=4)
    hash_a = await kea_client.get_config_hash(version=4)

    await kea_client.set_config(config_b, version=4)
    hash_b = await kea_client.get_config_hash(version=4)

    assert hash_a != hash_b, "changed config must change the hash or drift is undetectable"

    await kea_client.set_config(config_a, version=4)
    assert await kea_client.get_config_hash(version=4) == hash_a


async def test_apply_and_verify_returns_a_hash_and_counts_no_drift(
    kea_client: KeaClient,
) -> None:
    """The real apply/verify helper succeeds against real Kea without drift.

    This drives the function the reconcile loop calls, rather than asserting on
    mocked hashes: a successful apply must return a usable hash and must not
    touch the mismatch counter, since nothing diverged.
    """
    before = _counter_value(DHCP_CONFIG_HASH_MISMATCHES, ip_version="4")

    verified_hash = await cli._apply_and_verify_kea_config(
        kea_client, await _config_a(kea_client), 4
    )

    assert verified_hash is not None
    assert verified_hash == await kea_client.get_config_hash(version=4)
    assert _counter_value(DHCP_CONFIG_HASH_MISMATCHES, ip_version="4") == before


async def test_out_of_band_change_makes_the_stored_hash_stale(kea_client: KeaClient) -> None:
    """A config applied behind our back is visible as a hash disagreement.

    This is the EKS-upgrade failure mode from test_cli.py, but with real
    hashes: something other than this process changes Kea's running config, and
    the hash we stored at apply time must no longer match what Kea reports. That
    disagreement is the only thing the loop counts as drift.
    """
    stored_hash = await cli._apply_and_verify_kea_config(kea_client, await _config_a(kea_client), 4)
    assert stored_hash is not None

    # Stand in for Kea restarting onto its bootstrap config, or an operator
    # editing it directly. Either way this process did not do it.
    await kea_client.set_config(await _config_b(kea_client), version=4)

    assert await kea_client.get_config_hash(version=4) != stored_hash


class _StopLoop(Exception):
    """Sentinel raised from a patched asyncio.sleep to exit the sync loop."""


def _dummy_ini() -> ConfigParser:
    cfg = ConfigParser()
    cfg.read_dict(
        {
            "dhcp.kea": {"server": "localhost", "port": "8000"},
            "redis": {"host": "localhost", "port": "6379", "db": "0"},
        }
    )
    return cfg


async def test_sync_loop_repairs_out_of_band_kea_change(
    kea_client: KeaClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The reconcile loop must put Kea back on the Redis config after drift.

    The tests above only show that an out-of-band change *would* look like
    drift. This one runs ``_sync_kea_configuration_async`` against the real
    server: Redis still holds config A, someone else pushes config B onto
    Kea, and the loop has to reapply A. Repair is the running subnet going
    back to A's, not a log line or a mocked hash.
    """
    config_a = await _config_a(kea_client)
    config_b = await _config_b(kea_client)
    real_get = kea_client.get_config_hash
    real_set = kea_client.set_config
    hash_gets = {"n": 0}

    async def get_config_hash(version: int = 4) -> str | None:
        # Apply-verify does one hash-get after startup set. The next get is the
        # loop's drift check: that is the window where a restarted Kea would
        # already be on bootstrap. Inject B *before* that read so the loop sees
        # a real disagreement, then let it reapply A.
        hash_gets["n"] += 1
        if hash_gets["n"] == 2:
            await real_set(config_b, version=version)
        return await real_get(version=version)

    kea_client.get_config_hash = get_config_hash  # type: ignore[method-assign]

    redis_client = MagicMock()
    redis_client.load_kea_config = AsyncMock(return_value=config_a)
    redis_client.close = AsyncMock()

    mismatches_before = _counter_value(DHCP_CONFIG_HASH_MISMATCHES, ip_version="4")

    with (
        caplog.at_level(logging.INFO),
        patch.object(cli, "load_config", return_value=_dummy_ini()),
        patch.object(cli, "inject_lease_db_config", side_effect=lambda cfg, ver: cfg),
        patch.object(cli.KeaClient, "from_config", return_value=kea_client),
        patch.object(cli.RedisClient, "from_config", return_value=redis_client),
        patch.object(cli.asyncio, "sleep", new=AsyncMock(side_effect=_StopLoop())),
    ):
        try:
            await cli._sync_kea_configuration_async(ip_version=4, refresh_interval=1, debug=False)
        except _StopLoop:
            pass

    running = await _running_dhcp4(kea_client)
    assert running["subnet4"][0]["subnet"] == "10.10.0.0/24"
    assert _counter_value(DHCP_CONFIG_HASH_MISMATCHES, ip_version="4") == mismatches_before + 1

    states = {getattr(rec, "sync_state", None) for rec in caplog.records}
    assert SyncState.DRIFT_DETECTED in states
    assert SyncState.IN_SYNC in states
    recovered = [
        rec
        for rec in caplog.records
        if getattr(rec, "sync_state", None) == SyncState.IN_SYNC and "recovered" in rec.getMessage()
    ]
    assert recovered, "repair must log recovered-and-in-sync, not only the startup in-sync"
