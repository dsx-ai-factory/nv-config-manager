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
import gzip
import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from google.protobuf.json_format import MessageToDict
from nv_config_manager_dcim import DCIMConnectivityError
from temporalio.api.common.v1 import Payload
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleSpec,
)
from temporalio.common import SearchAttributeKey, SearchAttributePair, TypedSearchAttributes

from nv_config_manager_workflows.converter import get_data_converter
from nv_config_manager_workflows.scheduler_identity import BACKUP_SCHEDULE_PREFIX
from nv_config_manager_workflows.schedulers import backup as backup_module
from nv_config_manager_workflows.schedulers import runtime as runtime_module
from nv_config_manager_workflows.schedulers.backup import BackupScheduler
from nv_config_manager_workflows.schedulers.runtime import (
    BuiltinSchedulerRuntime,
    BuiltinSchedulerRuntimeNotConfiguredError,
    SchedulerRuntime,
    SchedulerRuntimeNotConfiguredError,
    SchedulerWorkflowRoles,
    configure_builtin_scheduler_runtime,
    configure_scheduler_runtime,
)
from nv_config_manager_workflows.workflows.backup import (
    BackupInput,
    BackupWorkflow,
    TriggerEnum,
)


@dataclass(frozen=True)
class SchedulerMocks:
    """Controllable capabilities installed as the shared scheduler runtime."""

    temporal_client: AsyncMock
    workflow_roles: Mock
    sleep: AsyncMock


@dataclass(frozen=True)
class BackupMocks:
    """Controllable capabilities installed as the built-in scheduler runtime."""

    desired_backup_devices: AsyncMock


@pytest.fixture(autouse=True)
def reset_scheduler_runtimes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_module, "_scheduler_runtime", runtime_module._UNSET)
    monkeypatch.setattr(runtime_module, "_builtin_scheduler_runtime", runtime_module._UNSET)


@pytest.fixture
def client() -> Mock:
    temporal_client = Mock()
    temporal_client.create_schedule = AsyncMock()
    temporal_client.get_schedule_handle.return_value.delete = AsyncMock()
    return temporal_client


@pytest.fixture
def scheduler_runtime(client: Mock) -> SchedulerMocks:
    mocks = SchedulerMocks(
        temporal_client=AsyncMock(return_value=client),
        workflow_roles=Mock(
            return_value=SchedulerWorkflowRoles(
                read_roles=frozenset({"z-reader", "a-reader"}),
                execute_roles=frozenset({"z-executor", "a-executor"}),
            )
        ),
        sleep=AsyncMock(),
    )
    configure_scheduler_runtime(
        SchedulerRuntime(
            temporal_client=mocks.temporal_client,
            workflow_roles=mocks.workflow_roles,
            sleep=mocks.sleep,
        )
    )
    return mocks


@pytest.fixture
def builtin_runtime() -> BackupMocks:
    mocks = BackupMocks(desired_backup_devices=AsyncMock(return_value=set()))
    configure_builtin_scheduler_runtime(
        BuiltinSchedulerRuntime(desired_backup_devices=mocks.desired_backup_devices)
    )
    return mocks


@pytest.fixture
def configured_runtimes(scheduler_runtime: SchedulerMocks, builtin_runtime: BackupMocks) -> None:
    """Configure both runtimes for tests that only need them to be present."""


def async_schedule_listing(*schedule_ids: str) -> AsyncMock:
    listing = AsyncMock()
    listing.__aiter__.return_value = [SimpleNamespace(id=value) for value in schedule_ids]
    return listing


def test_scheduler_preserves_backup_schedule_names_and_cadence() -> None:
    assert BackupScheduler.scheduler_identity == "builtin.backup"
    assert BACKUP_SCHEDULE_PREFIX == "backup-"
    assert BackupScheduler.SPEC.intervals[0].every == timedelta(hours=12)
    assert BackupScheduler.SPEC.jitter == timedelta(hours=1)
    assert BackupScheduler.RECONCILIATION_INTERVAL_SECONDS == 600


@pytest.mark.asyncio
async def test_runtime_providers_supply_temporal_client_and_desired_devices(
    scheduler_runtime: SchedulerMocks,
    builtin_runtime: BackupMocks,
    client: Mock,
) -> None:
    builtin_runtime.desired_backup_devices.return_value = {"device-1", "device-2"}
    scheduler = BackupScheduler()

    assert await scheduler.temporal_client() is client
    assert await scheduler.devices_to_schedule() == {"device-1", "device-2"}
    scheduler_runtime.temporal_client.assert_awaited_once_with()
    builtin_runtime.desired_backup_devices.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_scheduled_devices_adopts_only_backup_prefixed_schedules(
    configured_runtimes: None,
    client: Mock,
) -> None:
    client.list_schedules = AsyncMock(
        return_value=async_schedule_listing(
            "backup-device-1",
            "backup-device-2",
            "inventory-device-3",
        )
    )

    assert await BackupScheduler().scheduled_devices(client) == {"device-1", "device-2"}


@pytest.mark.asyncio
async def test_scheduled_devices_strips_only_the_leading_prefix(
    configured_runtimes: None,
    client: Mock,
) -> None:
    client.list_schedules = AsyncMock(
        return_value=async_schedule_listing(
            "backup-device-backup-1",
            "inventory-backup-device-2",
        )
    )
    scheduler = BackupScheduler()

    devices = await scheduler.scheduled_devices(client)
    assert devices == {"device-backup-1"}

    # Unscheduling an adopted device must target the schedule that was listed.
    await scheduler.unschedule_device(devices.pop(), client)
    client.get_schedule_handle.assert_called_once_with("backup-device-backup-1")


LEGACY_BACKUP_DEVICE = "11111111-1111-1111-1111-111111111111"
PLUGIN_SCHEDULE_LISTING = (
    f"backup-{LEGACY_BACKUP_DEVICE}",
    "backup.sync:device-2",
    "backups.sync:device-3",
    "acme.backup-sync:dev1",
    "builtin.other:device-4",
)


@pytest.mark.asyncio
async def test_scheduled_devices_ignores_plugin_schedule_ids(
    configured_runtimes: None,
    client: Mock,
) -> None:
    client.list_schedules = AsyncMock(return_value=async_schedule_listing(*PLUGIN_SCHEDULE_LISTING))

    assert await BackupScheduler().scheduled_devices(client) == {LEGACY_BACKUP_DEVICE}


@pytest.mark.asyncio
async def test_reconciliation_never_deletes_plugin_schedule_ids(
    scheduler_runtime: SchedulerMocks,
    builtin_runtime: BackupMocks,
    client: Mock,
) -> None:
    client.list_schedules = AsyncMock(return_value=async_schedule_listing(*PLUGIN_SCHEDULE_LISTING))
    builtin_runtime.desired_backup_devices.return_value = set()

    await BackupScheduler().reconcile_schedules()

    # Only the legacy backup schedule is removed; plugins named "backup" or
    # "backups", and plugin schedulers such as "acme.backup-sync", keep their
    # schedules because their IDs do not start with "backup-".
    client.get_schedule_handle.assert_called_once_with(f"backup-{LEGACY_BACKUP_DEVICE}")
    client.create_schedule.assert_not_awaited()


# Registry validation reserves the "backup-" plugin-name prefix, so every
# "backup-" schedule ID is a backup schedule, even one that contains ":".
DEVICE_IDS_WITH_SEPARATOR = ("site:dev1", "1abc.d:x", "tools.sync:device-2")


@pytest.mark.asyncio
@pytest.mark.parametrize("device_id", DEVICE_IDS_WITH_SEPARATOR)
async def test_scheduled_devices_adopts_backup_ids_whose_device_id_contains_separator(
    configured_runtimes: None,
    client: Mock,
    device_id: str,
) -> None:
    client.list_schedules = AsyncMock(return_value=async_schedule_listing(f"backup-{device_id}"))

    assert await BackupScheduler().scheduled_devices(client) == {device_id}


@pytest.mark.asyncio
@pytest.mark.parametrize("device_id", DEVICE_IDS_WITH_SEPARATOR)
async def test_device_id_with_separator_is_created_once_across_reconciliations(
    scheduler_runtime: SchedulerMocks,
    builtin_runtime: BackupMocks,
    client: Mock,
    device_id: str,
) -> None:
    created: list[str] = []

    async def create_schedule(schedule_id: str, schedule: Schedule) -> None:
        created.append(schedule_id)

    client.create_schedule = AsyncMock(side_effect=create_schedule)
    client.list_schedules = AsyncMock(side_effect=lambda: async_schedule_listing(*created))
    builtin_runtime.desired_backup_devices.return_value = {device_id, "device-1"}
    scheduler = BackupScheduler()

    await scheduler.reconcile_schedules()
    await scheduler.reconcile_schedules()

    assert sorted(created) == sorted([f"backup-{device_id}", "backup-device-1"])
    client.get_schedule_handle.assert_not_called()


GOLDEN_WORKFLOW_ID = "12345678-1234-5678-1234-567812345678"


@pytest.fixture
def fixed_workflow_id(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin the otherwise random scheduled workflow id so the Schedule is reproducible."""
    monkeypatch.setattr(backup_module, "uuid4", lambda: UUID(GOLDEN_WORKFLOW_ID))
    return GOLDEN_WORKFLOW_ID


async def created_schedule(client: Mock, device_uuid: str) -> tuple[str, Schedule]:
    """Schedule one device and return the single ``create_schedule`` call's arguments."""
    await BackupScheduler().schedule_device(device_uuid, client)

    client.create_schedule.assert_awaited_once()
    schedule_id, schedule = client.create_schedule.await_args.args
    return schedule_id, schedule


def readable_payload(payload: Payload) -> dict[str, object]:
    """Render a JSON payload as text so a golden can be written as a literal."""
    return {
        "metadata": {key: value.decode() for key, value in payload.metadata.items()},
        "data": json.loads(payload.data),
    }


@pytest.mark.asyncio
async def test_schedule_device_creates_the_golden_temporal_schedule(
    fixed_workflow_id: str,
    scheduler_runtime: SchedulerMocks,
    client: Mock,
) -> None:
    """The whole Schedule must equal the one main's service-owned scheduler created.

    The expected value is transcribed with literals from
    ``main:src/nv_config_manager/temporal/ngc/schedulers/backup.py`` so that a
    change to any scheduler constant fails here instead of moving the golden.
    Temporal's ``Schedule``, ``ScheduleActionStartWorkflow``, ``ScheduleSpec``,
    ``ScheduleIntervalSpec`` and ``TypedSearchAttributes`` are value-comparing
    dataclasses, and the action stores the workflow as its registered type name.
    """
    schedule_id, schedule = await created_schedule(client, "device-1")

    scheduler_runtime.workflow_roles.assert_called_once_with("BackupWorkflow")
    assert schedule_id == "backup-device-1"
    assert schedule == Schedule(
        action=ScheduleActionStartWorkflow(
            BackupWorkflow.run,
            BackupInput(
                device_id="device-1",
                trigger=TriggerEnum.SCHEDULED,
                user="nv-config-manager-temporal",
                user_domain=None,
                workflow_id=None,
                intended_config_commit_id=None,
            ),
            id="12345678-1234-5678-1234-567812345678",
            task_queue="default-task-queue",
            execution_timeout=timedelta(minutes=30),
            typed_search_attributes=TypedSearchAttributes(
                (
                    SearchAttributePair(
                        SearchAttributeKey.for_keyword("User"), "nv-config-manager-temporal"
                    ),
                    SearchAttributePair(
                        SearchAttributeKey.for_keyword_list("ReadRoles"),
                        ["a-reader", "z-reader"],
                    ),
                    SearchAttributePair(
                        SearchAttributeKey.for_keyword_list("ExecuteRoles"),
                        ["a-executor", "z-executor"],
                    ),
                    SearchAttributePair[bool](
                        SearchAttributeKey.for_bool("PendingApproval"), False
                    ),
                    SearchAttributePair[bool](SearchAttributeKey.for_bool("FailedStage"), False),
                )
            ),
        ),
        spec=ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(hours=12))],
            jitter=timedelta(hours=1),
        ),
    )
    # Equality is by value; the action records the Temporal type name, not the function.
    assert isinstance(schedule.action, ScheduleActionStartWorkflow)
    assert schedule.action.workflow == "BackupWorkflow"


# Temporal's default converter warns about serializing Pydantic models; the
# payload it produces is what main sent and is asserted below.
@pytest.mark.filterwarnings("ignore:If you're using Pydantic v2:UserWarning")
@pytest.mark.filterwarnings("ignore:The `dict` method is deprecated")
@pytest.mark.asyncio
async def test_schedule_device_serializes_to_the_golden_temporal_schedule_proto(
    fixed_workflow_id: str,
    scheduler_runtime: SchedulerMocks,
    client: Mock,
) -> None:
    """The wire form Temporal receives matches main, including policy defaults and payloads.

    The package converter is the one main's service converter re-exported, so
    no service import is needed. Gzip output embeds a timestamp, so the input
    payload is compared after decompression rather than byte for byte.
    """
    _, schedule = await created_schedule(client, "device-1")
    temporal_client = cast(
        Client, SimpleNamespace(data_converter=get_data_converter(), namespace="default")
    )

    proto = await schedule._to_proto(temporal_client)

    start_workflow = proto.action.start_workflow
    (compressed_input,) = start_workflow.input.payloads
    assert dict(compressed_input.metadata) == {"encoding": b"binary/gzip"}
    assert readable_payload(Payload.FromString(gzip.decompress(compressed_input.data))) == {
        "metadata": {"encoding": "json/plain"},
        "data": {
            "device_id": "device-1",
            "intended_config_commit_id": None,
            "suppress_drift_notification": False,
            "terminate_on_failure": False,
            "trigger": "SCHEDULED",
            "user": "nv-config-manager-temporal",
            "user_domain": None,
            "workflow_id": None,
        },
    }
    assert {
        name: readable_payload(payload)
        for name, payload in start_workflow.search_attributes.indexed_fields.items()
    } == {
        "User": {
            "metadata": {"encoding": "json/plain", "type": "Keyword"},
            "data": "nv-config-manager-temporal",
        },
        "ReadRoles": {
            "metadata": {"encoding": "json/plain", "type": "KeywordList"},
            "data": ["a-reader", "z-reader"],
        },
        "ExecuteRoles": {
            "metadata": {"encoding": "json/plain", "type": "KeywordList"},
            "data": ["a-executor", "z-executor"],
        },
        "PendingApproval": {"metadata": {"encoding": "json/plain", "type": "Bool"}, "data": False},
        "FailedStage": {"metadata": {"encoding": "json/plain", "type": "Bool"}, "data": False},
    }
    start_workflow.ClearField("input")
    start_workflow.ClearField("search_attributes")
    assert MessageToDict(proto) == {
        "spec": {"interval": [{"interval": "43200s"}], "jitter": "3600s"},
        "action": {
            "startWorkflow": {
                "workflowId": "12345678-1234-5678-1234-567812345678",
                "workflowType": {"name": "BackupWorkflow"},
                "taskQueue": {"name": "default-task-queue"},
                "workflowExecutionTimeout": "1800s",
                "priority": {},
            }
        },
        "policies": {
            "overlapPolicy": "SCHEDULE_OVERLAP_POLICY_SKIP",
            "catchupWindow": "31536000s",
        },
        "state": {},
    }


@pytest.mark.asyncio
async def test_missing_rbac_configuration_skips_schedule_creation(
    caplog: pytest.LogCaptureFixture,
    scheduler_runtime: SchedulerMocks,
    client: Mock,
) -> None:
    scheduler_runtime.workflow_roles.return_value = None

    with caplog.at_level(logging.ERROR, logger=backup_module.__name__):
        await BackupScheduler().schedule_device("device-1", client)

    client.create_schedule.assert_not_awaited()
    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (logging.ERROR, f"No RBAC configuration found for {BackupWorkflow.__name__}")
    ]


@pytest.mark.parametrize(
    ("read_roles", "execute_roles", "expected_read_roles", "expected_execute_roles"),
    [
        pytest.param(
            {"z-reader", "a-reader"},
            {"z-executor", "a-executor"},
            ["a-reader", "z-reader"],
            ["a-executor", "z-executor"],
            id="populated",
        ),
        pytest.param(set(), {"executor"}, [], ["executor"], id="empty-read-roles"),
        pytest.param({"reader"}, set(), ["reader"], [], id="admin-only-execute"),
        pytest.param(set(), set(), [], [], id="both-empty"),
    ],
)
@pytest.mark.asyncio
async def test_configured_rbac_creates_schedule_with_sorted_role_lists(
    scheduler_runtime: SchedulerMocks,
    client: Mock,
    read_roles: set[str],
    execute_roles: set[str],
    expected_read_roles: list[str],
    expected_execute_roles: list[str],
) -> None:
    scheduler_runtime.workflow_roles.return_value = SchedulerWorkflowRoles(
        read_roles=frozenset(read_roles),
        execute_roles=frozenset(execute_roles),
    )

    await BackupScheduler().schedule_device("device-1", client)

    client.create_schedule.assert_awaited_once()
    schedule_id, schedule = client.create_schedule.await_args.args
    assert schedule_id == "backup-device-1"
    search_attributes = {
        pair.key.name: pair.value for pair in schedule.action.typed_search_attributes
    }
    assert search_attributes["ReadRoles"] == expected_read_roles
    assert search_attributes["ExecuteRoles"] == expected_execute_roles


@pytest.mark.asyncio
async def test_unschedule_device_deletes_the_stable_schedule_id(
    configured_runtimes: None, client: Mock
) -> None:
    await BackupScheduler().unschedule_device("device-1", client)

    client.get_schedule_handle.assert_called_once_with("backup-device-1")
    client.get_schedule_handle.return_value.delete.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_reconciliation_adds_and_removes_only_the_set_difference(
    monkeypatch: pytest.MonkeyPatch,
    configured_runtimes: None,
    client: Mock,
) -> None:
    scheduler = BackupScheduler()
    monkeypatch.setattr(scheduler, "temporal_client", AsyncMock(return_value=client))
    monkeypatch.setattr(
        scheduler,
        "devices_to_schedule",
        AsyncMock(return_value={"device-1", "device-2"}),
    )
    monkeypatch.setattr(
        scheduler,
        "scheduled_devices",
        AsyncMock(return_value={"device-2", "device-3"}),
    )
    schedule_device = AsyncMock()
    unschedule_device = AsyncMock()
    monkeypatch.setattr(scheduler, "schedule_device", schedule_device)
    monkeypatch.setattr(scheduler, "unschedule_device", unschedule_device)

    await scheduler.reconcile_schedules()

    schedule_device.assert_awaited_once_with("device-1", client)
    unschedule_device.assert_awaited_once_with("device-3", client)


@pytest.mark.asyncio
async def test_desired_device_failure_leaves_existing_schedules_unchanged_and_retries_later(
    caplog: pytest.LogCaptureFixture,
    scheduler_runtime: SchedulerMocks,
    builtin_runtime: BackupMocks,
    client: Mock,
) -> None:
    builtin_runtime.desired_backup_devices.side_effect = DCIMConnectivityError("unavailable")
    scheduler_runtime.sleep.side_effect = asyncio.CancelledError
    client.list_schedules = AsyncMock(return_value=async_schedule_listing("backup-device-1"))

    with (
        caplog.at_level(logging.ERROR, logger=backup_module.__name__),
        pytest.raises(asyncio.CancelledError),
    ):
        await BackupScheduler().run()

    scheduler_runtime.temporal_client.assert_awaited_once_with()
    builtin_runtime.desired_backup_devices.assert_awaited_once_with()
    client.list_schedules.assert_not_awaited()
    client.create_schedule.assert_not_awaited()
    client.get_schedule_handle.assert_not_called()
    scheduler_runtime.sleep.assert_awaited_once_with(600)
    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (
            logging.ERROR,
            "Error querying desired backup devices from the configured DCIM, "
            "leaving schedules unchanged.",
        )
    ]


@pytest.mark.asyncio
async def test_cancellation_during_reconciliation_stops_without_sleep(
    scheduler_runtime: SchedulerMocks,
    builtin_runtime: BackupMocks,
) -> None:
    builtin_runtime.desired_backup_devices.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await BackupScheduler().run()

    scheduler_runtime.sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_non_dcim_failure_propagates(
    scheduler_runtime: SchedulerMocks,
    builtin_runtime: BackupMocks,
) -> None:
    scheduler_runtime.temporal_client.side_effect = RuntimeError("Temporal unavailable")

    with pytest.raises(RuntimeError, match="Temporal unavailable"):
        await BackupScheduler().run()

    scheduler_runtime.sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_during_sleep_propagates_after_successful_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
    scheduler_runtime: SchedulerMocks,
    builtin_runtime: BackupMocks,
) -> None:
    scheduler = BackupScheduler()
    reconcile = AsyncMock()
    monkeypatch.setattr(scheduler, "reconcile_schedules", reconcile)
    scheduler_runtime.sleep.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await scheduler.run()

    reconcile.assert_awaited_once_with()
    scheduler_runtime.sleep.assert_awaited_once_with(600)


@pytest.mark.asyncio
async def test_run_fails_before_reconciliation_without_scheduler_runtime(
    builtin_runtime: BackupMocks,
) -> None:
    with pytest.raises(SchedulerRuntimeNotConfiguredError, match="configure_scheduler_runtime"):
        await BackupScheduler().run()

    builtin_runtime.desired_backup_devices.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_fails_before_reconciliation_without_backup_runtime(
    scheduler_runtime: SchedulerMocks,
) -> None:
    with pytest.raises(
        BuiltinSchedulerRuntimeNotConfiguredError,
        match="configure_builtin_scheduler_runtime",
    ):
        await BackupScheduler().run()

    scheduler_runtime.temporal_client.assert_not_awaited()
    scheduler_runtime.workflow_roles.assert_not_called()
    scheduler_runtime.sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_each_cycle_connects_once_before_querying_desired_devices(
    scheduler_runtime: SchedulerMocks,
    builtin_runtime: BackupMocks,
    client: Mock,
) -> None:
    events: list[str] = []
    cycles = 0

    async def connect() -> Mock:
        events.append("connect")
        return client

    async def desired_devices() -> set[str]:
        events.append("desired-devices")
        return {"device-1"}

    async def list_schedules() -> AsyncMock:
        events.append("list-schedules")
        return async_schedule_listing()

    async def create_schedule(*args: object) -> None:
        events.append("create-schedule")

    def workflow_roles(name: str) -> SchedulerWorkflowRoles:
        events.append(f"workflow-roles:{name}")
        return SchedulerWorkflowRoles(read_roles=frozenset(), execute_roles=frozenset())

    async def sleep(seconds: float) -> None:
        nonlocal cycles
        events.append(f"sleep:{seconds}")
        cycles += 1
        if cycles == 2:
            raise asyncio.CancelledError

    scheduler_runtime.temporal_client.side_effect = connect
    scheduler_runtime.workflow_roles.side_effect = workflow_roles
    scheduler_runtime.sleep.side_effect = sleep
    builtin_runtime.desired_backup_devices.side_effect = desired_devices
    client.list_schedules = AsyncMock(side_effect=list_schedules)
    client.create_schedule = AsyncMock(side_effect=create_schedule)

    with pytest.raises(asyncio.CancelledError):
        await BackupScheduler().run()

    one_cycle = [
        "connect",
        "desired-devices",
        "list-schedules",
        "workflow-roles:BackupWorkflow",
        "create-schedule",
        "sleep:600",
    ]
    assert events == one_cycle * 2
    assert scheduler_runtime.temporal_client.await_count == 2
    assert builtin_runtime.desired_backup_devices.await_count == 2
