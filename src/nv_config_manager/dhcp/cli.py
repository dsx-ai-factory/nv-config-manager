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
from prometheus_client import start_http_server

from nv_config_manager.common.config import load_config
from nv_config_manager.common.log import (
    LogCategory,
    configure_logging,
    escape_log_newlines,
    get_logger,
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
from nv_config_manager.dhcp.nautobot import NautobotClient
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

# Bound + redact dependency-error text so a Redis/PostgreSQL exception that
# embeds a DSN or password= assignment cannot leak credentials into logs.
_MAX_ERROR_CHARS = 300
# `*` so redis://:password@host (empty username) is redacted as well as
# postgresql://user:password@host.
_DSN_USERINFO_RE = re.compile(r"(://[^:/@\s]*):([^@/\s]+)@")
_PASSWORD_ASSIGN_RE = re.compile(r"(?i)(password|passwd|pwd|secret)\s*[:=]\s*\S+")


def _redact_secrets(text: str) -> str:
    """Strip DSN userinfo and ``password=`` style assignments from ``text``."""
    text = _DSN_USERINFO_RE.sub(r"\1:<redacted>@", text)
    return _PASSWORD_ASSIGN_RE.sub(r"\1=<redacted>", text)


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
    async with NautobotClient.from_config(load_config()) as nautobot_client:
        return await generate_config(
            nautobot_client=nautobot_client,
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
    nautobot_client: NautobotClient,
    kea_client: KeaClient,
    redis_client: RedisClient,
    ip_version: int,
    check: bool,
) -> bool:
    """Async implementation of configuration refresh."""
    logger.info("Generating configuration from nautobot data.")

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
            nautobot_client=nautobot_client,
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
        raise click.ClickException(f"Generated configuration is invalid: {error}")

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
        async with NautobotClient.from_config(config) as nautobot_client:
            # Always run once
            should_exit = await _refresh_kea_configuration_async(
                nautobot_client, kea_client, redis_client, ip_version, check
            )
            if should_exit or not refresh_interval:
                return

            while True:
                # Leave errors uncaught so that they get raised and restart the container
                await _refresh_kea_configuration_async(
                    nautobot_client, kea_client, redis_client, ip_version, check
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


async def _apply_and_verify_kea_config(
    kea_client: KeaClient,
    config: dict[str, Any],
    ip_version: int,
) -> str | None:
    """Apply the desired configuration to KEA and return its verified hash.

    KEA (2.4+) returns the SHA-256 hash of the effective configuration from
    ``config-set``. Before treating the sync as successful, the running
    configuration is confirmed against that hash via ``config-hash-get`` so a
    configuration that was rolled back or only partially applied surfaces as an
    error rather than being silently trusted. The verified effective hash is
    returned to track for subsequent drift detection.
    """
    applied_hash = await _track_sync_operation(
        SyncOperation.CONFIG_SET, ip_version, kea_client.set_config(config, version=ip_version)
    )
    try:
        effective_hash = await kea_client.get_config_hash(version=ip_version)
    except (KeaException, TimeoutError) as exc:
        # config-set already succeeded, so the desired config is applied and
        # persisted -- only the verification read failed. get_config_hash
        # re-raises TimeoutError (it does not wrap it in KeaException), and the
        # refresh loop already swallows both. Aborting here would crash-loop
        # the sidecar over a config that is actually applied. Returning None
        # leaves the next drift check to reapply and re-verify.
        _record_sync_failure(SyncOperation.HASH_GET, ip_version, exc)
        logger.warning(
            "Could not verify the applied KEA configuration hash: %s",
            _safe_error_text(exc),
        )
        return None
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
    return effective_hash


async def _sync_kea_configuration_async(
    ip_version: int,
    refresh_interval: int,
    debug: bool,
) -> None:
    """Async implementation of sync configuration."""
    # Connect to the KEA server running in the same pod
    ini_config = load_config()
    kea_client = KeaClient.from_config(ini_config, attached=True)
    redis_client = RedisClient.from_config(ini_config)

    try:
        config = await _track_sync_operation(
            SyncOperation.REDIS_READ, ip_version, redis_client.load_kea_config(ip_version)
        )
        while config is None:
            _log_sync_state(
                SyncState.WAITING_FOR_INITIAL_REDIS_CONFIG,
                f"Waiting for KEA DHCP{ip_version} Configuration to be available in Redis...",
                ip_version,
            )
            await asyncio.sleep(1)
            config = await _track_sync_operation(
                SyncOperation.REDIS_READ, ip_version, redis_client.load_kea_config(ip_version)
            )

        # Inject Lease DB details after loading from Redis
        # so that secrets are not stored in the Redis cache. The lease DB is a
        # PostgreSQL dependency, so failures here are labeled ``postgres``.
        config = _inject_lease_db_config_tracked(config, ip_version)

        # Run once. The startup path always applies the desired Redis config and
        # captures a fresh effective hash, which keeps a config-sync restart safe.
        _log_sync_state(
            SyncState.APPLYING,
            f"Setting initial KEA DHCPv{ip_version} Configuration from Redis.",
            ip_version,
            desired_hash=_config_fingerprint(config),
        )
        expected_hash = await _apply_and_verify_kea_config(kea_client, config, ip_version)
        if expected_hash is not None:
            _mark_verified_sync(ip_version, expected_hash, recovered=False)

        if refresh_interval:
            logger.info(
                f"Monitoring KEA DHCPv{ip_version} Configuration for changes every {refresh_interval}s, "
                "only updates will be logged..."
            )
            previous_config = config
            while True:
                try:
                    new_config = await _track_sync_operation(
                        SyncOperation.REDIS_READ,
                        ip_version,
                        redis_client.load_kea_config(ip_version),
                    )
                    if new_config is None:
                        _log_sync_state(
                            SyncState.WAITING_FOR_INITIAL_REDIS_CONFIG,
                            "No configuration found in Redis, "
                            "waiting for configuration to be available...",
                            ip_version,
                        )
                        await asyncio.sleep(refresh_interval)
                        continue
                    new_config = _inject_lease_db_config_tracked(new_config, ip_version)
                    if new_config != previous_config:
                        desired_hash = _config_fingerprint(new_config)
                        running_hash = expected_hash or "none"
                        DHCP_CONFIG_HASH_MISMATCHES.labels(ip_version=str(ip_version)).inc()
                        _log_sync_state(
                            SyncState.DRIFT_DETECTED,
                            "KEA DHCP configuration drift detected, updating.",
                            ip_version,
                            desired_hash=desired_hash,
                            running_hash=running_hash,
                        )
                        _log_sync_state(
                            SyncState.APPLYING,
                            "Applying updated KEA DHCP configuration.",
                            ip_version,
                            desired_hash=desired_hash,
                        )
                        expected_hash = await _apply_and_verify_kea_config(
                            kea_client, new_config, ip_version
                        )
                        previous_config = new_config
                        if expected_hash is not None:
                            _mark_verified_sync(ip_version, expected_hash, recovered=True)
                    else:
                        # Redis is unchanged, but KEA (e.g. the Kea container) may
                        # have restarted from its bootstrap config while this
                        # sidecar kept running. Compare KEA's effective config hash
                        # against the last applied hash and reapply on drift.
                        # Refresh the in-sync gauge only after that verification.
                        try:
                            kea_running_hash = await kea_client.get_config_hash(version=ip_version)
                        except Exception as exc:
                            _record_sync_failure(SyncOperation.HASH_GET, ip_version, exc)
                            raise
                        if kea_running_hash != expected_hash:
                            DHCP_CONFIG_HASH_MISMATCHES.labels(ip_version=str(ip_version)).inc()
                            _log_sync_state(
                                SyncState.DRIFT_DETECTED,
                                "KEA DHCP configuration drift detected, updating.",
                                ip_version,
                                desired_hash=expected_hash or "none",
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
                                desired_hash=expected_hash or "none",
                            )
                            expected_hash = await _apply_and_verify_kea_config(
                                kea_client, previous_config, ip_version
                            )
                            if expected_hash is not None:
                                _mark_verified_sync(ip_version, expected_hash, recovered=True)
                        elif kea_running_hash is None:
                            # Kea omitted the digest (pre-2.4). Equality of two
                            # Nones is not a verified hash match; leave the
                            # gauge untouched so age-based alerts still fire.
                            pass
                        else:
                            _mark_verified_sync(
                                ip_version,
                                kea_running_hash if debug else "",
                                recovered=False,
                                log=debug,
                            )
                except Exception as exc:
                    DHCP_CACHE_REFRESH_ERRORS.labels(ip_version=str(ip_version)).inc()
                    logger.error(
                        "Error refreshing the KEA config: %s",
                        _safe_error_text(exc),
                    )
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
@click.option("--debug", is_flag=True, default=False, help="display tracebacks in error output.")
def sync_kea_configuration(
    ini_file: str,
    ip_version: int,
    refresh_interval: int,
    debug: bool,
) -> None:
    """Sync the Redis configuration to the KEA DHCP Server."""
    _set_config_path(ini_file)
    if not debug:
        sys.excepthook = _exception_handler

    initialize_sync_metrics(ip_version)
    _start_metrics_server("sync")

    asyncio.run(_sync_kea_configuration_async(ip_version, refresh_interval, debug))


def main() -> None:
    """CLI entrypoint."""
    cli()


if __name__ == "__main__":
    main()
