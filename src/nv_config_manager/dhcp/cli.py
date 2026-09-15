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
"""CLI commands for DHCP ConfGen."""

# pylint: disable=too-many-arguments
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Awaitable
from traceback import format_tb
from types import TracebackType
from typing import Any

import click
from aiohttp import ClientError
from prometheus_client import start_http_server

from nv_config_manager.common.config import load_config
from nv_config_manager.common.log import (
    LogCategory,
    configure_logging,
    escape_log_newlines,
    get_logger,
)
from nv_config_manager.dcim import DCIMClient, dcim_client_session
from nv_config_manager.dhcp.heartbeat import (
    DEFAULT_HEARTBEAT_FILE,
    DEFAULT_MAX_AGE_SECONDS,
    age_is_fresh,
    heartbeat_age_seconds,
    record_successful_reconciliation,
    touch_heartbeat,
)
from nv_config_manager.dhcp.kea import KeaClient, KeaException
from nv_config_manager.dhcp.kea_dhcp_confgen import generate_config, inject_lease_db_config
from nv_config_manager.dhcp.metrics import (
    DHCP_CACHE_REFRESH_ERRORS,
    DHCP_CONFIG_HASH_MISMATCHES,
    DHCP_LAST_SUCCESSFUL_SYNC_TIMESTAMP,
    DHCP_SYNC_FAILURES,
    SyncOperation,
    SyncState,
    initialize_refresh_metrics,
    initialize_sync_metrics,
)
from nv_config_manager.dhcp.redis import RedisClient

configure_logging(service="dhcp")
logger = get_logger(__name__, category=LogCategory.DHCP)

# Port each confgen process serves its Prometheus registry on. The metrics below
# are incremented in-process, and neither command serves other HTTP traffic, so
# without an exporter here they are unreachable -- the /metrics endpoint in
# dhcp/api.py runs in a different container with its own registry.
#
# config-sync-v4 and config-refresh-v4 live in separate Deployments, so they can
# share one port. Not the 8000 used elsewhere in the repo: every container in the
# DHCP pod shares one network namespace, and kea already binds 8000 (plus 67,
# 9000, 9090 and the stork agent port). Changing this requires matching updates
# to the containerPorts in dhcp.yaml, the PodMonitor endpoints in monitoring.yaml,
# and the Prometheus ingress allow-list in network-policy.yaml;
# test_confgen_metrics_port_is_scrapeable fails if they drift apart.
CONFGEN_METRICS_PORT = 9091

# Bounded timeouts for the reconcile loop's dependency calls so a hung Redis or
# Kea request can never suspend the event loop indefinitely (which would freeze
# the heartbeat). On timeout the call raises TimeoutError, which the loop treats
# as a recoverable error: it is logged/counted but the heartbeat still advances.
# Overridable via env for operational tuning; read once at import.
REDIS_OP_TIMEOUT_SECONDS = float(os.environ.get("CONFIG_SYNC_REDIS_TIMEOUT", "10"))
KEA_OP_TIMEOUT_SECONDS = float(os.environ.get("CONFIG_SYNC_KEA_TIMEOUT", "15"))

# The startup apply retries a fixed in-memory payload, so it must be bounded: a
# config KEA deterministically rejects (config-set returns non-zero, surfacing
# as KeaException) fails identically on every attempt, and an unbounded retry
# would spin at 1Hz forever without ever re-reading Redis -- so even publishing
# a corrected config could not recover it. On exhaustion the loop hands off to
# the monitoring loop, which re-reads Redis every interval and therefore does
# pick up a correction. Sized to cover a Kea container that is slow to start.
STARTUP_APPLY_ATTEMPTS = int(os.environ.get("CONFIG_SYNC_STARTUP_APPLY_ATTEMPTS", "30"))

# Bound + redact dependency-error text so a Redis/PostgreSQL exception that
# embeds a DSN or password= assignment cannot leak credentials into logs.
_MAX_ERROR_CHARS = 300
# `*` so redis://:password@host (empty username) is redacted as well as
# postgresql://user:password@host.
#
# The password run deliberately allows `@`: a password containing one is legal
# in a DSN, and excluding `@` here would stop the match at the first one and
# redact only the leading fragment -- leaving most of the secret in the log
# while still satisfying a `secret not in text` assertion. Greedy matching
# therefore backtracks to the LAST `@`, which is the userinfo/host boundary.
# `/` and whitespace stay excluded so the run cannot escape its own DSN.
_DSN_USERINFO_RE = re.compile(r"(://[^:/@\s]*):([^/\s]+)@")
# The key may be quoted, because a rejected KEA config is JSON and reaches this
# as `"password": "..."`. The quotes are matched independently rather than as a
# balanced pair: over-redacting malformed input is harmless, whereas requiring
# symmetry lets `password": "..."` through. The value alternation takes a quoted
# string before a bare run so a secret containing spaces is consumed whole.
# Quoted values allow escaped characters (including \") so a JSON fragment
# like `"password": "a\"secret"` is consumed whole rather than stopping at
# the escaped quote and leaking the rest.
_PASSWORD_ASSIGN_RE = re.compile(
    r"""(?i)
    (["']?(?:password|passwd|pwd|secret)["']?\s*[:=]\s*)
    (?:
        "(?:\\.|[^"\\])*"
        |
        '(?:\\.|[^'\\])*'
        |
        \S+
    )
    """,
    re.VERBOSE,
)


def _redact_secrets(text: str) -> str:
    """Strip DSN userinfo and ``password=`` style assignments from ``text``."""
    text = _DSN_USERINFO_RE.sub(r"\1:<redacted>@", text)
    return _PASSWORD_ASSIGN_RE.sub(r"\1<redacted>", text)


def _safe_error_text(error: BaseException | str) -> str:
    """Return a log-safe, bounded error string with credentials stripped.

    Accepts a message as well as an exception because KEA reports a validation
    rejection as a returned error string rather than by raising.
    """
    raw = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    text = _redact_secrets(escape_log_newlines(raw))
    if len(text) > _MAX_ERROR_CHARS:
        return text[:_MAX_ERROR_CHARS] + "…"
    return text


def _config_fingerprint(config: dict[str, Any] | None) -> str:
    """Return a short, log-safe fingerprint of a KEA config.

    Used only for pre-apply Redis-content drift logs, when KEA has not yet
    hashed the new desired config. Apply decisions use KEA's native
    ``config-hash-get`` / ``config-set`` hashes. Only the truncated digest is
    ever logged, never the config body.
    """
    if config is None:
        return "none"
    digest = hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode())
    return digest.hexdigest()[:12]


def _log_sync_state(state: str, message: str, ip_version: int, **fields: Any) -> None:
    """Emit a structured reconcile-loop log line tagged with ``sync_state``.

    ``fields`` must only contain non-secret values (e.g. config hashes); never
    pass database credentials or full ``config-get`` responses. Values are
    newline-escaped to keep structured fields free of log-forging characters.
    """
    extra = {
        "sync_state": state,
        "ip_version": ip_version,
        **{key: escape_log_newlines(value) for key, value in fields.items()},
    }
    logger.info(message, extra=extra)


def _record_sync_failure(operation: str, ip_version: int, error: BaseException | str) -> None:
    """Count a reconcile/apply failure and emit a ``dependency-error`` log line.

    Increments the failure counter for ``operation`` and logs the operation and
    a redacted, bounded error string. Redis/PostgreSQL exceptions can embed a
    DSN or password; those are stripped before the record reaches the pipeline.
    """
    DHCP_SYNC_FAILURES.labels(operation=operation, ip_version=str(ip_version)).inc()
    logger.error(
        "DHCP sync dependency error during %s",
        operation,
        extra={
            "sync_state": SyncState.DEPENDENCY_ERROR,
            "operation": operation,
            "ip_version": ip_version,
            "error": _safe_error_text(error),
        },
    )


async def _track_sync_operation[T](
    operation: str,
    ip_version: int,
    awaitable: Awaitable[T],
) -> T:
    """Await ``awaitable``, recording a labeled failure before re-raising."""
    try:
        return await awaitable
    except Exception as exc:
        _record_sync_failure(operation, ip_version, exc)
        raise


def _inject_lease_db_config_tracked(config: dict[str, Any], ip_version: int) -> dict[str, Any]:
    """Inject the PostgreSQL lease-DB config, recording a ``postgres`` failure."""
    try:
        return inject_lease_db_config(config, ip_version)
    except Exception as exc:
        _record_sync_failure(SyncOperation.POSTGRES, ip_version, exc)
        raise


def _mark_verified_sync(
    ip_version: int,
    running_hash: str,
    *,
    recovered: bool,
    log: bool = True,
) -> None:
    """Record a successful verified sync: bump the gauge and log ``in-sync``.

    Call only after the running KEA hash matches the desired hash (or after
    ``config-set`` plus ``config-hash-get`` verification succeeds). The gauge
    is always refreshed so its AGE stays low while healthy; the ``in-sync``
    log line is emitted when ``log`` is true. ``recovered`` marks a post-drift
    recovery, which must be logged.
    """
    DHCP_LAST_SUCCESSFUL_SYNC_TIMESTAMP.labels(ip_version=str(ip_version)).set(time.time())
    if not log:
        return
    _log_sync_state(
        SyncState.IN_SYNC,
        "KEA DHCP configuration recovered and in sync"
        if recovered
        else "KEA DHCP configuration in sync",
        ip_version,
        running_hash=running_hash,
    )


def _start_metrics_server(process: str) -> None:
    """Expose this process's registry so the PodMonitor can reach its metrics.

    ``process`` only labels the failure log; both confgen commands bind the same
    port because they run in separate pods.
    """
    try:
        start_http_server(CONFGEN_METRICS_PORT)
    except OSError as exc:
        # A bind failure must not prevent reconciliation, so this is logged and
        # swallowed rather than raised. A second process sharing this port in one
        # pod would otherwise CrashLoop it. IPv6 would need its own chart port,
        # not a silent offset here.
        logger.error(
            "Could not start the DHCP %s metrics server; continuing without metrics export",
            process,
            extra={"port": CONFGEN_METRICS_PORT, "error": escape_log_newlines(exc)},
        )


def _set_config_path(ini_file: str) -> None:
    """Set NV_CONFIG_MANAGER_INI env var for consistent config loading."""
    os.environ["NV_CONFIG_MANAGER_INI"] = ini_file


def _exception_handler(
    exception_type: type[BaseException],
    exception: BaseException,
    traceback: TracebackType | None,
) -> None:  # pylint: disable=unused-argument
    """Log an unhandled exception with credentials stripped from the message.

    Tracked reconcile operations redact the error before counting it, but they
    re-raise, and anything that escapes ``asyncio.run`` lands here. Formatting
    the exception raw -- or handing it to ``exc_info`` -- would put the original
    Redis/PostgreSQL DSN back into the log and undo that redaction. Only stack
    frames are attached, since those carry no exception message.
    """
    logger.error(
        "%s\n%s",
        _safe_error_text(exception),
        _redact_secrets("".join(format_tb(traceback))),
    )


@click.group()
def cli() -> None:
    """DHCP CLI operations."""


async def _generate_kea_configuration_async(ip_version: int) -> dict[str, Any]:
    """Async implementation of KEA config generation."""
    async with dcim_client_session(load_config()) as dcim_client:
        return await generate_config(
            dcim_client=dcim_client,
            redis_client=None,
            version=ip_version,
        )


@cli.command()
@click.option(
    "--ini-file",
    help="NVIDIA Config Manager ini file",
    default="/etc/vault/nv-config-manager.ini",
    envvar="NV_CONFIG_MANAGER_INI",
    show_envvar=True,
)
@click.option("--ip-version", default=4, help="DHCP IP Version to generate.")
@click.option("--output-file", help="Optional output file for generated configuration.")
@click.option("--debug", is_flag=True, default=False, help="display tracebacks in error output.")
def generate_kea_configuration(
    ini_file: str,
    ip_version: int,
    output_file: str,
    debug: bool,
) -> None:
    """Generate a KEA DHCP Server Configuration."""
    _set_config_path(ini_file)
    if not debug:
        sys.excepthook = _exception_handler

    config = asyncio.run(_generate_kea_configuration_async(ip_version))
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
    else:
        click.echo(json.dumps(config, indent=4))


async def _refresh_kea_configuration_async(
    dcim_client: DCIMClient,
    kea_client: KeaClient,
    redis_client: RedisClient,
    ip_version: int,
    check: bool,
) -> bool:
    """Async implementation of configuration refresh."""
    logger.info("Generating configuration from DCIM provider data.")

    # Get current Kea config to extract architecture-specific hooks path
    try:
        current_kea_config_response = await kea_client.get_config(version=ip_version)
        current_kea_config = current_kea_config_response[0].get("arguments", {})
    except Exception as exc:
        logger.warning(f"Could not fetch current Kea config, using defaults: {exc}")
        current_kea_config = None

    config = await _track_sync_operation(
        SyncOperation.CONFIG_GENERATION,
        ip_version,
        generate_config(
            dcim_client=dcim_client,
            redis_client=redis_client,
            version=ip_version,
            kea_config=current_kea_config,
        ),
    )

    logger.info("Validating configuration against KEA API.")
    result, error = await _track_sync_operation(
        SyncOperation.CONFIG_TEST, ip_version, kea_client.test_config(config, version=ip_version)
    )
    if not result:
        # test_config reports a KEA validation rejection as (False, error)
        # instead of raising, so the tracked-operation wrapper sees a successful
        # await and the rejection has to be counted here.
        _record_sync_failure(SyncOperation.CONFIG_TEST, ip_version, str(error))
        # KEA echoes the offending config back in the rejection, so this text can
        # carry the lease-DB password. Click renders it to stderr unredacted.
        raise click.ClickException(
            f"Generated configuration is invalid: {_safe_error_text(str(error))}"
        )

    if check:
        logger.info("Generated configuration is valid.")
        return True

    logger.info("Persisting configuration to Redis.")
    await redis_client.persist_kea_config(ip_version, config)
    logger.info(f"KEA DHCP{ip_version} Configuration Refresh Complete.")
    return False


async def _refresh_loop_async(
    ip_version: int,
    check: bool,
    refresh_interval: int,
) -> None:
    """Async loop for configuration refresh."""
    config = load_config()
    kea_client = KeaClient.from_config(config)
    redis_client = RedisClient.from_config(config)

    try:
        async with dcim_client_session(config) as dcim_client:
            # Always run once
            should_exit = await _refresh_kea_configuration_async(
                dcim_client, kea_client, redis_client, ip_version, check
            )
            if should_exit or not refresh_interval:
                return

            while True:
                # Leave errors uncaught so that they get raised and restart the container
                await _refresh_kea_configuration_async(
                    dcim_client, kea_client, redis_client, ip_version, check
                )
                logger.info(f"Sleeping {refresh_interval}s...")
                await asyncio.sleep(refresh_interval)
    finally:
        await kea_client.close()
        await redis_client.close()


@cli.command()
@click.option(
    "--ini-file",
    help="NVIDIA Config Manager ini file",
    default="/etc/vault/nv-config-manager.ini",
    envvar="NV_CONFIG_MANAGER_INI",
    show_envvar=True,
)
@click.option("--ip-version", default=4, help="DHCP IP Version to generate.")
@click.option(
    "--check",
    is_flag=True,
    default=False,
    help="validate the latest configuration, but do not update.",
)
@click.option(
    "--refresh-interval",
    default=0,
    help="interval in seconds at which to run the refresh, if unset, refresh will only be run once",
)
@click.option("--debug", is_flag=True, default=False, help="display tracebacks in error output.")
def refresh_kea_configuration(
    ini_file: str,
    ip_version: int,
    check: bool,
    refresh_interval: int,
    debug: bool,
) -> None:
    """Refresh the KEA DHCP Server Configuration in Redis."""
    _set_config_path(ini_file)
    if refresh_interval and check:
        raise click.ClickException("Cannot run --check on a refresh interval.")

    if not debug:
        sys.excepthook = _exception_handler

    # config_generation / config_test failures are recorded in this process, so
    # they need an exporter here -- the config-sync-v4 registry is a different
    # pod and never sees them. Skipped for --check, a one-shot validation run
    # that exits long before any scrape.
    if not check:
        initialize_refresh_metrics(ip_version)
        _start_metrics_server("refresh")

    asyncio.run(_refresh_loop_async(ip_version, check, refresh_interval))


async def _load_kea_config_with_timeout(
    redis_client: RedisClient, ip_version: int
) -> dict[str, Any] | None:
    """Load the cached Kea config from Redis, bounded so a hung call cannot wedge the loop."""
    return await asyncio.wait_for(
        redis_client.load_kea_config(ip_version), timeout=REDIS_OP_TIMEOUT_SECONDS
    )


async def _apply_and_verify_kea_config(
    kea_client: KeaClient,
    config: dict[str, Any],
    ip_version: int,
) -> tuple[str | None, bool]:
    """Apply the desired configuration to KEA and return its hash and verification.

    KEA (2.4+) returns the SHA-256 hash of the effective configuration from
    ``config-set``. Before treating the sync as successful, the running
    configuration is confirmed against that hash via ``config-hash-get`` so a
    configuration that was rolled back or only partially applied surfaces as an
    error rather than being silently trusted.

    Returns ``(effective_hash, verified)``. ``verified`` is False only when the
    verification read itself failed, which is distinct from a hash of ``None``:
    KEA versions without ``config-hash-get`` support report no digest at all, so
    they verify trivially and drift detection degrades to a no-op for them.

    Both Kea calls are bounded so a hung control channel cannot freeze the
    heartbeat (and therefore the exec livenessProbe).
    """
    applied_hash = await _track_sync_operation(
        SyncOperation.CONFIG_SET,
        ip_version,
        asyncio.wait_for(
            kea_client.set_config(config, version=ip_version), timeout=KEA_OP_TIMEOUT_SECONDS
        ),
    )
    try:
        effective_hash = await asyncio.wait_for(
            kea_client.get_config_hash(version=ip_version), timeout=KEA_OP_TIMEOUT_SECONDS
        )
    except (KeaException, TimeoutError, ClientError) as exc:
        # config-set already succeeded, so the desired config is applied and
        # persisted -- only the verification read failed. get_config_hash
        # re-raises TimeoutError and aiohttp.ClientError (it does not wrap
        # either in KeaException), and the refresh loop already swallows both.
        # Aborting here would crash-loop the sidecar over a config that is
        # actually applied, and reapplying it on a retry would hammer Kea for a
        # config it is already running, so the apply stands as unverified: the
        # caller must not report a successful reconciliation, and the next
        # drift check reapplies and re-verifies.
        _record_sync_failure(SyncOperation.HASH_GET, ip_version, exc)
        logger.warning(
            "Could not verify the applied KEA configuration hash: %s",
            _safe_error_text(exc),
        )
        return None, False
    if applied_hash is not None and effective_hash != applied_hash:
        # KEA did not keep what config-set reported, which is drift. Counted
        # here because this raise bypasses the tracked-operation wrappers: both
        # calls above returned successfully.
        DHCP_CONFIG_HASH_MISMATCHES.labels(ip_version=str(ip_version)).inc()
        _record_sync_failure(
            SyncOperation.CONFIG_SET,
            ip_version,
            f"KEA effective hash {effective_hash} does not match "
            f"the config-set hash {applied_hash}",
        )
        raise KeaException(
            f"KEA effective configuration hash ({effective_hash}) does not match "
            f"the hash returned by config-set ({applied_hash}); "
            "the configuration was not applied cleanly."
        )
    return effective_hash, True


async def _sync_kea_configuration_async(
    ip_version: int,
    refresh_interval: int,
    debug: bool,
    heartbeat_file: str = DEFAULT_HEARTBEAT_FILE,
) -> None:
    """Async implementation of sync configuration."""
    # Connect to the KEA server running in the same pod
    ini_config = load_config()
    kea_client = KeaClient.from_config(ini_config, attached=True)
    redis_client = RedisClient.from_config(ini_config)

    try:
        # Seed the heartbeat before the first Redis read and advance it on every
        # poll below. Waiting for config to appear in Redis is legitimate
        # progress, not a wedged loop, so the probe must not fail during it --
        # otherwise a cold start with no published config restart-loops the
        # sidecar once the probe's grace period expires.
        touch_heartbeat(heartbeat_file)
        config = None
        while config is None:
            try:
                config = await _track_sync_operation(
                    SyncOperation.REDIS_READ,
                    ip_version,
                    _load_kea_config_with_timeout(redis_client, ip_version),
                )
            except Exception as exc:
                # Startup follows the same contract as the monitoring loop: an
                # unreachable or slow Redis (including the bounded-timeout
                # TimeoutError) is recoverable. Letting it propagate would exit
                # the sidecar into CrashLoopBackOff during exactly the kind of
                # dependency outage this design is meant to ride out.
                DHCP_CACHE_REFRESH_ERRORS.labels(ip_version=str(ip_version)).inc()
                logger.error(
                    "Error loading the initial KEA config from Redis: %s",
                    _safe_error_text(exc),
                )
            if config is None:
                _log_sync_state(
                    SyncState.WAITING_FOR_INITIAL_REDIS_CONFIG,
                    f"Waiting for KEA DHCP{ip_version} Configuration to be available in Redis...",
                    ip_version,
                )
                touch_heartbeat(heartbeat_file)
                await asyncio.sleep(1)

        # Inject Lease DB details after loading from Redis
        # so that secrets are not stored in the Redis cache. The lease DB is a
        # PostgreSQL dependency, so failures here are labeled ``postgres``.
        config = _inject_lease_db_config_tracked(config, ip_version)

        # Apply once Redis has a config. Kea in this pod may still be coming
        # up, so a connection error or bounded timeout must not exit the
        # sidecar -- same recoverable contract as the Redis wait above.
        _log_sync_state(
            SyncState.APPLYING,
            f"Setting initial KEA DHCPv{ip_version} Configuration from Redis.",
            ip_version,
            desired_hash=_config_fingerprint(config),
        )
        expected_hash: str | None = None
        verified = False
        applied_config: dict[str, Any] | None = None
        for attempt in range(1, STARTUP_APPLY_ATTEMPTS + 1):
            try:
                expected_hash, verified = await _apply_and_verify_kea_config(
                    kea_client, config, ip_version
                )
                applied_config = config
                break
            except Exception as exc:
                DHCP_CACHE_REFRESH_ERRORS.labels(ip_version=str(ip_version)).inc()
                logger.error(
                    "Error applying the initial KEA config (attempt %s/%s): %s",
                    attempt,
                    STARTUP_APPLY_ATTEMPTS,
                    _safe_error_text(exc),
                )
                touch_heartbeat(heartbeat_file)
                await asyncio.sleep(1)

        if applied_config is None:
            # Attempts exhausted: retrying the same payload is not going to
            # start working. Leaving applied_config None tells the monitoring
            # loop nothing is applied yet, so it re-reads Redis every interval
            # and reapplies -- a corrected config self-heals without a restart,
            # at the refresh interval instead of 1Hz.
            logger.error(
                "Giving up on the initial KEA DHCPv%s apply after %s attempts. "
                "Handing off to the monitoring loop; this pod stays unready "
                "until an applicable configuration is published.",
                ip_version,
                STARTUP_APPLY_ATTEMPTS,
            )
            if not refresh_interval:
                # Run-once mode has no monitoring loop to hand off to, so
                # falling through would exit 0 and report success for a
                # configuration that was never applied.
                raise KeaException(
                    f"Failed to apply the initial KEA DHCPv{ip_version} configuration "
                    f"after {STARTUP_APPLY_ATTEMPTS} attempts."
                )
        if verified:
            # An apply whose verification read failed is not a successful
            # reconciliation: the config is applied but unconfirmed, so the
            # marker waits for the monitoring loop to re-verify it.
            record_successful_reconciliation()
            if expected_hash is not None:
                # A KEA without config-hash-get verifies trivially with no
                # digest, so there is no running_hash to publish; the gauge
                # waits for a KEA that reports one.
                _mark_verified_sync(ip_version, expected_hash, recovered=False)
        # Seed the heartbeat immediately so the liveness probe has a fresh
        # marker before the first monitoring iteration completes.
        touch_heartbeat(heartbeat_file)

        if refresh_interval:
            logger.info(
                f"Monitoring KEA DHCPv{ip_version} Configuration for changes every {refresh_interval}s, "
                "only updates will be logged..."
            )
            # None when the startup apply never succeeded: there is no applied
            # config to use as a drift baseline, so the first published config
            # the loop reads counts as a change and gets applied.
            previous_config = applied_config
            while True:
                # Reading the desired config is separate from reconciling it. The
                # drift check below needs only previous_config/expected_hash, both
                # held in memory, so an unreachable Redis must not skip it: a Kea
                # container recycle during a Redis outage would otherwise leave
                # this pod on bootstrap config -- unready and out of the external
                # Service -- for the whole outage, with a valid desired config
                # sitting right here.
                # None means Redis did not hand us a desired config this
                # iteration (absent, unreachable, or timed out).
                published_config: dict[str, Any] | None = None
                try:
                    new_config = await _track_sync_operation(
                        SyncOperation.REDIS_READ,
                        ip_version,
                        _load_kea_config_with_timeout(redis_client, ip_version),
                    )
                    if new_config is None:
                        _log_sync_state(
                            SyncState.WAITING_FOR_INITIAL_REDIS_CONFIG,
                            "No configuration found in Redis, "
                            "waiting for configuration to be available...",
                            ip_version,
                        )
                    else:
                        published_config = _inject_lease_db_config_tracked(new_config, ip_version)
                except Exception as exc:
                    DHCP_CACHE_REFRESH_ERRORS.labels(ip_version=str(ip_version)).inc()
                    logger.error(
                        "Error loading the desired KEA config from Redis: %s",
                        _safe_error_text(exc),
                    )

                verified = False
                try:
                    if published_config is not None and published_config != previous_config:
                        desired_hash = _config_fingerprint(published_config)
                        # Two Redis snapshots differ, which means config-generation
                        # published a new desired config. That is not drift: Kea's
                        # effective hash has not been read here, so nothing is known
                        # to have diverged. Counting it as a hash mismatch would fire
                        # drift alerts on every routine config push, and reporting the
                        # previously applied hash as ``running_hash`` would assert a
                        # disagreement that was never observed. Verified drift is
                        # detected in the else branch, which does read the hash back.
                        _log_sync_state(
                            SyncState.DESIRED_CONFIG_UPDATED,
                            "New desired KEA DHCP configuration published, updating.",
                            ip_version,
                            desired_hash=desired_hash,
                            previous_desired_hash=_config_fingerprint(previous_config),
                        )
                        _log_sync_state(
                            SyncState.APPLYING,
                            "Applying updated KEA DHCP configuration.",
                            ip_version,
                            desired_hash=desired_hash,
                        )
                        expected_hash, verified = await _apply_and_verify_kea_config(
                            kea_client, published_config, ip_version
                        )
                        previous_config = published_config
                        if verified and expected_hash is not None:
                            _mark_verified_sync(ip_version, expected_hash, recovered=True)
                    elif previous_config is None:
                        # Startup never applied anything and Redis has not
                        # returned a config since, so there is no desired state
                        # to reconcile and no baseline to detect drift against.
                        # Wait for the next read rather than reapplying the
                        # payload startup already exhausted its attempts on.
                        logger.warning(
                            "No KEA DHCPv%s configuration has been applied yet; "
                            "waiting for an applicable configuration in Redis.",
                            ip_version,
                        )
                    else:
                        # KEA (e.g. the Kea container) may have restarted from its
                        # bootstrap config while this sidecar kept running. Compare
                        # KEA's effective config hash against the last applied hash
                        # and reapply on drift. previous_config/expected_hash are
                        # both in memory, so this runs whether or not Redis answered.
                        # Refresh the in-sync gauge only after that verification.
                        kea_running_hash = await _track_sync_operation(
                            SyncOperation.HASH_GET,
                            ip_version,
                            asyncio.wait_for(
                                kea_client.get_config_hash(version=ip_version),
                                timeout=KEA_OP_TIMEOUT_SECONDS,
                            ),
                        )
                        if kea_running_hash != expected_hash:
                            if expected_hash is None:
                                # The last apply could not be verified, so
                                # nothing is known to have diverged. Reapply
                                # to confirm, but do not report drift: a
                                # transient hash-read failure would otherwise
                                # fire the mismatch alert.
                                _log_sync_state(
                                    SyncState.APPLYING,
                                    "Last applied hash is unverified, reapplying "
                                    "desired KEA DHCP configuration.",
                                    ip_version,
                                    running_hash=kea_running_hash,
                                )
                            else:
                                DHCP_CONFIG_HASH_MISMATCHES.labels(ip_version=str(ip_version)).inc()
                                _log_sync_state(
                                    SyncState.DRIFT_DETECTED,
                                    "KEA DHCP configuration drift detected, updating.",
                                    ip_version,
                                    desired_hash=expected_hash,
                                    running_hash=kea_running_hash or "none",
                                )
                                logger.warning(
                                    "KEA running configuration hash (%s) does not match the "
                                    "expected hash (%s); reapplying desired configuration "
                                    "(KEA may have restarted).",
                                    kea_running_hash,
                                    expected_hash,
                                )
                                _log_sync_state(
                                    SyncState.APPLYING,
                                    "Applying updated KEA DHCP configuration.",
                                    ip_version,
                                    desired_hash=expected_hash,
                                )
                            expected_hash, verified = await _apply_and_verify_kea_config(
                                kea_client, previous_config, ip_version
                            )
                            if verified and expected_hash is not None:
                                _mark_verified_sync(ip_version, expected_hash, recovered=True)
                        elif kea_running_hash is None:
                            # Kea omitted the digest (pre-2.4). The hashes agree,
                            # so this is still a reconciled iteration, but equality
                            # of two Nones is not a verified hash match: leave the
                            # gauge untouched so age-based alerts still fire.
                            verified = True
                        else:
                            verified = True
                            _mark_verified_sync(
                                ip_version,
                                kea_running_hash if debug else "",
                                recovered=False,
                                log=debug,
                            )
                    if published_config is not None and verified:
                        # A successful reconciliation means *verified* agreement
                        # with *published* desired state (distinct from the
                        # loop-progress heartbeat below). An unverified apply, or
                        # drift repaired from the cached config while Redis is
                        # down, keeps DHCP serving but confirms neither, so the
                        # staleness gauge keeps ageing and alerting until a full
                        # reconcile succeeds.
                        record_successful_reconciliation()
                except Exception as exc:
                    # Recoverable dependency errors (PostgreSQL/Kea, including
                    # bounded-timeout TimeoutError; Redis is handled above) are
                    # logged and counted but MUST NOT stop the heartbeat: the
                    # event loop is still making progress, so kubelet should not
                    # restart us just because a dependency is temporarily
                    # unreachable.
                    DHCP_CACHE_REFRESH_ERRORS.labels(ip_version=str(ip_version)).inc()
                    logger.error(
                        "Error refreshing the KEA config: %s",
                        _safe_error_text(exc),
                    )
                finally:
                    # Heartbeat == event-loop progress. Advances after every
                    # completed attempt, success or recoverable failure. A
                    # genuinely wedged iteration never reaches here, so the file
                    # goes stale and the exec livenessProbe recycles the sidecar.
                    touch_heartbeat(heartbeat_file)
                if debug:
                    logger.info(f"Sleeping {refresh_interval}s...")
                await asyncio.sleep(refresh_interval)
    finally:
        await kea_client.close()
        await redis_client.close()


@cli.command()
@click.option(
    "--ini-file",
    help="NVIDIA Config Manager ini file",
    default="/etc/vault/nv-config-manager.ini",
    envvar="NV_CONFIG_MANAGER_INI",
    show_envvar=True,
)
@click.option("--ip-version", default=4, help="DHCP IP Version to sync.")
@click.option(
    "--refresh-interval",
    default=0,
    help="interval in seconds at which to run the refresh, if unset, refresh will only be run once",
)
@click.option(
    "--heartbeat-file",
    default=DEFAULT_HEARTBEAT_FILE,
    envvar="CONFIG_SYNC_HEARTBEAT_FILE",
    show_envvar=True,
    show_default=True,
    help="path to the liveness heartbeat file touched after each reconcile attempt.",
)
@click.option("--debug", is_flag=True, default=False, help="display tracebacks in error output.")
def sync_kea_configuration(
    ini_file: str,
    ip_version: int,
    refresh_interval: int,
    heartbeat_file: str,
    debug: bool,
) -> None:
    """Sync the Redis configuration to the KEA DHCP Server."""
    _set_config_path(ini_file)
    if not debug:
        sys.excepthook = _exception_handler

    initialize_sync_metrics(ip_version)
    _start_metrics_server("sync")

    asyncio.run(_sync_kea_configuration_async(ip_version, refresh_interval, debug, heartbeat_file))


@cli.command("check-sync-heartbeat")
@click.option(
    "--heartbeat-file",
    default=DEFAULT_HEARTBEAT_FILE,
    envvar="CONFIG_SYNC_HEARTBEAT_FILE",
    show_envvar=True,
    show_default=True,
    help="path to the liveness heartbeat file written by sync-kea-configuration.",
)
@click.option(
    "--max-age",
    "max_age_seconds",
    type=float,
    default=DEFAULT_MAX_AGE_SECONDS,
    envvar="CONFIG_SYNC_HEARTBEAT_MAX_AGE",
    show_envvar=True,
    show_default=True,
    help="maximum allowed heartbeat age in seconds before it is considered stale.",
)
def check_sync_heartbeat(heartbeat_file: str, max_age_seconds: float) -> None:
    """Liveness check for the config-sync loop.

    Exits 0 only when the heartbeat file exists and its age is below the
    threshold; exits non-zero when the heartbeat is stale or missing. Designed
    to back an exec livenessProbe so a wedged reconcile loop is recycled while a
    mere dependency outage (which still advances the heartbeat) is not.
    """
    age = heartbeat_age_seconds(heartbeat_file)
    if age is None:
        click.echo(f"heartbeat missing or unreadable: {heartbeat_file}", err=True)
        sys.exit(1)
    if not age_is_fresh(age, max_age_seconds):
        click.echo(
            f"heartbeat stale: age={age:.1f}s outside 0..{max_age_seconds:.1f}s ({heartbeat_file})",
            err=True,
        )
        sys.exit(1)
    click.echo(f"heartbeat ok: age={age:.1f}s <= max-age={max_age_seconds:.1f}s")


def main() -> None:
    """CLI entrypoint."""
    cli()


if __name__ == "__main__":
    main()
