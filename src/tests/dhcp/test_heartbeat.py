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
"""Tests for the config-sync liveness heartbeat.

Covers:
- The ``check-sync-heartbeat`` CLI subcommand exit codes (fresh / stale / missing).
- The reconcile loop advancing the heartbeat after a completed attempt,
  including when a recoverable error occurred.
- Bounded timeouts around Redis/Kea calls so a hung dependency cannot block
  heartbeat advancement.
- "Last successful reconciliation" being tracked separately from loop progress.
"""

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from nv_config_manager.dhcp import cli, heartbeat
from nv_config_manager.dhcp.cli import cli as cli_group
from nv_config_manager.dhcp.kea import KeaException
from nv_config_manager.dhcp.metrics import DHCP_CACHE_REFRESH_ERRORS

CONFIG = {"Dhcp4": {}}


class _StopLoop(BaseException):
    """Break out of the otherwise-infinite sync loop from a patched sleep."""


def _error_count() -> float:
    return DHCP_CACHE_REFRESH_ERRORS.labels(ip_version="4")._value.get()


# --------------------------------------------------------------------------- #
# heartbeat module primitives
# --------------------------------------------------------------------------- #


def test_touch_heartbeat_creates_and_refreshes(tmp_path) -> None:
    hb = str(tmp_path / "hb")
    assert heartbeat.heartbeat_age_seconds(hb) is None

    heartbeat.touch_heartbeat(hb)
    assert os.path.exists(hb)

    old = time.time() - 1000
    os.utime(hb, (old, old))
    heartbeat.touch_heartbeat(hb)
    # mtime moved back to ~now, so age is small again.
    assert heartbeat.heartbeat_age_seconds(hb) < 5


def test_heartbeat_is_fresh_vs_stale(tmp_path) -> None:
    hb = str(tmp_path / "hb")
    heartbeat.touch_heartbeat(hb)
    assert heartbeat.heartbeat_is_fresh(hb, max_age=60) is True

    old = time.time() - 3600
    os.utime(hb, (old, old))
    assert heartbeat.heartbeat_is_fresh(hb, max_age=60) is False


def test_future_dated_heartbeat_is_stale(tmp_path) -> None:
    """A clock step backwards must not make a wedged loop look healthy forever."""
    hb = str(tmp_path / "hb")
    heartbeat.touch_heartbeat(hb)
    ahead = time.time() + 3600
    os.utime(hb, (ahead, ahead))

    assert heartbeat.heartbeat_age_seconds(hb) < 0
    assert heartbeat.heartbeat_is_fresh(hb, max_age=60) is False

    result = CliRunner().invoke(
        cli_group,
        ["check-sync-heartbeat", "--heartbeat-file", hb, "--max-age", "60"],
    )
    assert result.exit_code == 1
    assert "stale" in result.output


def test_unreadable_heartbeat_path_is_not_fresh(tmp_path) -> None:
    """A stat failure other than "missing" must not escape the liveness check."""
    not_a_directory = tmp_path / "hb"
    not_a_directory.write_text("")
    hb = str(not_a_directory / "hb")  # ENOTDIR, not FileNotFoundError

    assert heartbeat.heartbeat_age_seconds(hb) is None
    assert heartbeat.heartbeat_is_fresh(hb, max_age=60) is False

    result = CliRunner().invoke(
        cli_group,
        ["check-sync-heartbeat", "--heartbeat-file", hb, "--max-age", "60"],
    )
    assert result.exit_code == 1


def test_record_successful_reconciliation_tracks_timestamp() -> None:
    heartbeat._last_successful_reconciliation = None
    assert heartbeat.last_successful_reconciliation() is None
    heartbeat.record_successful_reconciliation(now=123.0)
    assert heartbeat.last_successful_reconciliation() == 123.0


# --------------------------------------------------------------------------- #
# check-sync-heartbeat CLI subcommand exit codes
# --------------------------------------------------------------------------- #


def test_check_sync_heartbeat_fresh_exits_zero(tmp_path) -> None:
    hb = tmp_path / "hb"
    hb.write_text("")  # mtime == now
    result = CliRunner().invoke(
        cli_group,
        ["check-sync-heartbeat", "--heartbeat-file", str(hb), "--max-age", "60"],
    )
    assert result.exit_code == 0, result.output


def test_check_sync_heartbeat_stale_exits_nonzero(tmp_path) -> None:
    hb = tmp_path / "hb"
    hb.write_text("")
    old = time.time() - 3600
    os.utime(hb, (old, old))
    result = CliRunner().invoke(
        cli_group,
        ["check-sync-heartbeat", "--heartbeat-file", str(hb), "--max-age", "60"],
    )
    assert result.exit_code == 1
    assert "stale" in result.output


def test_check_sync_heartbeat_missing_exits_nonzero(tmp_path) -> None:
    result = CliRunner().invoke(
        cli_group,
        [
            "check-sync-heartbeat",
            "--heartbeat-file",
            str(tmp_path / "does-not-exist"),
            "--max-age",
            "60",
        ],
    )
    assert result.exit_code == 1
    assert "missing" in result.output


# --------------------------------------------------------------------------- #
# reconcile loop: heartbeat advancement, error handling, timeouts
# --------------------------------------------------------------------------- #


@pytest.fixture()
def sync_env(mocker, tmp_path):
    """Patch clients/helpers so the sync loop runs one bounded iteration."""
    heartbeat._last_successful_reconciliation = None

    kea = MagicMock()
    kea.set_config = AsyncMock(return_value="HASH")
    kea.get_config_hash = AsyncMock(return_value="HASH")
    kea.close = AsyncMock()

    redis = MagicMock()
    redis.close = AsyncMock()

    mocker.patch.object(cli.KeaClient, "from_config", return_value=kea)
    mocker.patch.object(cli.RedisClient, "from_config", return_value=redis)
    mocker.patch.object(cli, "inject_lease_db_config", side_effect=lambda config, version: config)

    touch = mocker.patch.object(cli, "touch_heartbeat")
    record = mocker.patch.object(cli, "record_successful_reconciliation")

    # Break the infinite loop deterministically at the end-of-iteration sleep.
    mocker.patch.object(cli.asyncio, "sleep", new=AsyncMock(side_effect=_StopLoop()))

    return MagicMock(kea=kea, redis=redis, touch=touch, record=record, hb=str(tmp_path / "hb"))


async def test_loop_advances_heartbeat_on_success(sync_env) -> None:
    # Initial load + one loop load, both returning config unchanged.
    sync_env.redis.load_kea_config = AsyncMock(side_effect=[CONFIG, CONFIG])

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(4, 10, False, sync_env.hb)

    # Pre-read seed + post-apply seed + one loop-iteration touch.
    assert sync_env.touch.call_count == 3
    # Successful reconciliation recorded for both the initial set and the iteration.
    assert sync_env.record.call_count == 2


async def test_loop_advances_heartbeat_after_recoverable_error(sync_env) -> None:
    before = _error_count()
    # Initial load OK; the loop's load raises a recoverable error.
    sync_env.redis.load_kea_config = AsyncMock(side_effect=[CONFIG, RuntimeError("redis blip")])

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(4, 10, False, sync_env.hb)

    # Heartbeat still advanced after the failed attempt (seeds + loop finally).
    assert sync_env.touch.call_count == 3
    # But the failed iteration did NOT count as a successful reconciliation
    # (only the initial set did) -- the two are tracked separately.
    assert sync_env.record.call_count == 1
    # The recoverable error was counted.
    assert _error_count() == before + 1


async def test_hung_dependency_is_bounded_and_heartbeat_advances(sync_env, mocker) -> None:
    before = _error_count()
    mocker.patch.object(cli, "REDIS_OP_TIMEOUT_SECONDS", 0.05)

    calls = {"n": 0}

    async def load(ip_version):
        calls["n"] += 1
        if calls["n"] == 1:
            return CONFIG
        # Simulate a Redis call that hangs forever; wait_for must bound it.
        await asyncio.Event().wait()

    sync_env.redis.load_kea_config = load

    start = time.monotonic()
    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(4, 10, False, sync_env.hb)
    elapsed = time.monotonic() - start

    # The hung call was cancelled well within a second, not left to block.
    assert elapsed < 2.0
    # Heartbeat advanced despite the hang (seeds + loop finally).
    assert sync_env.touch.call_count == 3
    # The timeout was surfaced as a recoverable error.
    assert _error_count() == before + 1
    # A hung reconcile is not a success.
    assert sync_env.record.call_count == 1


async def test_heartbeat_advances_while_waiting_for_initial_config(sync_env, mocker) -> None:
    """A cold start with no config published yet must keep the heartbeat fresh.

    Polling Redis for a config that has not been published is legitimate
    progress, not a wedged loop. If the heartbeat stalled here, the exec
    livenessProbe would restart-loop the sidecar once its grace period expired.
    """
    # No config for the first two reads, then it appears.
    sync_env.redis.load_kea_config = AsyncMock(side_effect=[None, None, CONFIG, CONFIG])

    sleeps = {"n": 0}

    async def sleep(_seconds):
        sleeps["n"] += 1
        # Let both wait-loop polls run, then break out at the refresh sleep.
        if sleeps["n"] > 2:
            raise _StopLoop()

    mocker.patch.object(cli.asyncio, "sleep", new=sleep)

    at_first_apply = {}

    async def set_config(*_args, **_kwargs):
        at_first_apply["touches"] = sync_env.touch.call_count
        at_first_apply["records"] = sync_env.record.call_count

    sync_env.kea.set_config = AsyncMock(side_effect=set_config)

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(4, 10, False, sync_env.hb)

    # Pre-read seed plus one touch per poll, all before any config was applied.
    assert at_first_apply["touches"] == 3
    # Waiting is loop progress, never a successful reconciliation.
    assert at_first_apply["records"] == 0
    # Both reconciliations happen only once the config appears.
    assert sync_env.record.call_count == 2


@pytest.fixture()
def _startup_failure(mocker):
    """Stop the sync loop at its second sleep: one startup retry, then the refresh sleep."""
    sleeps = {"n": 0}

    async def sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] > 1:
            raise _StopLoop()

    mocker.patch.object(cli.asyncio, "sleep", new=sleep)


async def test_startup_survives_a_redis_outage(sync_env, _startup_failure) -> None:
    """Redis being down before the first apply must not exit into CrashLoopBackOff."""
    before = _error_count()
    sync_env.redis.load_kea_config = AsyncMock(
        side_effect=[ConnectionError("redis down"), CONFIG, CONFIG]
    )

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(4, 10, False, sync_env.hb)

    # The outage was counted as recoverable, and the loop went on to reconcile
    # twice (initial apply plus one monitoring iteration) once Redis came back.
    assert _error_count() == before + 1
    assert sync_env.record.call_count == 2
    # Seed + retry touch + post-apply + monitoring iteration.
    assert sync_env.touch.call_count == 4


async def test_startup_survives_a_hung_redis_call(sync_env, _startup_failure, mocker) -> None:
    """The bounded loader's TimeoutError is recoverable on the startup path too."""
    before = _error_count()
    mocker.patch.object(cli, "REDIS_OP_TIMEOUT_SECONDS", 0.05)

    calls = {"n": 0}

    async def load(ip_version):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.Event().wait()
        return CONFIG

    sync_env.redis.load_kea_config = load

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(4, 10, False, sync_env.hb)

    assert _error_count() == before + 1
    assert sync_env.record.call_count == 2


async def test_startup_survives_a_kea_outage(sync_env, _startup_failure) -> None:
    """Kea being down before the first apply must not exit into CrashLoopBackOff."""
    before = _error_count()
    sync_env.redis.load_kea_config = AsyncMock(side_effect=[CONFIG, CONFIG])
    sync_env.kea.set_config = AsyncMock(side_effect=[ConnectionError("kea down"), "HASH"])

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(4, 10, False, sync_env.hb)

    # The outage was counted as recoverable, and the loop went on to reconcile
    # twice (initial apply plus one monitoring iteration) once Kea came up.
    assert _error_count() == before + 1
    assert sync_env.record.call_count == 2
    # Seed + retry touch + post-apply + monitoring iteration.
    assert sync_env.touch.call_count == 4


@pytest.mark.parametrize(
    "verification_error",
    [
        KeaException("Failed to get configuration hash: down"),
        TimeoutError("KEA Request timed out"),
    ],
    ids=["kea_exception", "timeout"],
)
async def test_unverified_startup_apply_is_not_a_successful_reconciliation(
    sync_env, verification_error
) -> None:
    """An apply whose verification read failed must not record success.

    config-set succeeded, so the config is applied and the sidecar keeps going,
    but nothing has confirmed KEA is running it. Recording success here would
    freshen the staleness gauge on an unverified apply. The monitoring loop's
    drift check reapplies and re-verifies, and only that counts.
    """
    sync_env.redis.load_kea_config = AsyncMock(side_effect=[CONFIG, CONFIG])
    # Startup verification fails; the reapply and its verification then succeed.
    sync_env.kea.get_config_hash = AsyncMock(side_effect=[verification_error, "HASH", "HASH"])

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(4, 10, False, sync_env.hb)

    # Startup apply plus the reapply triggered by the unverified hash.
    assert sync_env.kea.set_config.await_count == 2
    # Only the verified reconcile in the monitoring loop counted.
    assert sync_env.record.call_count == 1


async def test_startup_survives_a_hung_kea_call(sync_env, _startup_failure, mocker) -> None:
    """The bounded apply's TimeoutError is recoverable on the startup path too."""
    before = _error_count()
    mocker.patch.object(cli, "KEA_OP_TIMEOUT_SECONDS", 0.05)
    sync_env.redis.load_kea_config = AsyncMock(side_effect=[CONFIG, CONFIG])

    calls = {"n": 0}

    async def set_config(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.Event().wait()
        return "HASH"

    sync_env.kea.set_config = set_config

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(4, 10, False, sync_env.hb)

    assert _error_count() == before + 1
    assert sync_env.record.call_count == 2


# --------------------------------------------------------------------------- #
# startup apply: deterministic rejections must not retry a fixed payload forever
# --------------------------------------------------------------------------- #

_BAD_CONFIG = {"Dhcp4": {"subnet4": "not-a-list"}}
_REJECTED = KeaException("Failed to set configuration: not a valid subnet4 list")


def _reject_only(bad_config):
    """Fail set_config for ``bad_config``, succeed for anything else."""

    async def set_config(configuration, *_args, **_kwargs):
        if configuration == bad_config:
            raise _REJECTED
        return "HASH"

    return set_config


async def test_startup_apply_is_bounded_and_self_heals_on_a_corrected_config(
    sync_env, mocker
) -> None:
    """A config KEA deterministically rejects must not spin at 1Hz forever.

    The startup apply retries a payload held in memory, so a rejection that is
    not going to start working (config-set returning non-zero) would otherwise
    be retried indefinitely while the heartbeat stayed fresh -- a silent hang
    that no restart and no corrected config could clear. Attempts are bounded
    and the loop hands off to the monitoring loop, which re-reads Redis and so
    picks up a correction.
    """
    mocker.patch.object(cli, "STARTUP_APPLY_ATTEMPTS", 3)
    # The rejected config is published first, then corrected.
    sync_env.redis.load_kea_config = AsyncMock(side_effect=[_BAD_CONFIG, CONFIG, CONFIG])
    sync_env.kea.set_config = AsyncMock(side_effect=_reject_only(_BAD_CONFIG))

    sleeps = {"n": 0}

    async def sleep(_seconds):
        sleeps["n"] += 1
        # Let all three bounded startup retries run, then break at the
        # monitoring loop's end-of-iteration sleep.
        if sleeps["n"] > 3:
            raise _StopLoop()

    mocker.patch.object(cli.asyncio, "sleep", new=sleep)

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(4, 10, False, sync_env.hb)

    # Exactly the bounded number of startup attempts, then the corrected config.
    applied = [call.args[0] for call in sync_env.kea.set_config.await_args_list]
    assert applied == [_BAD_CONFIG, _BAD_CONFIG, _BAD_CONFIG, CONFIG]
    # Only the corrected apply counted; the rejected startup never did.
    assert sync_env.record.call_count == 1


async def test_rejected_startup_config_does_not_block_the_drift_check(sync_env, mocker) -> None:
    """With nothing applied and Redis unavailable there is no baseline to drift from.

    Handing off with no applied config means the monitoring loop must not treat
    the exhausted payload as a drift baseline: reapplying it would resume the
    same rejected apply, and there is no published desired state to reconcile.
    """
    mocker.patch.object(cli, "STARTUP_APPLY_ATTEMPTS", 1)
    # Rejected config at startup, then Redis goes away.
    sync_env.redis.load_kea_config = AsyncMock(
        side_effect=[_BAD_CONFIG, ConnectionError("redis down")]
    )
    sync_env.kea.set_config = AsyncMock(side_effect=_reject_only(_BAD_CONFIG))

    sleeps = {"n": 0}

    async def sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] > 1:
            raise _StopLoop()

    mocker.patch.object(cli.asyncio, "sleep", new=sleep)

    with pytest.raises(_StopLoop):
        await cli._sync_kea_configuration_async(4, 10, False, sync_env.hb)

    # Only the single bounded startup attempt -- the payload is not reapplied.
    assert sync_env.kea.set_config.await_count == 1
    # No baseline exists, so the drift check is skipped rather than comparing
    # against a hash for a config that was never applied.
    assert sync_env.kea.get_config_hash.await_count == 0
    assert sync_env.record.call_count == 0
    # The iteration still completed, so the heartbeat advanced.
    assert sync_env.touch.called


async def test_run_once_startup_apply_failure_is_fatal(sync_env, mocker) -> None:
    """Run-once mode has no monitoring loop, so it must not exit 0 on a failed apply."""
    mocker.patch.object(cli, "STARTUP_APPLY_ATTEMPTS", 2)
    sync_env.redis.load_kea_config = AsyncMock(return_value=_BAD_CONFIG)
    sync_env.kea.set_config = AsyncMock(side_effect=_reject_only(_BAD_CONFIG))
    mocker.patch.object(cli.asyncio, "sleep", new=AsyncMock())

    with pytest.raises(KeaException, match="after 2 attempts"):
        await cli._sync_kea_configuration_async(4, 0, False, sync_env.hb)

    assert sync_env.kea.set_config.await_count == 2
    assert sync_env.record.call_count == 0
