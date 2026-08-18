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
"""Tests for DHCP sync hash reconciliation and reconcile-loop observability."""

import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from nv_config_manager.dhcp import cli
from nv_config_manager.dhcp.kea import KeaException
from nv_config_manager.dhcp.metrics import (
    DHCP_CONFIG_HASH_MISMATCHES,
    DHCP_LAST_SUCCESSFUL_SYNC_TIMESTAMP,
    DHCP_SYNC_FAILURES,
    SyncOperation,
    SyncState,
)

DESIRED_CONFIG: dict[str, Any] = {"Dhcp4": {"subnet4": ["desired"]}}
CHANGED_CONFIG: dict[str, Any] = {"Dhcp4": {"subnet4": ["changed"]}}

# A distinctive lease-DB password used to prove secrets never reach the logs.
_SECRET_PASSWORD = "P@ssw0rd-SUPER-SECRET-DoNotLog"

_INI_WITH_SECRET = f"""
[dhcp.kea]
server = localhost
port = 8000

[dhcp.lease_db]
local = no
host = dhcp-db.example.local
database = kea_dhcp
user = kea_user
password = {_SECRET_PASSWORD}

[redis]
host = localhost
port = 6379
db = 0
lock_db = 0
ssl = false
"""


class _StopLoop(Exception):
    """Sentinel raised from a patched asyncio.sleep to exit the sync loop."""


def _patch_hash_clients(
    mocker: Any,
    *,
    load_kea_config: AsyncMock,
    set_config: AsyncMock,
    get_config_hash: AsyncMock,
) -> tuple[AsyncMock, AsyncMock]:
    """Wire mock KEA/Redis clients into the hash-reconciliation tests."""
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
    kea_client, _ = _patch_hash_clients(
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
    kea_client, _ = _patch_hash_clients(
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
    kea_client, _ = _patch_hash_clients(
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
    kea_client, _ = _patch_hash_clients(
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


async def test_config_set_failure_propagates(mocker: Any) -> None:
    """A failed initial config-set aborts the sync loop with a KeaException."""
    load_kea_config = AsyncMock(return_value=DESIRED_CONFIG)
    set_config = AsyncMock(side_effect=KeaException("Failed to set configuration: boom"))
    get_config_hash = AsyncMock(return_value="HASH_A")
    kea_client, redis_client = _patch_hash_clients(
        mocker,
        load_kea_config=load_kea_config,
        set_config=set_config,
        get_config_hash=get_config_hash,
    )
    _patch_sleep_to_break(mocker)

    with pytest.raises(KeaException, match="Failed to set configuration: boom"):
        await cli._sync_kea_configuration_async(ip_version=4, refresh_interval=5, debug=False)

    get_config_hash.assert_not_awaited()
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
    kea_client, _ = _patch_hash_clients(
        mocker,
        load_kea_config=load_kea_config,
        set_config=set_config,
        get_config_hash=get_config_hash,
    )
    _patch_sleep_to_break(mocker)
    metric = mocker.patch.object(cli, "DHCP_CACHE_REFRESH_ERRORS", MagicMock())
    hash_get_before = _counter_value(
        DHCP_SYNC_FAILURES, operation=SyncOperation.HASH_GET, ip_version="4"
    )

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(ip_version=4, refresh_interval=5, debug=False)

    # Only the startup apply happened; the hash failure did not trigger a reapply.
    set_config.assert_awaited_once_with(DESIRED_CONFIG, version=4)
    metric.labels.assert_called_once_with(ip_version="4")
    metric.labels.return_value.inc.assert_called_once()
    assert (
        _counter_value(DHCP_SYNC_FAILURES, operation=SyncOperation.HASH_GET, ip_version="4")
        == hash_get_before + 1
    )


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
    _patch_hash_clients(
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


def _counter_value(metric: Any, **labels: str) -> float:
    """Return the current value of a labeled counter (0.0 if never touched)."""
    return metric.labels(**labels)._value.get()  # noqa: SLF001


def _gauge_value(metric: Any, **labels: str) -> float:
    """Return the current value of a labeled gauge."""
    return metric.labels(**labels)._value.get()  # noqa: SLF001


def _make_clients(
    load_side_effect: list[Any],
    set_config_side_effect: Any = None,
    *,
    config_hash: str | None = "verified-hash",
) -> tuple[MagicMock, MagicMock]:
    """Build mock Kea and Redis clients wired for the observability tests."""
    kea_client = MagicMock()
    if set_config_side_effect is None:
        kea_client.set_config = AsyncMock(return_value=config_hash)
    else:
        kea_client.set_config = AsyncMock(side_effect=set_config_side_effect)
    kea_client.get_config_hash = AsyncMock(return_value=config_hash)
    kea_client.close = AsyncMock()

    redis_client = MagicMock()
    redis_client.load_kea_config = AsyncMock(side_effect=load_side_effect)
    redis_client.close = AsyncMock()
    return kea_client, redis_client


def _patch_obs_clients(kea_client: MagicMock, redis_client: MagicMock) -> Any:
    """Patch the client factories and a loop-breaking ``asyncio.sleep``."""
    return (
        patch.object(cli.KeaClient, "from_config", return_value=kea_client),
        patch.object(cli.RedisClient, "from_config", return_value=redis_client),
        patch.object(cli.asyncio, "sleep", new=AsyncMock(side_effect=_StopLoop())),
    )


async def _run_sync_until_stop(
    kea_client: MagicMock,
    redis_client: MagicMock,
    *,
    ip_version: int = 4,
    refresh_interval: int = 1,
    debug: bool = False,
) -> None:
    """Run the sync loop with mocked clients, swallowing the stop sentinel."""
    patch_kea, patch_redis, patch_sleep = _patch_obs_clients(kea_client, redis_client)
    with patch_kea, patch_redis, patch_sleep:
        try:
            await cli._sync_kea_configuration_async(ip_version, refresh_interval, debug)
        except _StopLoop:
            pass


def test_record_sync_failure_increments_operation_label() -> None:
    """Each operation label is incremented independently on failure."""
    before = {
        op: _counter_value(DHCP_SYNC_FAILURES, operation=op, ip_version="4")
        for op in (
            SyncOperation.REDIS_READ,
            SyncOperation.CONFIG_GENERATION,
            SyncOperation.CONFIG_SET,
            SyncOperation.HASH_GET,
            SyncOperation.CONFIG_TEST,
            SyncOperation.POSTGRES,
        )
    }

    for op in before:
        cli._record_sync_failure(op, 4, RuntimeError("boom"))

    for op, prior in before.items():
        assert _counter_value(DHCP_SYNC_FAILURES, operation=op, ip_version="4") == prior + 1


def test_record_sync_failure_escapes_newlines_and_hides_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The dependency-error log escapes newlines and redacts DSN credentials."""
    with caplog.at_level(logging.ERROR):
        cli._record_sync_failure(
            SyncOperation.REDIS_READ,
            4,
            RuntimeError(
                f"could not connect to postgresql://kea_user:{_SECRET_PASSWORD}"
                f"@dhcp-db.example.local/kea_dhcp\nretry"
            ),
        )

    blob = _log_blob(caplog)
    assert "\\nretry" in blob
    assert _SECRET_PASSWORD not in blob
    assert ":<redacted>@" in blob
    assert any(rec.sync_state == SyncState.DEPENDENCY_ERROR for rec in caplog.records)

    with caplog.at_level(logging.ERROR):
        caplog.clear()
        cli._record_sync_failure(
            SyncOperation.REDIS_READ,
            4,
            RuntimeError(f"could not connect to redis://:{_SECRET_PASSWORD}@localhost:6379/0"),
        )
    blob = _log_blob(caplog)
    assert _SECRET_PASSWORD not in blob
    assert ":<redacted>@" in blob


async def test_sync_loop_records_redis_read_failure() -> None:
    """A Redis read error in the loop increments the redis_read failure label."""
    initial = {"Dhcp4": {"subnet4": []}}
    kea_client, redis_client = _make_clients(
        load_side_effect=[initial, ConnectionError("redis down")],
    )
    before = _counter_value(DHCP_SYNC_FAILURES, operation=SyncOperation.REDIS_READ, ip_version="4")

    await _run_sync_until_stop(kea_client, redis_client)

    assert (
        _counter_value(DHCP_SYNC_FAILURES, operation=SyncOperation.REDIS_READ, ip_version="4")
        == before + 1
    )


async def test_sync_loop_refresh_error_log_redacts_redis_password(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The outer refresh-loop error log must not leak a Redis DSN password."""
    initial = {"Dhcp4": {"subnet4": []}}
    kea_client, redis_client = _make_clients(
        load_side_effect=[
            initial,
            ConnectionError(f"redis://:{_SECRET_PASSWORD}@localhost:6379/0"),
        ],
    )

    with caplog.at_level(logging.ERROR):
        await _run_sync_until_stop(kea_client, redis_client)

    blob = _log_blob(caplog)
    assert _SECRET_PASSWORD not in blob
    assert ":<redacted>@" in blob
    assert "Error refreshing the KEA config:" in caplog.text


async def test_sync_loop_records_config_set_failure() -> None:
    """A Kea config-set error increments the config_set failure label."""
    initial = {"Dhcp4": {"subnet4": []}}
    changed = {"Dhcp4": {"subnet4": [{"subnet": "10.0.0.0/24"}]}}
    kea_client, redis_client = _make_clients(
        load_side_effect=[initial, changed],
        set_config_side_effect=["verified-hash", RuntimeError("config-set failed")],
    )
    before = _counter_value(DHCP_SYNC_FAILURES, operation=SyncOperation.CONFIG_SET, ip_version="4")

    await _run_sync_until_stop(kea_client, redis_client)

    assert (
        _counter_value(DHCP_SYNC_FAILURES, operation=SyncOperation.CONFIG_SET, ip_version="4")
        == before + 1
    )


async def test_sync_loop_increments_mismatch_and_logs_hashes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drift bumps the mismatch counter and logs desired/running hashes."""
    initial = {"Dhcp4": {"subnet4": []}}
    changed = {"Dhcp4": {"subnet4": [{"subnet": "10.0.0.0/24"}]}}
    kea_client, redis_client = _make_clients(load_side_effect=[initial, changed])
    before = _counter_value(DHCP_CONFIG_HASH_MISMATCHES, ip_version="4")

    with caplog.at_level(logging.INFO):
        await _run_sync_until_stop(kea_client, redis_client)

    assert _counter_value(DHCP_CONFIG_HASH_MISMATCHES, ip_version="4") == before + 1

    states = {getattr(rec, "sync_state", None) for rec in caplog.records}
    assert SyncState.DRIFT_DETECTED in states
    assert SyncState.APPLYING in states
    assert SyncState.IN_SYNC in states

    drift_records = [
        rec
        for rec in caplog.records
        if getattr(rec, "sync_state", None) == SyncState.DRIFT_DETECTED
    ]
    assert drift_records
    drift = drift_records[0]
    assert hasattr(drift, "desired_hash")
    assert hasattr(drift, "running_hash")
    assert drift.desired_hash != drift.running_hash


async def test_sync_loop_updates_last_successful_sync_gauge() -> None:
    """A successful one-shot sync sets the last-successful-sync gauge."""
    config = {"Dhcp4": {"subnet4": []}}
    kea_client, redis_client = _make_clients(load_side_effect=[config])

    with (
        patch.object(cli.KeaClient, "from_config", return_value=kea_client),
        patch.object(cli.RedisClient, "from_config", return_value=redis_client),
        patch.object(cli.time, "time", return_value=1_800_000_000.0),
    ):
        # refresh_interval=0 => run once and return, no monitoring loop.
        await cli._sync_kea_configuration_async(4, 0, False)

    assert _gauge_value(DHCP_LAST_SUCCESSFUL_SYNC_TIMESTAMP, ip_version="4") == 1_800_000_000.0
    kea_client.set_config.assert_awaited_once()
    kea_client.get_config_hash.assert_awaited_once()


async def test_sync_loop_does_not_mark_verified_when_hash_unavailable() -> None:
    """Two missing hashes are not a verified match; the gauge must stay put."""
    config = {"Dhcp4": {"subnet4": []}}
    kea_client, redis_client = _make_clients(
        load_side_effect=[config, config],
        config_hash=None,
    )
    before = _gauge_value(DHCP_LAST_SUCCESSFUL_SYNC_TIMESTAMP, ip_version="4")

    await _run_sync_until_stop(kea_client, redis_client)

    assert _gauge_value(DHCP_LAST_SUCCESSFUL_SYNC_TIMESTAMP, ip_version="4") == before


async def test_sync_loop_logs_waiting_for_initial_redis_config(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The loop emits the waiting-for-initial-Redis-config state until config exists."""
    config = {"Dhcp4": {"subnet4": []}}
    # First read: nothing in Redis (waiting), second read: config appears.
    kea_client, redis_client = _make_clients(load_side_effect=[None, config])

    with caplog.at_level(logging.INFO):
        with (
            patch.object(cli.KeaClient, "from_config", return_value=kea_client),
            patch.object(cli.RedisClient, "from_config", return_value=redis_client),
            patch.object(cli.asyncio, "sleep", new=AsyncMock()),
        ):
            await cli._sync_kea_configuration_async(4, 0, False)

    states = {getattr(rec, "sync_state", None) for rec in caplog.records}
    assert SyncState.WAITING_FOR_INITIAL_REDIS_CONFIG in states


async def test_sync_loop_never_logs_secrets_or_full_config(
    caplog: pytest.LogCaptureFixture,
    custom_ini: Any,
) -> None:
    """Lease-DB credentials and full config bodies must never appear in logs."""
    custom_ini(_INI_WITH_SECRET)

    # Marker embedded in the config body to prove full configs are not logged.
    config_marker = "UNIQUE-CONFIG-MARKER-9f3a"
    initial = {"Dhcp4": {"subnet4": [], "note": config_marker}}
    changed = {"Dhcp4": {"subnet4": [{"subnet": "10.0.0.0/24"}], "note": config_marker}}
    kea_client, redis_client = _make_clients(load_side_effect=[initial, changed])

    with caplog.at_level(logging.DEBUG):
        await _run_sync_until_stop(kea_client, redis_client, debug=True)

    blob = _log_blob(caplog)
    assert _SECRET_PASSWORD not in blob
    assert config_marker not in blob
    # The lease-database dict that inject_lease_db_config adds must not leak.
    assert "lease-database" not in blob


def test_sync_kea_configuration_starts_metrics_server() -> None:
    """The click command must export this process's registry before the loop.

    The reconcile metrics live in config-sync-v4, which has no other HTTP
    server. Without this call they are unreachable from the PodMonitor.
    """
    with (
        patch.object(cli, "start_http_server") as start_http_server,
        patch.object(cli, "_set_config_path"),
        patch.object(cli, "_sync_kea_configuration_async"),
        patch.object(cli.asyncio, "run"),
    ):
        cli.sync_kea_configuration.callback(
            ini_file="/tmp/nv-config-manager.ini",
            ip_version=4,
            refresh_interval=0,
            debug=True,
        )

    start_http_server.assert_called_once_with(cli.SYNC_METRICS_PORT)


def test_sync_kea_configuration_continues_when_metrics_port_is_bound(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A metrics bind failure must not prevent the reconcile loop from starting."""
    with (
        patch.object(cli, "start_http_server", side_effect=OSError("address already in use")),
        patch.object(cli, "_set_config_path"),
        patch.object(cli, "_sync_kea_configuration_async"),
        patch.object(cli.asyncio, "run") as asyncio_run,
        caplog.at_level(logging.ERROR),
    ):
        cli.sync_kea_configuration.callback(
            ini_file="/tmp/nv-config-manager.ini",
            ip_version=4,
            refresh_interval=0,
            debug=True,
        )

    asyncio_run.assert_called_once()
    assert "continuing without metrics export" in caplog.text


def test_sync_metrics_port_is_scrapeable() -> None:
    """SYNC_METRICS_PORT must match the chart port, PodMonitor, and NetworkPolicy.

    Prometheus scrapes named ports declared on the pod, and NetworkPolicy
    enumerates those ports for the monitoring namespace. If any of the four
    sites drifts, the metrics exist in-process but are never collected.
    """
    repo = Path(__file__).resolve().parents[3]
    dhcp_yaml = (repo / "deploy/helm/templates/dhcp.yaml").read_text()
    monitoring_yaml = (repo / "deploy/helm/templates/monitoring.yaml").read_text()
    policy_yaml = (repo / "deploy/helm/templates/network-policy.yaml").read_text()

    port = cli.SYNC_METRICS_PORT
    assert f"containerPort: {port}" in dhcp_yaml
    assert "name: sync-metrics" in dhcp_yaml
    assert "port: sync-metrics" in monitoring_yaml
    assert f"port: {port}" in policy_yaml


def _log_blob(caplog: pytest.LogCaptureFixture) -> str:
    """Flatten every captured record (message + structured fields) to one string."""
    parts: list[str] = []
    for record in caplog.records:
        parts.append(record.getMessage())
        for key, value in record.__dict__.items():
            parts.append(f"{key}={value!r}")
    return "\n".join(parts)
