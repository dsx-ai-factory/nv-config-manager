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
import asyncio
import logging
import signal
from typing import Any, cast

import pytest

from nv_config_manager.common.log import LogCategory
from nv_config_manager.temporal.scheduler import host
from nv_config_manager_workflows.registration.registry import WorkflowRegistry
from nv_config_manager_workflows.schedulers import runtime as scheduler_runtime
from nv_config_manager_workflows.schedulers.backup import (
    BackupScheduler as CanonicalBackupScheduler,
)
from tests.temporal.scheduler.helpers import (
    SECRET_SENTINEL,
    assert_sentinel_absent,
    host_messages,
    registration,
    secret_scheduler_runtime,
)


def test_builtin_backup_identity_is_derived_from_the_canonical_scheduler() -> None:
    """The host default stays in lockstep with the scheduler's stable identity."""
    assert (
        host.BUILTIN_BACKUP_SCHEDULER_IDENTITY
        == CanonicalBackupScheduler.scheduler_identity
        == "builtin.backup"
    )


@pytest.mark.usefixtures("non_nautobot_config", "dcim_provider_must_not_be_consulted")
def test_absent_scheduler_selection_enables_builtin_backup_for_any_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent selection runs the built-in backup scheduler, as main did.

    The host is provider-neutral: only the Helm chart restricts the default
    scheduler Deployment to Nautobot, so a direct run with a non-Nautobot
    configuration still selects ``builtin.backup`` without reading the provider.
    """
    monkeypatch.delenv(host.ENABLED_SCHEDULERS_ENV, raising=False)
    backup_registration = registration(
        host.BUILTIN_BACKUP_SCHEDULER_IDENTITY,
        CanonicalBackupScheduler,
        plugin="builtin",
    )
    registry = WorkflowRegistry(scheduler_registrations=(backup_registration,))

    enabled = host.configured_scheduler_identities()

    assert enabled == (host.BUILTIN_BACKUP_SCHEDULER_IDENTITY,)
    assert host.select_scheduler_registrations(registry, enabled) == (backup_registration,)


def test_empty_scheduler_selection_enables_no_schedulers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(host.ENABLED_SCHEDULERS_ENV, "")

    assert host.configured_scheduler_identities() == ()


def test_explicit_scheduler_selection_is_parsed_and_trimmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(host.ENABLED_SCHEDULERS_ENV, " fixture.cleanup, builtin.backup ")

    assert host.configured_scheduler_identities() == (
        "fixture.cleanup",
        "builtin.backup",
    )


def test_duplicate_scheduler_selection_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(host.ENABLED_SCHEDULERS_ENV, "fixture.cleanup,fixture.cleanup")

    with pytest.raises(host.SchedulerHostError, match="more than once"):
        host.configured_scheduler_identities()


def test_scheduler_selection_uses_registry_order_and_allows_provider_neutral_plugins() -> None:
    class FirstScheduler:
        async def run(self) -> None: ...

    class SecondScheduler:
        async def run(self) -> None: ...

    first = registration("alpha.first", FirstScheduler, plugin="alpha")
    second = registration("beta.second", SecondScheduler, plugin="beta")
    registry = WorkflowRegistry(scheduler_registrations=(first, second))

    selected = host.select_scheduler_registrations(registry, ("beta.second", "alpha.first"))

    assert selected == (first, second)


def test_unknown_scheduler_selection_is_rejected() -> None:
    registry = WorkflowRegistry()

    with pytest.raises(host.SchedulerHostError, match="unknown.scheduler"):
        host.select_scheduler_registrations(registry, ("unknown.scheduler",))


@pytest.mark.usefixtures("non_nautobot_config", "dcim_provider_must_not_be_consulted")
def test_explicit_builtin_backup_selection_is_kept_for_any_provider(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FixtureScheduler:
        async def run(self) -> None: ...

    backup_registration = registration(
        host.BUILTIN_BACKUP_SCHEDULER_IDENTITY,
        CanonicalBackupScheduler,
        plugin="builtin",
    )
    fixture_registration = registration("fixture.cleanup", FixtureScheduler)
    registry = WorkflowRegistry(
        scheduler_registrations=(backup_registration, fixture_registration),
    )

    with caplog.at_level(logging.DEBUG, logger=host.logger.name):
        selected = host.select_scheduler_registrations(
            registry,
            ("fixture.cleanup", host.BUILTIN_BACKUP_SCHEDULER_IDENTITY),
        )

    assert selected == (backup_registration, fixture_registration)
    assert host_messages(caplog) == []


@pytest.mark.asyncio
async def test_run_schedulers_runs_every_registered_scheduler_concurrently() -> None:
    both_started = asyncio.Event()
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    stopped: set[str] = set()

    async def wait_for_both(name: str, own_event: asyncio.Event) -> None:
        own_event.set()
        if first_started.is_set() and second_started.is_set():
            both_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.add(name)

    class FirstScheduler:
        async def run(self) -> None:
            await wait_for_both("first", first_started)

    class SecondScheduler:
        async def run(self) -> None:
            await wait_for_both("second", second_started)

    registrations = (
        registration("fixture.first", FirstScheduler),
        registration("fixture.second", SecondScheduler),
    )
    host_task = asyncio.create_task(host.run_schedulers(registrations))

    await asyncio.wait_for(both_started.wait(), timeout=1)
    host_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await host_task
    assert stopped == {"first", "second"}


@pytest.mark.asyncio
async def test_run_schedulers_cancels_siblings_and_propagates_failure() -> None:
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()

    class FailingScheduler:
        async def run(self) -> None:
            await sibling_started.wait()
            raise RuntimeError("scheduler failed")

    class SiblingScheduler:
        async def run(self) -> None:
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                sibling_cancelled.set()

    registrations = (
        registration("fixture.failing", FailingScheduler),
        registration("fixture.sibling", SiblingScheduler),
    )

    with pytest.raises(ExceptionGroup) as exc_info:
        await host.run_schedulers(registrations)

    assert any(
        isinstance(error, RuntimeError) and str(error) == "scheduler failed"
        for error in exc_info.value.exceptions
    )
    assert sibling_cancelled.is_set()


@pytest.mark.asyncio
async def test_run_schedulers_with_no_selection_waits_for_shutdown() -> None:
    shutdown = asyncio.Event()
    host_task = asyncio.create_task(host.run_schedulers((), shutdown))
    await asyncio.sleep(0)

    assert not host_task.done()
    shutdown.set()
    await asyncio.wait_for(host_task, timeout=1)


@pytest.mark.asyncio
async def test_run_schedulers_constructs_every_scheduler_before_starting_any() -> None:
    events: list[str] = []

    class FirstScheduler:
        def __init__(self) -> None:
            events.append("first-constructed")

        async def run(self) -> None:
            events.append("first-started")

    class BrokenScheduler:
        def __init__(self) -> None:
            events.append("broken-constructed")
            raise RuntimeError("construction failed")

        async def run(self) -> None: ...

    registrations = (
        registration("fixture.first", FirstScheduler),
        registration("fixture.broken", BrokenScheduler),
    )

    with pytest.raises(RuntimeError, match="construction failed"):
        await host.run_schedulers(registrations)

    assert events == ["first-constructed", "broken-constructed"]


@pytest.mark.asyncio
async def test_run_schedulers_treats_a_normal_return_as_failure() -> None:
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()

    class ReturningScheduler:
        async def run(self) -> None:
            await sibling_started.wait()

    class SiblingScheduler:
        async def run(self) -> None:
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                sibling_cancelled.set()

    registrations = (
        registration("fixture.returning", ReturningScheduler),
        registration("fixture.sibling", SiblingScheduler),
    )

    with pytest.raises(ExceptionGroup) as exc_info:
        await host.run_schedulers(registrations)

    assert any(
        isinstance(error, host.SchedulerHostError) and "returned unexpectedly" in str(error)
        for error in exc_info.value.exceptions
    )
    assert sibling_cancelled.is_set()


@pytest.mark.asyncio
async def test_shutdown_request_cancels_and_awaits_every_scheduler() -> None:
    started = asyncio.Event()
    cleaned_up: set[str] = set()
    shutdown = asyncio.Event()

    async def wait_for_shutdown(name: str) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.add(name)

    class FirstScheduler:
        async def run(self) -> None:
            await wait_for_shutdown("first")

    class SecondScheduler:
        async def run(self) -> None:
            await wait_for_shutdown("second")

    registrations = (
        registration("fixture.first", FirstScheduler),
        registration("fixture.second", SecondScheduler),
    )
    task = asyncio.create_task(host.run_schedulers(registrations, shutdown))
    await started.wait()
    await asyncio.sleep(0)

    shutdown.set()
    await asyncio.wait_for(task, timeout=1)

    assert cleaned_up == {"first", "second"}


def test_signal_handlers_request_shutdown_and_can_be_removed() -> None:
    callbacks: dict[signal.Signals, Any] = {}
    removed: list[signal.Signals] = []

    class FakeLoop:
        def add_signal_handler(self, sig: signal.Signals, callback: Any) -> None:
            callbacks[sig] = callback

        def remove_signal_handler(self, sig: signal.Signals) -> bool:
            removed.append(sig)
            return True

    shutdown = asyncio.Event()
    remove_handlers = host._install_signal_handlers(
        shutdown,
        cast(asyncio.AbstractEventLoop, FakeLoop()),
    )

    callbacks[signal.SIGTERM]()
    remove_handlers()

    assert shutdown.is_set()
    assert set(callbacks) == {signal.SIGTERM, signal.SIGINT}
    assert removed == [signal.SIGTERM, signal.SIGINT]


async def test_run_schedulers_logs_lifecycle_with_identity_and_plugin(
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = asyncio.Event()
    shutdown = asyncio.Event()

    class FirstScheduler:
        async def run(self) -> None:
            await asyncio.Event().wait()

    class SecondScheduler:
        async def run(self) -> None:
            started.set()
            await asyncio.Event().wait()

    registrations = (
        registration("alpha.first", FirstScheduler, plugin="alpha"),
        registration("beta.second", SecondScheduler, plugin="beta"),
    )

    with caplog.at_level(logging.INFO, logger=host.logger.name):
        task = asyncio.create_task(host.run_schedulers(registrations, shutdown))
        await asyncio.wait_for(started.wait(), timeout=1)
        shutdown.set()
        await asyncio.wait_for(task, timeout=1)

    messages = host_messages(caplog)
    for identity, plugin in (("alpha.first", "alpha"), ("beta.second", "beta")):
        for template in (
            "Constructing workflow scheduler {} from plugin {}",
            "Constructed workflow scheduler {} from plugin {}",
            "Started workflow scheduler {} from plugin {}",
            "Stopped workflow scheduler {} from plugin {}",
        ):
            assert (logging.INFO, template.format(identity, plugin)) in messages
    assert all(level == logging.INFO for level, _ in messages)
    host_records = [record for record in caplog.records if record.name == host.logger.name]
    assert host_records
    assert {getattr(record, "category", None) for record in host_records} == {
        LogCategory.TEMPORAL_WORKFLOW
    }


async def test_run_schedulers_logs_scheduler_failure_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sibling_started = asyncio.Event()

    class FailingScheduler:
        async def run(self) -> None:
            await sibling_started.wait()
            raise RuntimeError("scheduler failed")

    class SiblingScheduler:
        async def run(self) -> None:
            sibling_started.set()
            await asyncio.Event().wait()

    registrations = (
        registration("fixture.failing", FailingScheduler, plugin="failing-plugin"),
        registration("fixture.sibling", SiblingScheduler, plugin="sibling-plugin"),
    )

    with caplog.at_level(logging.INFO, logger=host.logger.name):
        with pytest.raises(ExceptionGroup):
            await host.run_schedulers(registrations)

    messages = host_messages(caplog)
    assert [message for level, message in messages if level >= logging.ERROR] == [
        "Workflow scheduler fixture.failing from plugin failing-plugin failed: RuntimeError"
    ]
    assert (
        logging.INFO,
        "Stopped workflow scheduler fixture.sibling from plugin sibling-plugin",
    ) in messages


async def test_run_schedulers_logs_unexpected_normal_return(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class ReturningScheduler:
        async def run(self) -> None:
            return None

    registrations = (
        registration("fixture.returning", ReturningScheduler, plugin="returning-plugin"),
    )

    with caplog.at_level(logging.INFO, logger=host.logger.name):
        with pytest.raises(ExceptionGroup) as exc_info:
            await host.run_schedulers(registrations)

    assert exc_info.group_contains(host.SchedulerHostError, match="returned unexpectedly")
    assert [message for level, message in host_messages(caplog) if level >= logging.ERROR] == [
        "Workflow scheduler fixture.returning from plugin returning-plugin returned unexpectedly"
    ]


async def test_run_schedulers_logs_construction_failure_before_any_start(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FirstScheduler:
        async def run(self) -> None: ...

    class BrokenScheduler:
        def __init__(self) -> None:
            raise RuntimeError("construction failed")

        async def run(self) -> None: ...

    registrations = (
        registration("fixture.first", FirstScheduler),
        registration("fixture.broken", BrokenScheduler, plugin="broken-plugin"),
    )

    with caplog.at_level(logging.INFO, logger=host.logger.name):
        with pytest.raises(RuntimeError, match="construction failed"):
            await host.run_schedulers(registrations)

    assert host_messages(caplog) == [
        (logging.INFO, "Constructing workflow scheduler fixture.first from plugin fixture-plugin"),
        (logging.INFO, "Constructed workflow scheduler fixture.first from plugin fixture-plugin"),
        (logging.INFO, "Constructing workflow scheduler fixture.broken from plugin broken-plugin"),
        (
            logging.ERROR,
            "Failed to construct workflow scheduler fixture.broken from plugin broken-plugin: "
            "RuntimeError",
        ),
    ]


@pytest.mark.parametrize("process_signal", [signal.SIGTERM, signal.SIGINT])
def test_signal_handler_logs_received_signal_and_requests_shutdown(
    caplog: pytest.LogCaptureFixture,
    process_signal: signal.Signals,
) -> None:
    callbacks: dict[signal.Signals, Any] = {}

    class FakeLoop:
        def add_signal_handler(self, sig: signal.Signals, callback: Any) -> None:
            callbacks[sig] = callback

        def remove_signal_handler(self, sig: signal.Signals) -> bool:
            return True

    shutdown = asyncio.Event()
    host._install_signal_handlers(shutdown, cast(asyncio.AbstractEventLoop, FakeLoop()))

    with caplog.at_level(logging.INFO, logger=host.logger.name):
        callbacks[process_signal]()

    assert shutdown.is_set()
    assert host_messages(caplog) == [
        (logging.INFO, f"Received signal {process_signal.name}, stopping workflow schedulers")
    ]


@pytest.mark.parametrize("error", [RuntimeError, NotImplementedError])
def test_signal_handler_install_failure_removes_already_installed_handlers(
    error: type[Exception],
) -> None:
    installed: list[signal.Signals] = []
    removed: list[signal.Signals] = []

    class FakeLoop:
        def add_signal_handler(self, sig: signal.Signals, callback: Any) -> None:
            if sig == signal.SIGINT:
                raise error("cannot install handler")
            installed.append(sig)

        def remove_signal_handler(self, sig: signal.Signals) -> bool:
            removed.append(sig)
            return True

    with pytest.raises(error, match="cannot install handler"):
        host._install_signal_handlers(
            asyncio.Event(),
            cast(asyncio.AbstractEventLoop, FakeLoop()),
        )

    assert installed == [signal.SIGTERM]
    assert removed == [signal.SIGTERM]


@pytest.mark.parametrize("scheduler_error", [None, RuntimeError("scheduler failed")])
async def test_run_scheduler_service_removes_signal_handlers_on_exit(
    monkeypatch: pytest.MonkeyPatch,
    scheduler_error: Exception | None,
) -> None:
    cleanup_calls: list[None] = []

    def install_signal_handlers(shutdown: asyncio.Event) -> Any:
        return lambda: cleanup_calls.append(None)

    async def run_schedulers(registrations: Any, shutdown: asyncio.Event) -> None:
        if scheduler_error is not None:
            raise scheduler_error

    monkeypatch.setattr(host, "_install_signal_handlers", install_signal_handlers)
    monkeypatch.setattr(host, "run_schedulers", run_schedulers)

    if scheduler_error is None:
        await host.run_scheduler_service(())
    else:
        with pytest.raises(RuntimeError, match="scheduler failed"):
            await host.run_scheduler_service(())

    assert cleanup_calls == [None]


async def test_scheduler_that_swallows_cancellation_during_shutdown_stops_cleanly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = asyncio.Event()
    shutdown = asyncio.Event()

    class SwallowingScheduler:
        async def run(self) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return

    registrations = (registration("fixture.swallowing", SwallowingScheduler, plugin="swallow"),)

    with caplog.at_level(logging.INFO, logger=host.logger.name):
        task = asyncio.create_task(host.run_schedulers(registrations, shutdown))
        await asyncio.wait_for(started.wait(), timeout=1)
        shutdown.set()
        await asyncio.wait_for(task, timeout=1)

    messages = host_messages(caplog)
    assert (logging.INFO, "Started workflow scheduler fixture.swallowing from plugin swallow") in (
        messages
    )
    assert (logging.INFO, "Stopped workflow scheduler fixture.swallowing from plugin swallow") in (
        messages
    )
    assert [message for level, message in messages if level >= logging.ERROR] == []


@pytest.mark.usefixtures("secret_config", "unset_scheduler_runtimes")
def test_scheduler_host_error_paths_are_credential_free(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Guard that host and runtime startup errors never echo credential values.

    The service INI credentials and runtime provider closures hold the sentinel;
    host-raised exceptions and captured logs must stay free of it, and a plugin's
    construction error text must stay out of host logs. Scheduler identities are
    configuration, not secrets, so the unknown-identity error may echo them.
    """
    errors: list[BaseException] = []

    with caplog.at_level(logging.DEBUG):
        monkeypatch.setenv(host.ENABLED_SCHEDULERS_ENV, "fixture.dup,fixture.dup")
        with pytest.raises(host.SchedulerHostError) as duplicate:
            host.configured_scheduler_identities()
        errors.append(duplicate.value)

        with pytest.raises(host.SchedulerHostError) as unknown:
            host.select_scheduler_registrations(WorkflowRegistry(), ("fixture.unknown",))
        errors.append(unknown.value)

        with pytest.raises(scheduler_runtime.SchedulerRuntimeNotConfiguredError) as shared:
            scheduler_runtime.get_scheduler_runtime()
        errors.append(shared.value)

        scheduler_runtime.configure_scheduler_runtime(secret_scheduler_runtime())
        backup_registration = registration(
            host.BUILTIN_BACKUP_SCHEDULER_IDENTITY,
            CanonicalBackupScheduler,
            plugin="builtin",
        )
        with pytest.raises(ExceptionGroup) as builtin:
            asyncio.run(host.run_schedulers((backup_registration,)))
        assert builtin.group_contains(scheduler_runtime.BuiltinSchedulerRuntimeNotConfiguredError)
        errors.extend(builtin.value.exceptions)

        class BrokenScheduler:
            def __init__(self) -> None:
                raise ValueError(f"rejected credential {SECRET_SENTINEL}")

            async def run(self) -> None: ...

        with pytest.raises(ValueError, match="rejected credential"):
            asyncio.run(host.run_schedulers((registration("fixture.broken", BrokenScheduler),)))

    assert (
        logging.ERROR,
        "Failed to construct workflow scheduler fixture.broken from plugin fixture-plugin: "
        "ValueError",
    ) in host_messages(caplog)
    assert_sentinel_absent(caplog, errors)
