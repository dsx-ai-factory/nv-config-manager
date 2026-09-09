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
import json
import os
import sys
from types import TracebackType
from typing import Any

import click

from nv_config_manager.common.config import load_config
from nv_config_manager.common.log import LogCategory, configure_logging, get_logger
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
from nv_config_manager.dhcp.metrics import DHCP_CACHE_REFRESH_ERRORS
from nv_config_manager.dhcp.redis import RedisClient

configure_logging(service="dhcp")
logger = get_logger(__name__, category=LogCategory.DHCP)

# Bounded timeouts for the reconcile loop's dependency calls so a hung Redis or
# Kea request can never suspend the event loop indefinitely (which would freeze
# the heartbeat). On timeout the call raises TimeoutError, which the loop treats
# as a recoverable error: it is logged/counted but the heartbeat still advances.
# Overridable via env for operational tuning; read once at import.
REDIS_OP_TIMEOUT_SECONDS = float(os.environ.get("CONFIG_SYNC_REDIS_TIMEOUT", "10"))
KEA_OP_TIMEOUT_SECONDS = float(os.environ.get("CONFIG_SYNC_KEA_TIMEOUT", "15"))


def _set_config_path(ini_file: str) -> None:
    """Set NV_CONFIG_MANAGER_INI env var for consistent config loading."""
    os.environ["NV_CONFIG_MANAGER_INI"] = ini_file


def _exception_handler(
    exception_type: type[BaseException],
    exception: BaseException,
    traceback: TracebackType | None,
) -> None:  # pylint: disable=unused-argument
    logger.error(
        "%s: %s",
        exception_type.__name__,
        exception,
        exc_info=(exception_type, exception, traceback),
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

    config = await generate_config(
        dcim_client=dcim_client,
        redis_client=redis_client,
        version=ip_version,
        kea_config=current_kea_config,
    )

    logger.info("Validating configuration against KEA API.")
    result, error = await kea_client.test_config(config, version=ip_version)
    if check:
        logger.info("Generated configuration is valid.")
        return True

    if not result:
        raise click.ClickException(f"Generated configuration is invalid: {error}")
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
) -> str | None:
    """Apply the desired configuration to KEA and return its verified hash.

    KEA (2.4+) returns the SHA-256 hash of the effective configuration from
    ``config-set``. Before treating the sync as successful, the running
    configuration is confirmed against that hash via ``config-hash-get`` so a
    configuration that was rolled back or only partially applied surfaces as an
    error rather than being silently trusted. The verified effective hash is
    returned to track for subsequent drift detection.

    Both Kea calls are bounded so a hung control channel cannot freeze the
    heartbeat (and therefore the exec livenessProbe).
    """
    applied_hash = await asyncio.wait_for(
        kea_client.set_config(config, version=ip_version), timeout=KEA_OP_TIMEOUT_SECONDS
    )
    try:
        effective_hash = await asyncio.wait_for(
            kea_client.get_config_hash(version=ip_version), timeout=KEA_OP_TIMEOUT_SECONDS
        )
    except (KeaException, TimeoutError) as exc:
        # config-set already succeeded, so the desired config is applied and
        # persisted -- only the verification read failed. get_config_hash
        # re-raises TimeoutError (it does not wrap it in KeaException), and the
        # refresh loop already swallows both. Aborting here would crash-loop
        # the sidecar over a config that is actually applied. Returning None
        # leaves the next drift check to reapply and re-verify.
        logger.warning(f"Could not verify the applied KEA configuration hash: {exc}")
        return None
    if applied_hash is not None and effective_hash != applied_hash:
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
                config = await _load_kea_config_with_timeout(redis_client, ip_version)
            except Exception as exc:
                # Startup follows the same contract as the monitoring loop: an
                # unreachable or slow Redis (including the bounded-timeout
                # TimeoutError) is recoverable. Letting it propagate would exit
                # the sidecar into CrashLoopBackOff during exactly the kind of
                # dependency outage this design is meant to ride out.
                DHCP_CACHE_REFRESH_ERRORS.labels(ip_version=str(ip_version)).inc()
                logger.error(f"Error loading the initial KEA config from Redis: {exc}")
            if config is None:
                logger.info(
                    f"Waiting for KEA DHCP{ip_version} Configuration to be available in Redis..."
                )
                touch_heartbeat(heartbeat_file)
                await asyncio.sleep(1)

        # Inject Lease DB details after loading from Redis
        # so that secrets are not stored in the Redis cache
        config = inject_lease_db_config(config, ip_version)

        # Run once. The startup path always applies the desired Redis config and
        # captures a fresh effective hash, which keeps a config-sync restart safe.
        logger.info(f"Setting initial KEA DHCPv{ip_version} Configuration from Redis.")
        expected_hash = await _apply_and_verify_kea_config(kea_client, config, ip_version)
        record_successful_reconciliation()
        # Seed the heartbeat immediately so the liveness probe has a fresh
        # marker before the first monitoring iteration completes.
        touch_heartbeat(heartbeat_file)

        if refresh_interval:
            logger.info(
                f"Monitoring KEA DHCPv{ip_version} Configuration for changes every {refresh_interval}s, "
                "only updates will be logged..."
            )
            previous_config = config
            while True:
                try:
                    new_config = await _load_kea_config_with_timeout(redis_client, ip_version)
                    if new_config is None:
                        logger.info(
                            "No configuration found in Redis, waiting for configuration to be available..."
                        )
                        await asyncio.sleep(refresh_interval)
                        continue
                    new_config = inject_lease_db_config(new_config, ip_version)
                    if new_config != previous_config:
                        logger.info("Configuration changed, updating KEA DHCP Configuration.")
                        expected_hash = await _apply_and_verify_kea_config(
                            kea_client, new_config, ip_version
                        )
                        previous_config = new_config
                        record_successful_reconciliation()
                    else:
                        # Redis is unchanged, but KEA (e.g. the Kea container) may
                        # have restarted from its bootstrap config while this
                        # sidecar kept running. Compare KEA's effective config hash
                        # against the last applied hash and reapply on drift.
                        running_hash = await asyncio.wait_for(
                            kea_client.get_config_hash(version=ip_version),
                            timeout=KEA_OP_TIMEOUT_SECONDS,
                        )
                        if running_hash != expected_hash:
                            logger.warning(
                                "KEA running configuration hash (%s) does not match the "
                                "expected hash (%s); reapplying desired configuration "
                                "(KEA may have restarted).",
                                running_hash,
                                expected_hash,
                            )
                            expected_hash = await _apply_and_verify_kea_config(
                                kea_client, previous_config, ip_version
                            )
                        elif debug:
                            logger.info("No configuration changes detected.")
                        # Only a completed reconcile with config present counts
                        # as a *successful* reconciliation (distinct from the
                        # loop-progress heartbeat below).
                        record_successful_reconciliation()
                except Exception as exc:
                    # Recoverable dependency errors (Redis/PostgreSQL/Kea,
                    # including bounded-timeout TimeoutError) are logged and
                    # counted but MUST NOT stop the heartbeat: the event loop
                    # is still making progress, so kubelet should not restart us
                    # just because a dependency is temporarily unreachable.
                    DHCP_CACHE_REFRESH_ERRORS.labels(ip_version=str(ip_version)).inc()
                    logger.error(f"Error refreshing the KEA config: {exc}")
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
