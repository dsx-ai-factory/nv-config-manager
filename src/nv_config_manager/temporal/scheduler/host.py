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
"""Selection, supervision, and signal handling for workflow schedulers."""

import asyncio
import functools
import os
import signal
from collections.abc import Callable, Sequence

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager_workflows.registration import (
    SchedulerRegistration,
    WorkflowRegistry,
)
from nv_config_manager_workflows.registration.scheduler import WorkflowScheduler
from nv_config_manager_workflows.schedulers.backup import BackupScheduler

ENABLED_SCHEDULERS_ENV = "NVCM_ENABLED_SCHEDULERS"
BUILTIN_BACKUP_SCHEDULER_IDENTITY = BackupScheduler.scheduler_identity

logger = get_logger(__name__, category=LogCategory.TEMPORAL_WORKFLOW)


class SchedulerHostError(RuntimeError):
    """The discovered scheduler process cannot continue safely."""


class _SchedulerShutdown(Exception):
    """Request TaskGroup cancellation without treating shutdown as a failure."""


def configured_scheduler_identities() -> tuple[str, ...]:
    """Return the stable scheduler identities enabled by ``NVCM_ENABLED_SCHEDULERS``.

    Helm sets the variable from ``temporal.scheduler.schedulers``. When it is
    unset, such as when running the console script without Helm, only the
    built-in backup scheduler is enabled. When it is set but empty, no
    identities are returned and the host waits for shutdown. Otherwise the
    trimmed comma-separated identities are returned.

    Raises:
        SchedulerHostError: If an identity appears more than once.
    """
    configured = os.environ.get(ENABLED_SCHEDULERS_ENV)
    if configured is None:
        return (BUILTIN_BACKUP_SCHEDULER_IDENTITY,)

    identities = tuple(identity.strip() for identity in configured.split(",") if identity.strip())
    if len(set(identities)) != len(identities):
        raise SchedulerHostError(
            f"{ENABLED_SCHEDULERS_ENV} contains a scheduler identity more than once"
        )
    return identities


def select_scheduler_registrations(
    registry: WorkflowRegistry,
    enabled_identities: Sequence[str],
) -> tuple[SchedulerRegistration, ...]:
    """Select known schedulers in deterministic registry order."""
    registrations = tuple(registry.scheduler_registrations)
    available_identities = {registration.identity for registration in registrations}
    unknown_identities = sorted(set(enabled_identities) - available_identities)
    if unknown_identities:
        raise SchedulerHostError(
            "Unknown enabled workflow scheduler identities: " + ", ".join(unknown_identities)
        )

    enabled = set(enabled_identities)
    return tuple(registration for registration in registrations if registration.identity in enabled)


def _construct_schedulers(
    registrations: Sequence[SchedulerRegistration],
) -> tuple[WorkflowScheduler, ...]:
    """Construct every selected scheduler before any of them starts."""
    schedulers: list[WorkflowScheduler] = []
    for registration in registrations:
        identity, plugin = registration.identity, registration.plugin
        logger.info("Constructing workflow scheduler %s from plugin %s", identity, plugin)
        try:
            scheduler = registration.scheduler()
        except Exception as error:
            # Only the exception type is logged; the exception itself propagates.
            logger.error(
                "Failed to construct workflow scheduler %s from plugin %s: %s",
                identity,
                plugin,
                type(error).__name__,
            )
            raise
        logger.info("Constructed workflow scheduler %s from plugin %s", identity, plugin)
        schedulers.append(scheduler)
    return tuple(schedulers)


async def _run_scheduler(
    scheduler: WorkflowScheduler,
    registration: SchedulerRegistration,
) -> None:
    """Run one long-lived scheduler and reject an unexpected normal return."""
    identity, plugin = registration.identity, registration.plugin
    logger.info("Started workflow scheduler %s from plugin %s", identity, plugin)
    try:
        await scheduler.run()
    except asyncio.CancelledError:
        logger.info("Stopped workflow scheduler %s from plugin %s", identity, plugin)
        raise
    except Exception as error:
        # Only the exception type is logged; the exception itself propagates.
        logger.error(
            "Workflow scheduler %s from plugin %s failed: %s",
            identity,
            plugin,
            type(error).__name__,
        )
        raise
    task = asyncio.current_task()
    if task is None or not task.cancelling():
        logger.error(
            "Workflow scheduler %s from plugin %s returned unexpectedly",
            identity,
            plugin,
        )
        raise SchedulerHostError(f'Scheduler "{identity}" returned unexpectedly')
    logger.info("Stopped workflow scheduler %s from plugin %s", identity, plugin)


async def _stop_on_shutdown(shutdown: asyncio.Event) -> None:
    """Turn an orderly process shutdown into TaskGroup sibling cancellation."""
    await shutdown.wait()
    raise _SchedulerShutdown


async def run_schedulers(
    registrations: Sequence[SchedulerRegistration],
    shutdown: asyncio.Event | None = None,
) -> None:
    """Construct and concurrently run every selected scheduler."""
    if not registrations:
        logger.warning("Scheduler host enabled with no selected scheduler contributions")
        shutdown = asyncio.Event() if shutdown is None else shutdown
        await shutdown.wait()
        return

    schedulers = _construct_schedulers(registrations)
    try:
        async with asyncio.TaskGroup() as task_group:
            for registration, scheduler in zip(
                registrations,
                schedulers,
                strict=True,
            ):
                task_group.create_task(
                    _run_scheduler(scheduler, registration),
                    name=f"scheduler:{registration.identity}",
                )
            if shutdown is not None:
                task_group.create_task(
                    _stop_on_shutdown(shutdown),
                    name="scheduler-host:shutdown",
                )
    except* _SchedulerShutdown:
        pass


def _request_shutdown(shutdown: asyncio.Event, process_signal: signal.Signals) -> None:
    """Record the received process signal and request an orderly shutdown."""
    logger.info("Received signal %s, stopping workflow schedulers", process_signal.name)
    shutdown.set()


def _install_signal_handlers(
    shutdown: asyncio.Event,
    loop: asyncio.AbstractEventLoop | None = None,
) -> Callable[[], None]:
    """Install process shutdown handlers and return their cleanup callback."""
    event_loop = asyncio.get_running_loop() if loop is None else loop
    installed: list[signal.Signals] = []
    try:
        for process_signal in (signal.SIGTERM, signal.SIGINT):
            event_loop.add_signal_handler(
                process_signal,
                functools.partial(_request_shutdown, shutdown, process_signal),
            )
            installed.append(process_signal)
    except BaseException:
        for installed_signal in installed:
            event_loop.remove_signal_handler(installed_signal)
        raise

    def remove_signal_handlers() -> None:
        for installed_signal in installed:
            event_loop.remove_signal_handler(installed_signal)

    return remove_signal_handlers


async def run_scheduler_service(registrations: Sequence[SchedulerRegistration]) -> None:
    """Run discovered schedulers until they fail or process shutdown is requested."""
    shutdown = asyncio.Event()
    remove_signal_handlers = _install_signal_handlers(shutdown)
    try:
        await run_schedulers(registrations, shutdown)
    finally:
        remove_signal_handlers()


__all__ = [
    "BUILTIN_BACKUP_SCHEDULER_IDENTITY",
    "ENABLED_SCHEDULERS_ENV",
    "SchedulerHostError",
    "configured_scheduler_identities",
    "run_scheduler_service",
    "run_schedulers",
    "select_scheduler_registrations",
]
