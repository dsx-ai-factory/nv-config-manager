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
"""The scheduler host runs a plugin scheduler discovered through its entry point.

Only process setup and the Temporal client are faked: discovery, registry
validation, selection, construction, signal handling, and supervision are real.
"""

import asyncio
import logging
import signal
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import NoReturn, cast

import pytest
from temporalio.client import Client, Schedule

from nv_config_manager.temporal import workflow_registry
from nv_config_manager.temporal.scheduler import host
from nv_config_manager.temporal.scheduler import main as scheduler_main
from nv_config_manager_workflows.schedulers import runtime as scheduler_runtime
from nv_config_manager_workflows.schedulers.backup import BackupScheduler
from tests.temporal.scheduler.helpers import (
    SECRET_SENTINEL,
    assert_sentinel_absent,
    host_messages,
)

FIXTURE_PLUGIN = "nvcm-fixture"
FIXTURE_SCHEDULER = "nvcm-fixture.heartbeat"


@dataclass
class FakeScheduleClient:
    """Record the schedule calls the fixture scheduler makes on its Temporal client."""

    created: dict[str, Schedule] = field(default_factory=dict)

    async def list_schedules(self) -> AsyncIterator[SimpleNamespace]:
        return self._listing()

    async def _listing(self) -> AsyncIterator[SimpleNamespace]:
        for schedule_id in list(self.created):
            yield SimpleNamespace(id=schedule_id)

    async def create_schedule(self, schedule_id: str, schedule: Schedule, /) -> None:
        self.created[schedule_id] = schedule


@pytest.fixture
def schedule_client(monkeypatch: pytest.MonkeyPatch) -> FakeScheduleClient:
    """Serve scheduler main a fake Temporal client and send SIGTERM at the first sleep."""
    client = FakeScheduleClient()
    sigterm_handler_before_main = signal.getsignal(signal.SIGTERM)

    async def temporal_client() -> Client:
        return cast(Client, client)

    async def sleep(seconds: float) -> None:
        # The host's loop.add_signal_handler replaces the process SIGTERM handler with
        # asyncio's no-op Python handler; without it, a real SIGTERM kills the test process.
        if signal.getsignal(signal.SIGTERM) in (
            sigterm_handler_before_main,
            signal.SIG_DFL,
            signal.SIG_IGN,
            None,
        ):
            pytest.fail("Scheduler host did not install its SIGTERM handler; not sending SIGTERM")
        signal.raise_signal(signal.SIGTERM)
        await asyncio.Future()

    runtime = scheduler_runtime.SchedulerRuntime(
        temporal_client=temporal_client,
        workflow_roles=lambda _workflow: None,
        sleep=sleep,
    )
    monkeypatch.setattr(scheduler_main, "configure_logging", lambda *, service: None)
    monkeypatch.setattr(scheduler_main, "setup_telemetry", lambda service: None)
    monkeypatch.setattr(scheduler_main, "configure_workflow_runtime", lambda: None)
    monkeypatch.setattr(scheduler_main, "build_scheduler_runtime", lambda: runtime)
    monkeypatch.setenv(host.ENABLED_SCHEDULERS_ENV, FIXTURE_SCHEDULER)
    return client


@pytest.mark.usefixtures(
    "fixture_plugin_installed",
    "non_nautobot_config",
    "dcim_provider_must_not_be_consulted",
    "unset_scheduler_runtimes",
)
def test_main_runs_the_discovered_plugin_scheduler_until_sigterm(
    schedule_client: FakeScheduleClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-Nautobot deployment enables only the plugin scheduler, which stops on SIGTERM.

    ``builtin.backup`` is discovered but not selected, so its runtime stays
    unconfigured and the DCIM provider is never consulted.
    """
    # Another plugin's schedule, which owns_schedule_id must filter out and leave alone.
    foreign_id, foreign_schedule = "other-plugin.sync:x", cast(Schedule, object())
    schedule_client.created[foreign_id] = foreign_schedule

    with caplog.at_level(logging.INFO):
        scheduler_main.main()

    assert list(schedule_client.created) == [foreign_id, f"{FIXTURE_SCHEDULER}:heartbeat"]
    assert schedule_client.created[foreign_id] is foreign_schedule
    attribution = f"workflow scheduler {FIXTURE_SCHEDULER} from plugin {FIXTURE_PLUGIN}"
    assert host_messages(caplog) == [
        (logging.INFO, f"Constructing {attribution}"),
        (logging.INFO, f"Constructed {attribution}"),
        (logging.INFO, f"Started {attribution}"),
        (logging.INFO, "Received signal SIGTERM, stopping workflow schedulers"),
        (logging.INFO, f"Stopped {attribution}"),
    ]
    (manifest_record,) = [
        record
        for record in caplog.records
        if vars(record).get("event_type") == "workflow_registry_manifest"
    ]
    assert manifest_record.name == workflow_registry.logger.name
    assert vars(manifest_record)["scheduler_identities"] == [
        BackupScheduler.scheduler_identity,
        FIXTURE_SCHEDULER,
    ]
    assert f"Enabled workflow schedulers: ['{FIXTURE_SCHEDULER}']" in caplog.messages
    with pytest.raises(scheduler_runtime.BuiltinSchedulerRuntimeNotConfiguredError):
        scheduler_runtime.get_builtin_scheduler_runtime()


@pytest.mark.usefixtures("fixture_plugin_installed", "secret_config", "unset_scheduler_runtimes")
def test_main_fails_when_the_discovered_plugin_scheduler_fails(
    schedule_client: FakeScheduleClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing plugin scheduler fails the host with an attributed, credential-free log."""

    async def list_schedules() -> NoReturn:
        raise ConnectionError(f"Temporal rejected client key {SECRET_SENTINEL}")

    monkeypatch.setattr(schedule_client, "list_schedules", list_schedules)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ExceptionGroup) as exc_info:
            scheduler_main.main()

    assert exc_info.group_contains(ConnectionError)
    assert schedule_client.created == {}
    assert [message for level, message in host_messages(caplog) if level >= logging.ERROR] == [
        f"Workflow scheduler {FIXTURE_SCHEDULER} from plugin {FIXTURE_PLUGIN} failed: "
        "ConnectionError"
    ]
    assert_sentinel_absent(caplog)
