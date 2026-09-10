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
"""Tests for the DHCP sync loop's hash-based reconciliation with KEA."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from nv_config_manager.dhcp import cli
from nv_config_manager.dhcp.kea import KeaException

DESIRED_CONFIG: dict[str, Any] = {"Dhcp4": {"subnet4": ["desired"]}}
CHANGED_CONFIG: dict[str, Any] = {"Dhcp4": {"subnet4": ["changed"]}}


class _StopLoop(Exception):
    """Sentinel raised from a patched asyncio.sleep to exit the sync loop."""


def _patch_clients(
    mocker: Any,
    *,
    load_kea_config: AsyncMock,
    set_config: AsyncMock,
    get_config_hash: AsyncMock,
) -> tuple[AsyncMock, AsyncMock]:
    """Wire mock KEA/Redis clients into the sync loop under test."""
    kea_client = AsyncMock()
    kea_client.set_config = set_config
    kea_client.get_config_hash = get_config_hash
    kea_client.close = AsyncMock()

    redis_client = AsyncMock()
    redis_client.load_kea_config = load_kea_config
    redis_client.close = AsyncMock()

    mocker.patch.object(cli.KeaClient, "from_config", return_value=kea_client)
    mocker.patch.object(cli.RedisClient, "from_config", return_value=redis_client)
    # Keep configs comparable: skip lease-db secret injection.
    mocker.patch.object(cli, "inject_lease_db_config", side_effect=lambda cfg, ver: cfg)
    return kea_client, redis_client


def _patch_sleep_to_break(mocker: Any) -> AsyncMock:
    """Patch asyncio.sleep so the loop's tail sleep raises to break the loop."""
    return mocker.patch.object(cli.asyncio, "sleep", side_effect=_StopLoop)


async def test_matching_hash_does_not_reapply(mocker: Any) -> None:
    """When Redis is unchanged and KEA's hash matches, nothing is reapplied."""
    load_kea_config = AsyncMock(side_effect=[DESIRED_CONFIG, DESIRED_CONFIG])
    set_config = AsyncMock(return_value="HASH_A")
    get_config_hash = AsyncMock(return_value="HASH_A")
    kea_client, _ = _patch_clients(
        mocker,
        load_kea_config=load_kea_config,
        set_config=set_config,
        get_config_hash=get_config_hash,
    )
    _patch_sleep_to_break(mocker)

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(ip_version=4, refresh_interval=5, debug=False)

    # Only the unconditional startup apply happened; no reapply on match.
    set_config.assert_awaited_once_with(DESIRED_CONFIG, version=4)
    kea_client.close.assert_awaited_once()


async def test_mismatched_hash_reapplies(mocker: Any) -> None:
    """When Redis is unchanged but KEA's hash drifts, the config is reapplied."""
    load_kea_config = AsyncMock(side_effect=[DESIRED_CONFIG, DESIRED_CONFIG])
    set_config = AsyncMock(return_value="HASH_A")
    # startup verify -> drift detected -> reapply verify
    get_config_hash = AsyncMock(side_effect=["HASH_A", "DRIFT_HASH", "HASH_A"])
    kea_client, _ = _patch_clients(
        mocker,
        load_kea_config=load_kea_config,
        set_config=set_config,
        get_config_hash=get_config_hash,
    )
    _patch_sleep_to_break(mocker)

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(ip_version=4, refresh_interval=5, debug=False)

    # Startup apply + reapply on drift, both with the desired config.
    assert set_config.await_args_list == [
        call(DESIRED_CONFIG, version=4),
        call(DESIRED_CONFIG, version=4),
    ]


async def test_kea_restart_bootstrap_recovery_with_unchanged_redis(mocker: Any) -> None:
    """A KEA restart to bootstrap config is recovered even when Redis is unchanged.

    This is the EKS-upgrade failure mode: the Kea container restarts from its
    bootstrap /etc/kea/kea-dhcp4.conf while the sidecar keeps running, so Redis
    never changes. The loop must still detect the effective-hash drift and
    reapply the desired configuration.
    """
    load_kea_config = AsyncMock(side_effect=[DESIRED_CONFIG, DESIRED_CONFIG])
    set_config = AsyncMock(return_value="DESIRED_HASH")
    # startup verify -> bootstrap hash after Kea restart -> reapply verify
    get_config_hash = AsyncMock(side_effect=["DESIRED_HASH", "BOOTSTRAP_HASH", "DESIRED_HASH"])
    kea_client, _ = _patch_clients(
        mocker,
        load_kea_config=load_kea_config,
        set_config=set_config,
        get_config_hash=get_config_hash,
    )
    _patch_sleep_to_break(mocker)

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(ip_version=4, refresh_interval=5, debug=False)

    assert set_config.await_count == 2
    assert set_config.await_args_list[-1] == call(DESIRED_CONFIG, version=4)


async def test_redis_changed_reapplies_new_config(mocker: Any) -> None:
    """When the desired config in Redis changes, the new config is applied."""
    load_kea_config = AsyncMock(side_effect=[DESIRED_CONFIG, CHANGED_CONFIG])
    set_config = AsyncMock(side_effect=["HASH_A", "HASH_B"])
    get_config_hash = AsyncMock(side_effect=["HASH_A", "HASH_B"])
    kea_client, _ = _patch_clients(
        mocker,
        load_kea_config=load_kea_config,
        set_config=set_config,
        get_config_hash=get_config_hash,
    )
    _patch_sleep_to_break(mocker)

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(ip_version=4, refresh_interval=5, debug=False)

    assert set_config.await_args_list == [
        call(DESIRED_CONFIG, version=4),
        call(CHANGED_CONFIG, version=4),
    ]


async def test_config_set_failure_does_not_abort_sync(mocker: Any) -> None:
    """A failed initial config-set is retried instead of crashing the sidecar."""
    load_kea_config = AsyncMock(return_value=DESIRED_CONFIG)
    set_config = AsyncMock(side_effect=KeaException("Failed to set configuration: boom"))
    get_config_hash = AsyncMock(return_value="HASH_A")
    kea_client, redis_client = _patch_clients(
        mocker,
        load_kea_config=load_kea_config,
        set_config=set_config,
        get_config_hash=get_config_hash,
    )
    _patch_sleep_to_break(mocker)
    metric = mocker.patch.object(cli, "DHCP_CACHE_REFRESH_ERRORS", MagicMock())

    # Reaching the retry sleep proves the exception was swallowed.
    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(ip_version=4, refresh_interval=5, debug=False)

    get_config_hash.assert_not_awaited()
    metric.labels.assert_called_once_with(ip_version="4")
    metric.labels.return_value.inc.assert_called_once()
    kea_client.close.assert_awaited_once()
    redis_client.close.assert_awaited_once()


async def test_config_hash_get_failure_is_handled_without_reapply(mocker: Any) -> None:
    """A config-hash-get failure is counted as an error and does not reapply."""
    load_kea_config = AsyncMock(side_effect=[DESIRED_CONFIG, DESIRED_CONFIG])
    set_config = AsyncMock(return_value="HASH_A")
    # startup verify succeeds; drift check raises.
    get_config_hash = AsyncMock(
        side_effect=["HASH_A", KeaException("Failed to get configuration hash: down")]
    )
    kea_client, _ = _patch_clients(
        mocker,
        load_kea_config=load_kea_config,
        set_config=set_config,
        get_config_hash=get_config_hash,
    )
    _patch_sleep_to_break(mocker)
    metric = mocker.patch.object(cli, "DHCP_CACHE_REFRESH_ERRORS", MagicMock())

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(ip_version=4, refresh_interval=5, debug=False)

    # Only the startup apply happened; the hash failure did not trigger a reapply.
    set_config.assert_awaited_once_with(DESIRED_CONFIG, version=4)
    metric.labels.assert_called_once_with(ip_version="4")
    metric.labels.return_value.inc.assert_called_once()


@pytest.mark.parametrize(
    "startup_error",
    [
        KeaException("Failed to get configuration hash: down"),
        TimeoutError("KEA Request timed out, are you running within a KEA Docker Container?"),
    ],
    ids=["kea_exception", "timeout"],
)
async def test_startup_hash_get_failure_does_not_abort_sync(
    mocker: Any, startup_error: Exception
) -> None:
    """A config-hash-get failure at startup must not abort the sync loop.

    config-set has already applied and persisted the desired config at that
    point, so only the verification read failed -- the same failure the refresh
    loop tolerates. Aborting would crash-loop the sidecar over a config that is
    actually applied. TimeoutError is included because get_config_hash re-raises
    it instead of wrapping it in KeaException.
    """
    load_kea_config = AsyncMock(side_effect=[DESIRED_CONFIG, DESIRED_CONFIG])
    set_config = AsyncMock(return_value="HASH_A")
    # Startup verification fails; the later drift check succeeds.
    get_config_hash = AsyncMock(side_effect=[startup_error, "HASH_A"])
    _patch_clients(
        mocker,
        load_kea_config=load_kea_config,
        set_config=set_config,
        get_config_hash=get_config_hash,
    )
    _patch_sleep_to_break(mocker)

    # Reaching the loop's tail sleep proves startup was not aborted.
    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(ip_version=4, refresh_interval=5, debug=False)

    # Startup apply, plus a reapply once the unverified hash is re-checked.
    assert set_config.await_args_list == [
        call(DESIRED_CONFIG, version=4),
        call(DESIRED_CONFIG, version=4),
    ]
