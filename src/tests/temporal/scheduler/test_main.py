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

import nv_config_manager_logging as logging_config
import pytest

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.temporal import workflow_registry
from nv_config_manager.temporal.scheduler import host
from nv_config_manager.temporal.scheduler import main as scheduler_main
from nv_config_manager_workflows.registration.registry import PluginInfo, WorkflowRegistry
from nv_config_manager_workflows.schedulers.backup import BackupScheduler
from tests.temporal.scheduler.helpers import registration

STARTUP_LOGGER_NAMES = {workflow_registry.logger.name, scheduler_main.logger.name}
PLUGIN_MESSAGE = "Loaded workflow plugin %s version %s: %d workflows, %d activities, %d schedulers"
MANIFEST_MESSAGE = (
    "Workflow registry manifest %s: %d plugins, %d workflows, %d activities, %d schedulers"
)


@pytest.mark.usefixtures("dcim_provider_must_not_be_consulted")
def test_main_configures_runtime_before_starting_discovered_schedulers(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[object] = []
    scheduler_runtime = object()
    builtin_runtime = object()
    scheduler_registration = registration(
        BackupScheduler.scheduler_identity,
        BackupScheduler,
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
        scheduler_main,
        "build_workflow_registry",
        lambda: events.append("build-registry") or registry,
    )
    monkeypatch.delenv(host.ENABLED_SCHEDULERS_ENV, raising=False)
    monkeypatch.setattr(scheduler_main, "run_scheduler_service", fake_run_scheduler_service)

    with caplog.at_level(logging.INFO, logger=scheduler_main.logger.name):
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
    assert [
        (record.levelno, record.getMessage())
        for record in caplog.records
        if record.name == scheduler_main.logger.name
    ] == [(logging.INFO, "Enabled workflow schedulers: ['builtin.backup']")]


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
    monkeypatch.setattr(scheduler_main, "build_workflow_registry", lambda: registry)
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
        scheduler_registrations=(
            registration("fixture.cleanup", FixtureScheduler, plugin="fixture"),
        ),
        plugin_diagnostics=manifest,
    )

    monkeypatch.setattr(scheduler_main, "configure_logging", lambda *, service: None)
    monkeypatch.setattr(scheduler_main, "setup_telemetry", lambda service: None)
    monkeypatch.setattr(scheduler_main, "configure_workflow_runtime", lambda: None)
    monkeypatch.setattr(scheduler_main, "build_scheduler_runtime", object)
    monkeypatch.setattr(scheduler_main, "configure_scheduler_runtime", lambda runtime: None)
    monkeypatch.setattr(scheduler_main, "build_workflow_registry", lambda: registry)
    monkeypatch.setenv(host.ENABLED_SCHEDULERS_ENV, "fixture.unknown")
    monkeypatch.setattr(
        scheduler_main,
        "run_scheduler_service",
        lambda registrations: pytest.fail("schedulers must not start after selection fails"),
    )

    caplog.set_level(logging.INFO, logger=workflow_registry.logger.name)
    with caplog.at_level(logging.INFO, logger=scheduler_main.logger.name):
        with pytest.raises(host.SchedulerHostError, match="fixture.unknown"):
            scheduler_main.main()

    startup_records = [record for record in caplog.records if record.name in STARTUP_LOGGER_NAMES]
    assert [(record.levelno, record.msg) for record in startup_records] == [
        (logging.INFO, PLUGIN_MESSAGE),
        (logging.INFO, MANIFEST_MESSAGE),
    ]
    assert startup_records[0].getMessage() == (
        "Loaded workflow plugin fixture version 0.1.0: 0 workflows, 0 activities, 1 schedulers"
    )


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
        BackupScheduler.logger.info("unique backup scheduler event")

    backup_logger = BackupScheduler.logger.logger
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
    monkeypatch.setattr(scheduler_main, "build_workflow_registry", lambda: registry)
    monkeypatch.setenv(host.ENABLED_SCHEDULERS_ENV, "fixture.cleanup")
    monkeypatch.setattr(scheduler_main, "run_scheduler_service", fake_run_scheduler_service)

    scheduler_main.main()

    emitted = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line]
    backup_events = [
        event for event in emitted if event["message"] == "unique backup scheduler event"
    ]
    assert len(backup_events) == 1
    startup_events = [event for event in emitted if event["name"] in STARTUP_LOGGER_NAMES]
    manifest_event, enabled_event = startup_events
    assert manifest_event["event_type"] == "workflow_registry_manifest"
    assert enabled_event["message"].startswith("Enabled workflow schedulers:")
    for event in (*backup_events, *startup_events):
        assert event["service"] == "temporal-scheduler"
        assert event["category"] == LogCategory.TEMPORAL_WORKFLOW
