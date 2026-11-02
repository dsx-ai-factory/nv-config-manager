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
"""Tests for the shared workflow registry bootstrap and its startup diagnostics."""

import ast
import json
import logging
from pathlib import Path

import nv_config_manager_logging as logging_config
import pytest

from nv_config_manager.common.log import LogCategory, configure_logging
from nv_config_manager.temporal import workflow_registry
from nv_config_manager.temporal.workflow_registry import (
    build_workflow_registry,
    log_workflow_registry,
)
from nv_config_manager_workflows.registration import (
    BUILTIN_PLUGIN_NAME,
    WORKFLOW_PLUGIN_ENTRY_POINT_GROUP,
    PluginInfo,
    WorkflowPluginDescriptor,
    WorkflowPluginDiscoveryError,
    WorkflowRegistry,
    builtin_plugin,
    registry_manifest,
)
from tests.temporal.scheduler.helpers import SECRET_SENTINEL, registration

PLUGIN_FIELDS = (
    "event_type",
    "plugin",
    "plugin_version",
    "workflow_count",
    "activity_count",
    "scheduler_count",
    "scheduler_identities",
    "registry_fingerprint",
)
MANIFEST_FIELDS = (
    "event_type",
    "registry_fingerprint",
    "plugins",
    "workflow_count",
    "activity_count",
    "scheduler_count",
    "scheduler_identities",
)
_SERVICE_PACKAGE = Path(workflow_registry.__file__).parents[1]
# Modules that take their workflow catalog from the registry.
REGISTRY_CONSUMER_MODULES = (
    _SERVICE_PACKAGE / "temporal" / "worker" / "main.py",
    *sorted((_SERVICE_PACKAGE / "temporal" / "api").rglob("*.py")),
    _SERVICE_PACKAGE / "temporal" / "cli.py",
    *sorted((_SERVICE_PACKAGE / "mcp").rglob("*.py")),
)
SERVICE_WORKFLOW_CATALOGS = (
    "nv_config_manager.temporal.hello_world.workflows",
    "nv_config_manager.temporal.ngc.workflows",
)


class _FixtureScheduler:
    async def run(self) -> None: ...


def _registry_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == workflow_registry.logger.name]


def _fields(record: logging.LogRecord | dict[str, object], names: tuple[str, ...]) -> dict:
    values = record if isinstance(record, dict) else vars(record)
    return {name: values.get(name) for name in names}


def _imported_modules(path: Path) -> set[str]:
    """Return every absolute module a source file imports, including ``from`` members."""
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


def test_build_rejects_a_registry_without_the_builtin_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing built-in entry point fails startup instead of serving an empty catalog."""
    fixture_only = WorkflowRegistry(
        plugin_diagnostics=[
            PluginInfo("fixture", "0.1.0", workflow_count=0, activity_count=0, scheduler_count=0)
        ],
    )
    monkeypatch.setattr(WorkflowRegistry, "build", lambda: fixture_only)

    with pytest.raises(WorkflowPluginDiscoveryError) as exc_info:
        build_workflow_registry()

    message = str(exc_info.value)
    assert f'Workflow plugin "{BUILTIN_PLUGIN_NAME}"' in message
    assert f"entry-point group {WORKFLOW_PLUGIN_ENTRY_POINT_GROUP}" in message


def test_build_returns_the_installed_registry_with_the_builtin_plugin() -> None:
    registry = build_workflow_registry()

    assert BUILTIN_PLUGIN_NAME in {plugin.name for plugin in registry.plugin_diagnostics}
    assert registry.all_workflows


def test_log_emits_one_record_per_plugin_and_a_manifest_summary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = build_workflow_registry()
    expected = registry_manifest(registry)

    with caplog.at_level(logging.INFO, logger=workflow_registry.logger.name):
        log_workflow_registry(registry)

    records = _registry_records(caplog)
    assert [vars(record).get("event_type") for record in records] == [
        *(["workflow_plugin"] * len(expected.plugins)),
        "workflow_registry_manifest",
    ]
    assert {record.levelno for record in records} == {logging.INFO}
    assert {vars(record).get("category") for record in records} == {LogCategory.TEMPORAL_WORKFLOW}
    assert {vars(record).get("registry_fingerprint") for record in records} == {
        expected.fingerprint
    }
    *plugin_records, summary = records
    builtin_record = next(
        record for record in plugin_records if vars(record).get("plugin") == BUILTIN_PLUGIN_NAME
    )
    assert vars(builtin_record).get("scheduler_identities") == ["builtin.backup"]
    assert _fields(summary, MANIFEST_FIELDS) == {
        "event_type": "workflow_registry_manifest",
        "registry_fingerprint": expected.fingerprint,
        "plugins": [f"{plugin.name}=={plugin.version}" for plugin in expected.plugins],
        "workflow_count": len(expected.workflows),
        "activity_count": len(expected.activities),
        "scheduler_count": len(expected.schedulers),
        "scheduler_identities": list(expected.schedulers),
    }


def test_log_attributes_scheduler_identities_to_their_plugin(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = WorkflowRegistry(
        scheduler_registrations=(
            registration("builtin.backup", _FixtureScheduler, plugin="builtin"),
            registration("fixture.cleanup", _FixtureScheduler, plugin="fixture"),
            registration("fixture.audit", _FixtureScheduler, plugin="fixture"),
        ),
        plugin_diagnostics=[
            PluginInfo(
                "builtin", "1.0.0", workflow_count=33, activity_count=121, scheduler_count=1
            ),
            PluginInfo("fixture", "0.1.0", workflow_count=0, activity_count=0, scheduler_count=2),
        ],
    )
    fingerprint = registry_manifest(registry).fingerprint

    with caplog.at_level(logging.INFO, logger=workflow_registry.logger.name):
        log_workflow_registry(registry)

    builtin_record, fixture_record, summary = _registry_records(caplog)
    assert builtin_record.getMessage() == (
        "Loaded workflow plugin builtin version 1.0.0: 33 workflows, 121 activities, 1 schedulers"
    )
    assert _fields(builtin_record, PLUGIN_FIELDS) == {
        "event_type": "workflow_plugin",
        "plugin": "builtin",
        "plugin_version": "1.0.0",
        "workflow_count": 33,
        "activity_count": 121,
        "scheduler_count": 1,
        "scheduler_identities": ["builtin.backup"],
        "registry_fingerprint": fingerprint,
    }
    assert fixture_record.getMessage() == (
        "Loaded workflow plugin fixture version 0.1.0: 0 workflows, 0 activities, 2 schedulers"
    )
    assert _fields(fixture_record, PLUGIN_FIELDS) == {
        "event_type": "workflow_plugin",
        "plugin": "fixture",
        "plugin_version": "0.1.0",
        "workflow_count": 0,
        "activity_count": 0,
        "scheduler_count": 2,
        # Per-plugin identities keep registry order; the summary sorts them.
        "scheduler_identities": ["fixture.cleanup", "fixture.audit"],
        "registry_fingerprint": fingerprint,
    }
    assert summary.getMessage() == (
        f"Workflow registry manifest {fingerprint}: "
        "2 plugins, 0 workflows, 0 activities, 3 schedulers"
    )
    assert _fields(summary, MANIFEST_FIELDS) == {
        "event_type": "workflow_registry_manifest",
        "registry_fingerprint": fingerprint,
        "plugins": ["builtin==1.0.0", "fixture==0.1.0"],
        "workflow_count": 0,
        "activity_count": 0,
        "scheduler_count": 3,
        "scheduler_identities": ["builtin.backup", "fixture.audit", "fixture.cleanup"],
    }


@pytest.mark.usefixtures("restore_logging_configuration")
def test_log_emits_credential_free_structured_service_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """JSON records carry the diagnostic fields and service, never descriptor metadata."""
    fixture = WorkflowPluginDescriptor(
        name="fixture",
        version="0.1.0",
        metadata={"token": SECRET_SENTINEL, "url": f"https://{SECRET_SENTINEL}.example.invalid"},
    )
    registry = WorkflowRegistry.build(
        {BUILTIN_PLUGIN_NAME: builtin_plugin(), fixture.name: fixture},
    )
    manifest = registry_manifest(registry)
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.delenv("NV_CONFIG_MANAGER_CUSTOM_LABELS", raising=False)
    logging_config._logging_configured = False
    configure_logging(service="temporal-worker")

    log_workflow_registry(registry)

    output = capsys.readouterr().err
    assert SECRET_SENTINEL not in output
    events = [json.loads(line) for line in output.splitlines() if line]
    events = [event for event in events if event["name"] == workflow_registry.logger.name]
    assert [event["event_type"] for event in events] == [
        "workflow_plugin",
        "workflow_plugin",
        "workflow_registry_manifest",
    ]
    for event in events:
        assert event["service"] == "temporal-worker"
        assert event["category"] == LogCategory.TEMPORAL_WORKFLOW
        assert event["levelname"] == "INFO"
        assert event["registry_fingerprint"] == manifest.fingerprint
    builtin_event, fixture_event, summary_event = events
    assert builtin_event["plugin"] == BUILTIN_PLUGIN_NAME
    assert builtin_event["scheduler_identities"] == ["builtin.backup"]
    assert _fields(fixture_event, PLUGIN_FIELDS) == {
        "event_type": "workflow_plugin",
        "plugin": "fixture",
        "plugin_version": "0.1.0",
        "workflow_count": 0,
        "activity_count": 0,
        "scheduler_count": 0,
        "scheduler_identities": [],
        "registry_fingerprint": manifest.fingerprint,
    }
    assert summary_event["plugins"] == [
        f"{plugin.name}=={plugin.version}" for plugin in manifest.plugins
    ]
    assert summary_event["scheduler_identities"] == ["builtin.backup"]


@pytest.mark.parametrize(
    "module",
    REGISTRY_CONSUMER_MODULES,
    ids=lambda path: path.relative_to(_SERVICE_PACKAGE).as_posix(),
)
def test_registry_consumers_do_not_import_service_workflow_catalogs(module: Path) -> None:
    """Registry consumers read workflows from the registry, not the service-owned lists."""
    service_imports = sorted(
        imported
        for imported in _imported_modules(module)
        if any(
            imported == catalog or imported.startswith(f"{catalog}.")
            for catalog in SERVICE_WORKFLOW_CATALOGS
        )
    )

    assert service_imports == []
