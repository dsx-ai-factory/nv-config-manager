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
import json
import logging
from collections.abc import Iterator

import nv_config_manager_logging as logging_config
import pytest

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.temporal.scheduler import host
from nv_config_manager.temporal.scheduler import main as scheduler_main
from nv_config_manager_workflows.registration.registry import PluginInfo, WorkflowRegistry
from nv_config_manager_workflows.schedulers import runtime as scheduler_runtime
from nv_config_manager_workflows.schedulers.backup import (
    BackupScheduler as CanonicalBackupScheduler,
)
from tests.temporal.scheduler.helpers import (
    assert_sentinel_absent,
    host_messages,
    registration,
    secret_scheduler_runtime,
)


@pytest.fixture
def restore_logging_configuration() -> Iterator[None]:
    """Restore process-wide logging state changed by a real configure_logging() call."""
    original_factory = logging.getLogRecordFactory()
    original_configured = logging_config._logging_configured
    original_handlers = logging.root.handlers[:]
    original_level = logging.root.level
    original_labels = logging_config._custom_labels
    backup_logger = CanonicalBackupScheduler.logger.logger
    original_backup_handlers = backup_logger.handlers[:]
    original_backup_level = backup_logger.level
    yield
    logging.setLogRecordFactory(original_factory)
    logging_config._logging_configured = original_configured
    logging.root.handlers[:] = original_handlers
    logging.root.setLevel(original_level)
    logging_config._custom_labels = original_labels
    backup_logger.handlers[:] = original_backup_handlers
    backup_logger.setLevel(original_backup_level)


@pytest.mark.usefixtures("dcim_provider_must_not_be_consulted")
def test_main_configures_runtime_before_starting_discovered_schedulers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    scheduler_runtime = object()
    builtin_runtime = object()
    scheduler_registration = registration(
        host.BUILTIN_BACKUP_SCHEDULER_IDENTITY,
        CanonicalBackupScheduler,
        plugin="builtin",
    )
    registry = WorkflowRegistry(
        scheduler_registrations=(scheduler_registration,),
    )

    async def fake_run_scheduler_service(configured_registrations: object) -> None:
        events.append(("run-scheduler-service", configured_registrations))

    monkeypatch.setattr(
        scheduler_main,
        "configure_logging",
        lambda *, service: events.append(("logging", service)),
    )
    monkeypatch.setattr(
        scheduler_main,
        "setup_telemetry",
        lambda service: events.append(("telemetry", service)),
    )
    monkeypatch.setattr(
        scheduler_main,
        "configure_workflow_runtime",
        lambda: events.append("configure-workflow-runtime"),
    )
    monkeypatch.setattr(
        scheduler_main,
        "build_scheduler_runtime",
        lambda: events.append("build-scheduler-runtime") or scheduler_runtime,
    )
    monkeypatch.setattr(
        scheduler_main,
        "configure_scheduler_runtime",
        lambda configured: events.append(("configure-scheduler-runtime", configured)),
    )
    monkeypatch.setattr(
        scheduler_main,
        "build_builtin_scheduler_runtime",
        lambda: events.append("build-backup-runtime") or builtin_runtime,
    )
    monkeypatch.setattr(
        scheduler_main,
        "configure_builtin_scheduler_runtime",
        lambda configured: events.append(("configure-backup-runtime", configured)),
    )
    monkeypatch.setattr(
        scheduler_main.WorkflowRegistry,
        "build",
        lambda: events.append("build-registry") or registry,
    )
    monkeypatch.delenv(host.ENABLED_SCHEDULERS_ENV, raising=False)
    monkeypatch.setattr(scheduler_main, "run_scheduler_service", fake_run_scheduler_service)

    scheduler_main.main()

    assert events == [
        ("logging", "temporal-scheduler"),
        ("telemetry", "nv-config-manager-temporal-scheduler"),
        "configure-workflow-runtime",
        "build-scheduler-runtime",
        ("configure-scheduler-runtime", scheduler_runtime),
        "build-registry",
        "build-backup-runtime",
        ("configure-backup-runtime", builtin_runtime),
        ("run-scheduler-service", (scheduler_registration,)),
    ]


def test_main_does_not_configure_backup_runtime_when_backup_is_not_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixtureScheduler:
        async def run(self) -> None: ...

    scheduler_registration = registration("fixture.cleanup", FixtureScheduler)
    registry = WorkflowRegistry(scheduler_registrations=(scheduler_registration,))
    started_with: list[object] = []
    configured: list[str] = []

    async def fake_run_scheduler_service(configured_registrations: object) -> None:
        started_with.append(configured_registrations)

    monkeypatch.setattr(scheduler_main, "configure_logging", lambda *, service: None)
    monkeypatch.setattr(scheduler_main, "setup_telemetry", lambda service: None)
    monkeypatch.setattr(
        scheduler_main,
        "configure_workflow_runtime",
        lambda: configured.append("workflow-runtime"),
    )
    monkeypatch.setattr(scheduler_main, "build_scheduler_runtime", object)
    monkeypatch.setattr(
        scheduler_main,
        "configure_scheduler_runtime",
        lambda runtime: configured.append("scheduler-runtime"),
    )
    monkeypatch.setattr(scheduler_main.WorkflowRegistry, "build", lambda: registry)
    monkeypatch.setenv(host.ENABLED_SCHEDULERS_ENV, "fixture.cleanup")
    monkeypatch.setattr(
        scheduler_main,
        "build_builtin_scheduler_runtime",
        lambda: pytest.fail("backup runtime must remain unconfigured"),
    )
    monkeypatch.setattr(
        scheduler_main,
        "configure_builtin_scheduler_runtime",
        lambda runtime: pytest.fail("backup runtime must remain unconfigured"),
    )
    monkeypatch.setattr(scheduler_main, "run_scheduler_service", fake_run_scheduler_service)

    scheduler_main.main()

    assert started_with == [(scheduler_registration,)]
    assert configured == ["workflow-runtime", "scheduler-runtime"]


@pytest.mark.usefixtures(
    "non_nautobot_config",
    "dcim_provider_must_not_be_consulted",
    "unset_scheduler_runtimes",
)
def test_main_runs_explicit_builtin_backup_with_a_non_nautobot_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicitly enabling the backup scheduler runs it on any DCIM provider.

    The host does not read the DCIM provider; the backup scheduler uses the
    provider-neutral DCIM client interface at run time.
    """
    scheduler_registration = registration(
        host.BUILTIN_BACKUP_SCHEDULER_IDENTITY,
        CanonicalBackupScheduler,
        plugin="builtin",
    )
    registry = WorkflowRegistry(scheduler_registrations=(scheduler_registration,))
    started_with: list[object] = []

    async def fake_run_scheduler_service(configured_registrations: object) -> None:
        started_with.append(configured_registrations)

    monkeypatch.setattr(scheduler_main, "configure_logging", lambda *, service: None)
    monkeypatch.setattr(scheduler_main, "setup_telemetry", lambda service: None)
    monkeypatch.setattr(scheduler_main, "configure_workflow_runtime", lambda: None)
    monkeypatch.setattr(scheduler_main, "build_scheduler_runtime", secret_scheduler_runtime)
    monkeypatch.setattr(scheduler_main.WorkflowRegistry, "build", lambda: registry)
    monkeypatch.setenv(host.ENABLED_SCHEDULERS_ENV, host.BUILTIN_BACKUP_SCHEDULER_IDENTITY)
    monkeypatch.setattr(scheduler_main, "run_scheduler_service", fake_run_scheduler_service)

    scheduler_main.main()

    assert started_with == [(scheduler_registration,)]
    assert isinstance(
        scheduler_runtime.get_builtin_scheduler_runtime(),
        scheduler_runtime.BuiltinSchedulerRuntime,
    )


def test_main_logs_plugin_manifest_and_selected_schedulers(
    monkeypatch: pytest.MonkeyPatch,
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
    manifest = [
        PluginInfo("builtin", "1.0.0", workflow_count=33, activity_count=121, scheduler_count=1),
        PluginInfo("fixture", "0.1.0", workflow_count=0, activity_count=0, scheduler_count=1),
    ]
    registry = WorkflowRegistry(
        scheduler_registrations=(backup_registration, fixture_registration),
        plugin_diagnostics=manifest,
    )

    async def fake_run_scheduler_service(configured_registrations: object) -> None: ...

    monkeypatch.setattr(scheduler_main, "configure_logging", lambda *, service: None)
    monkeypatch.setattr(scheduler_main, "setup_telemetry", lambda service: None)
    monkeypatch.setattr(scheduler_main, "configure_workflow_runtime", lambda: None)
    monkeypatch.setattr(scheduler_main, "build_scheduler_runtime", object)
    monkeypatch.setattr(scheduler_main, "configure_scheduler_runtime", lambda runtime: None)
    monkeypatch.setattr(scheduler_main.WorkflowRegistry, "build", lambda: registry)
    monkeypatch.setenv(
        host.ENABLED_SCHEDULERS_ENV,
        f"{host.BUILTIN_BACKUP_SCHEDULER_IDENTITY},fixture.cleanup",
    )
    monkeypatch.setattr(scheduler_main, "run_scheduler_service", fake_run_scheduler_service)

    with caplog.at_level(logging.INFO, logger=scheduler_main.logger.name):
        scheduler_main.main()

    # A configured service logging filter (EscapingFilter) may replace list items in
    # record.args with escaped strings before caplog sees the record, so check the
    # templates and the rendered content rather than the raw argument objects.
    startup_records = [
        record for record in caplog.records if record.name == scheduler_main.logger.name
    ]
    assert [(record.levelno, record.msg) for record in startup_records] == [
        (logging.INFO, "Discovered workflow plugin manifest: %s"),
        (logging.INFO, "Enabled workflow schedulers: %s"),
    ]
    manifest_message, enabled_message = (record.getMessage() for record in startup_records)
    assert all(repr(info) in manifest_message for info in manifest)
    assert enabled_message == ("Enabled workflow schedulers: ['builtin.backup', 'fixture.cleanup']")
    assert {getattr(record, "category", None) for record in startup_records} == {
        LogCategory.TEMPORAL_WORKFLOW
    }


def test_main_logs_plugin_manifest_before_rejecting_unknown_scheduler(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The manifest is logged even when scheduler selection fails.

    An unknown enabled identity is the case where the discovered manifest is
    most useful to an operator, so it must be emitted before selection raises.
    """

    class FixtureScheduler:
        async def run(self) -> None: ...

    manifest = [
        PluginInfo("fixture", "0.1.0", workflow_count=0, activity_count=0, scheduler_count=1),
    ]
    registry = WorkflowRegistry(
        scheduler_registrations=(registration("fixture.cleanup", FixtureScheduler),),
        plugin_diagnostics=manifest,
    )

    monkeypatch.setattr(scheduler_main, "configure_logging", lambda *, service: None)
    monkeypatch.setattr(scheduler_main, "setup_telemetry", lambda service: None)
    monkeypatch.setattr(scheduler_main, "configure_workflow_runtime", lambda: None)
    monkeypatch.setattr(scheduler_main, "build_scheduler_runtime", object)
    monkeypatch.setattr(scheduler_main, "configure_scheduler_runtime", lambda runtime: None)
    monkeypatch.setattr(scheduler_main.WorkflowRegistry, "build", lambda: registry)
    monkeypatch.setenv(host.ENABLED_SCHEDULERS_ENV, "fixture.unknown")
    monkeypatch.setattr(
        scheduler_main,
        "run_scheduler_service",
        lambda registrations: pytest.fail("schedulers must not start after selection fails"),
    )

    with caplog.at_level(logging.INFO, logger=scheduler_main.logger.name):
        with pytest.raises(host.SchedulerHostError, match="fixture.unknown"):
            scheduler_main.main()

    startup_records = [
        record for record in caplog.records if record.name == scheduler_main.logger.name
    ]
    assert [(record.levelno, record.msg) for record in startup_records] == [
        (logging.INFO, "Discovered workflow plugin manifest: %s"),
    ]
    assert all(repr(info) in startup_records[0].getMessage() for info in manifest)


@pytest.mark.usefixtures("secret_config", "unset_scheduler_runtimes")
def test_scheduler_host_startup_diagnostics_are_credential_free(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Guard that host startup and lifecycle logs never echo configuration values.

    Credentials and endpoints are loaded from a real service INI, held by
    scheduler runtime provider closures, and carried in a scheduler failure
    message. The host must log only stable scheduler identities, plugin names,
    and exception types, so the sentinel never appears
    in any captured record. The scheduler's own exception propagates unchanged
    and is outside this guard.
    """

    class SecretiveScheduler:
        def __init__(self) -> None:
            self.runtime = scheduler_runtime.get_scheduler_runtime()

        async def run(self) -> None:
            await self.runtime.temporal_client()

    backup_registration = registration(
        host.BUILTIN_BACKUP_SCHEDULER_IDENTITY,
        CanonicalBackupScheduler,
        plugin="builtin",
    )
    secretive_registration = registration("fixture.secretive", SecretiveScheduler)
    registry = WorkflowRegistry(
        scheduler_registrations=(backup_registration, secretive_registration),
    )
    monkeypatch.setattr(scheduler_main, "configure_logging", lambda *, service: None)
    monkeypatch.setattr(scheduler_main, "setup_telemetry", lambda service: None)
    monkeypatch.setattr(scheduler_main, "configure_workflow_runtime", lambda: None)
    monkeypatch.setattr(scheduler_main, "build_scheduler_runtime", secret_scheduler_runtime)
    monkeypatch.setattr(scheduler_main.WorkflowRegistry, "build", lambda: registry)
    monkeypatch.setenv(
        host.ENABLED_SCHEDULERS_ENV,
        f"{host.BUILTIN_BACKUP_SCHEDULER_IDENTITY},fixture.secretive",
    )

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ExceptionGroup) as exc_info:
            scheduler_main.main()

    assert exc_info.group_contains(ConnectionError)
    messages = [message for _, message in host_messages(caplog)]
    assert "Started workflow scheduler builtin.backup from plugin builtin" in messages
    assert "Started workflow scheduler fixture.secretive from plugin fixture-plugin" in messages
    assert (
        "Workflow scheduler fixture.secretive from plugin fixture-plugin failed: ConnectionError"
        in messages
    )
    assert_sentinel_absent(caplog)


@pytest.mark.usefixtures("restore_logging_configuration")
def test_main_emits_each_scheduler_record_once_as_structured_service_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Scheduler logs created at import time are emitted once, tagged with the service.

    The built-in backup scheduler creates its logger during import, before the
    host configures logging, so that logger carries the shared package's
    temporary fallback handler. The host must replace it with the root service
    handler instead of adding a second handler alongside it.
    """

    class FixtureScheduler:
        async def run(self) -> None: ...

    scheduler_registration = registration("fixture.cleanup", FixtureScheduler)
    registry = WorkflowRegistry(scheduler_registrations=(scheduler_registration,))

    async def fake_run_scheduler_service(configured_registrations: object) -> None:
        CanonicalBackupScheduler.logger.info("unique backup scheduler event")

    backup_logger = CanonicalBackupScheduler.logger.logger
    backup_logger.handlers.clear()
    logging_config._logging_configured = False
    get_logger(backup_logger.name, category=LogCategory.TEMPORAL_WORKFLOW)
    assert len(backup_logger.handlers) == 1

    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.delenv("NV_CONFIG_MANAGER_CUSTOM_LABELS", raising=False)
    monkeypatch.setattr(scheduler_main, "setup_telemetry", lambda service: None)
    monkeypatch.setattr(scheduler_main, "configure_workflow_runtime", lambda: None)
    monkeypatch.setattr(scheduler_main, "build_scheduler_runtime", object)
    monkeypatch.setattr(scheduler_main, "configure_scheduler_runtime", lambda runtime: None)
    monkeypatch.setattr(scheduler_main.WorkflowRegistry, "build", lambda: registry)
    monkeypatch.setenv(host.ENABLED_SCHEDULERS_ENV, "fixture.cleanup")
    monkeypatch.setattr(scheduler_main, "run_scheduler_service", fake_run_scheduler_service)

    scheduler_main.main()

    emitted = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line]
    backup_events = [
        event for event in emitted if event["message"] == "unique backup scheduler event"
    ]
    assert len(backup_events) == 1
    startup_events = [event for event in emitted if event["name"] == scheduler_main.logger.name]
    assert [event["message"].split(":")[0] for event in startup_events] == [
        "Discovered workflow plugin manifest",
        "Enabled workflow schedulers",
    ]
    for event in (*backup_events, *startup_events):
        assert event["service"] == "temporal-scheduler"
        assert event["category"] == LogCategory.TEMPORAL_WORKFLOW
