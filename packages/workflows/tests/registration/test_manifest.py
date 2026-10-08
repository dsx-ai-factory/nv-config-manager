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
import re
from dataclasses import asdict
from importlib import metadata
from typing import Any, NoReturn

import pytest

from nv_config_manager_workflows.activities.builtin import BUILTIN_ACTIVITIES
from nv_config_manager_workflows.registration.builtin import BUILTIN_PLUGIN_NAME
from nv_config_manager_workflows.registration.contract import activity_name, workflow_type_name
from nv_config_manager_workflows.registration.descriptor import WorkflowPluginDescriptor
from nv_config_manager_workflows.registration.errors import WorkflowPluginDiscoveryError
from nv_config_manager_workflows.registration.manifest import main, registry_manifest
from nv_config_manager_workflows.registration.registry import PluginInfo, WorkflowRegistry
from nv_config_manager_workflows.schedulers.builtin import BUILTIN_SCHEDULERS
from nv_config_manager_workflows.workflows.builtin import BUILTIN_WORKFLOWS
from nv_config_manager_workflows.workflows.hello_world import LOCAL_TEST_WORKFLOWS

from .test_registry import (
    AlphaWorkflow,
    BackupScheduler,
    BetaWorkflow,
    ComplianceScheduler,
    InternalWorkflow,
    InventoryScheduler,
    collect_facts,
    installed,
    plugin,
    push_config,
)


def alpha_plugin(
    *,
    version: str = "1.0.0",
    workflows: tuple[type, ...] = (AlphaWorkflow,),
    activities: tuple[Any, ...] = (collect_facts,),
) -> WorkflowPluginDescriptor:
    """The baseline plugin each fingerprint case changes one thing about."""
    return plugin(
        "alpha-plugin",
        version=version,
        workflows=workflows,
        activities=activities,
        schedulers=(BackupScheduler,),
    )


def fingerprint(*descriptors: WorkflowPluginDescriptor) -> str:
    return registry_manifest(WorkflowRegistry.build(installed(*descriptors))).fingerprint


class TestInstalledManifest:
    def test_it_summarizes_the_builtin_plugin(self) -> None:
        manifest = registry_manifest(WorkflowRegistry.build())
        builtin = next(info for info in manifest.plugins if info.name == BUILTIN_PLUGIN_NAME)

        assert builtin == PluginInfo(
            name=BUILTIN_PLUGIN_NAME,
            version=metadata.version("nv-config-manager-workflows"),
            workflow_count=len(BUILTIN_WORKFLOWS),
            activity_count=len(BUILTIN_ACTIVITIES),
            scheduler_count=len(BUILTIN_SCHEDULERS),
        )
        assert {workflow_type_name(w) for w in BUILTIN_WORKFLOWS} <= set(manifest.workflows)
        assert {activity_name(a) for a in BUILTIN_ACTIVITIES} <= set(manifest.activities)
        assert "builtin.backup" in manifest.schedulers

    def test_names_are_sorted_and_unique(self) -> None:
        manifest = registry_manifest(WorkflowRegistry.build())

        for names in (manifest.workflows, manifest.activities, manifest.schedulers):
            assert list(names) == sorted(set(names))

    def test_local_test_workflows_are_left_out(self) -> None:
        manifest = registry_manifest(WorkflowRegistry.build())

        assert LOCAL_TEST_WORKFLOWS
        for workflow in LOCAL_TEST_WORKFLOWS:
            assert workflow.__name__ not in manifest.workflows

    def test_two_builds_produce_the_same_manifest(self) -> None:
        first = registry_manifest(WorkflowRegistry.build())
        second = registry_manifest(WorkflowRegistry.build())

        assert first == second
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", first.fingerprint)


class TestFingerprint:
    def test_it_changes_when_a_plugin_is_added(self) -> None:
        assert fingerprint(alpha_plugin()) != fingerprint(
            alpha_plugin(), plugin("zulu-plugin", version="1.0.0")
        )

    def test_it_changes_when_a_plugin_version_changes(self) -> None:
        assert fingerprint(alpha_plugin()) != fingerprint(alpha_plugin(version="1.0.1"))

    def test_it_changes_when_a_workflow_name_changes(self) -> None:
        assert fingerprint(alpha_plugin()) != fingerprint(
            alpha_plugin(workflows=(InternalWorkflow,))
        )

    def test_it_changes_when_an_activity_name_changes(self) -> None:
        assert fingerprint(alpha_plugin()) != fingerprint(alpha_plugin(activities=(push_config,)))

    def test_it_changes_when_a_scheduler_identity_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = fingerprint(alpha_plugin())
        monkeypatch.setattr(BackupScheduler, "scheduler_identity", "alpha-plugin.archive")

        assert fingerprint(alpha_plugin()) != before

    def test_it_ignores_the_order_a_plugin_declares_its_contributions_in(self) -> None:
        assert fingerprint(
            plugin(
                "zulu-plugin",
                workflows=(AlphaWorkflow, BetaWorkflow),
                activities=(collect_facts, push_config),
                schedulers=(InventoryScheduler, ComplianceScheduler),
            )
        ) == fingerprint(
            plugin(
                "zulu-plugin",
                workflows=(BetaWorkflow, AlphaWorkflow),
                activities=(push_config, collect_facts),
                schedulers=(ComplianceScheduler, InventoryScheduler),
            )
        )


class TestSerializedManifest:
    def test_it_round_trips_through_json_with_only_the_public_fields(self) -> None:
        manifest = registry_manifest(WorkflowRegistry.build())

        serialized = json.loads(json.dumps(asdict(manifest)))

        assert serialized["fingerprint"] == manifest.fingerprint
        assert set(serialized) == {
            "plugins",
            "workflows",
            "activities",
            "schedulers",
            "fingerprint",
        }

    def test_it_names_no_python_module(self) -> None:
        """Class paths are process-local; the Temporal names are the compatibility contract."""
        serialized = json.dumps(asdict(registry_manifest(WorkflowRegistry.build())))

        assert "nv_config_manager_workflows." not in serialized

    def test_it_carries_no_descriptor_metadata(self) -> None:
        sentinel = "descriptor-metadata-sentinel"
        descriptor = WorkflowPluginDescriptor(
            name="alpha-plugin",
            version="1.0.0",
            workflows=(AlphaWorkflow,),
            activities=(collect_facts,),
            schedulers=(BackupScheduler,),
            metadata={"support-contact": sentinel},
        )

        serialized = json.dumps(
            asdict(registry_manifest(WorkflowRegistry.build(installed(descriptor))))
        )

        assert sentinel not in serialized
        assert "support-contact" not in serialized
        assert AlphaWorkflow.__module__ not in serialized


class TestManifestCommand:
    def test_it_prints_the_installed_manifest_as_json(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main()

        printed = json.loads(capsys.readouterr().out)
        expected = asdict(registry_manifest(WorkflowRegistry.build()))
        assert printed == json.loads(json.dumps(expected))

    def test_a_failed_registry_build_exits_nonzero_and_prints_no_manifest(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fail(plugins: object = None) -> NoReturn:
            raise WorkflowPluginDiscoveryError('Workflow plugin "broken-plugin" failed to load')

        monkeypatch.setattr(WorkflowRegistry, "build", fail)

        with pytest.raises(SystemExit) as raised:
            main()

        captured = capsys.readouterr()
        assert raised.value.code == 1
        assert captured.out == ""
        assert captured.err == (
            'Workflow registry build failed: Workflow plugin "broken-plugin" failed to load\n'
        )
