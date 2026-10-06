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
"""Check the built workflow package and fixture plugin outside the monorepo.

Run only by ``scripts/check-workflow-wheels.sh``, with ``python -I`` from a
virtual environment holding just the built wheels and a working directory
outside the repository, so that nothing can be imported from the source tree.
"""

import argparse
import asyncio
import importlib
import json
import pkgutil
import re
import subprocess
import sys
import tarfile
import uuid
import zipfile
from datetime import timedelta
from importlib.metadata import Distribution, PathDistribution, entry_points
from pathlib import Path, PurePosixPath

from nvcm_fixture_plugin.activities import FIXTURE_ACTIVITIES, echo
from nvcm_fixture_plugin.schedulers import FIXTURE_SCHEDULERS
from nvcm_fixture_plugin.workflows import (
    FIXTURE_WORKFLOWS,
    FixtureApiOnlyWorkflow,
    FixtureEchoWorkflow,
    FixtureInput,
)
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from nv_config_manager_workflows.converter import get_data_converter
from nv_config_manager_workflows.registration import (
    BUILTIN_ACTIVITIES,
    BUILTIN_WORKFLOWS,
    WORKFLOW_PLUGIN_ENTRY_POINT_GROUP,
    PluginInfo,
    WorkflowRegistry,
    registry_manifest,
)
from nv_config_manager_workflows.schedulers.builtin import BUILTIN_SCHEDULERS
from nv_config_manager_workflows.schedulers.runtime import (
    SchedulerRuntime,
    configure_scheduler_runtime,
)
from nv_config_manager_workflows.schedulers.schedule_ids import schedule_id

SIBLING_DISTRIBUTIONS = {
    "nv-config-manager-clients",
    "nv-config-manager-dcim",
    "nv-config-manager-infrastructure",
    "nv-config-manager-logging",
}
FIXTURE_PLUGIN = "nvcm-fixture"
FIXTURE_SCHEDULER = "nvcm-fixture.heartbeat"


def single(dist: Path, pattern: str) -> Path:
    """Return the one artifact in ``dist`` matching ``pattern``."""
    matches = sorted(dist.glob(pattern))
    assert len(matches) == 1, f"expected one {pattern} in {dist}, found {matches}"
    return matches[0]


def wheel_distribution(wheel: Path) -> Distribution:
    """Return the metadata of a wheel without installing it."""
    dist_infos = [p for p in zipfile.Path(wheel).iterdir() if p.name.endswith(".dist-info")]
    assert len(dist_infos) == 1, f"{wheel.name} has dist-info dirs {dist_infos}"
    return PathDistribution(dist_infos[0])


def entry_point_triples(distribution: Distribution) -> set[tuple[str, str, str]]:
    """Return each entry point as ``(group, name, value)``."""
    return {(ep.group, ep.name, ep.value) for ep in distribution.entry_points}


def check_artifacts(dist: Path) -> tuple[str, str]:
    """Inspect the built artifacts; return the workflows and fixture versions."""
    workflows_wheel = single(dist, "nv_config_manager_workflows-*.whl")
    with zipfile.ZipFile(workflows_wheel) as archive:
        names = archive.namelist()
    assert "nv_config_manager_workflows/py.typed" in names, "workflows wheel lacks py.typed"
    tests = [name for name in names if "tests" in PurePosixPath(name).parts]
    assert not tests, f"workflows wheel ships tests: {tests[:5]}"

    workflows = wheel_distribution(workflows_wheel)
    expected_entry_points = {
        (
            WORKFLOW_PLUGIN_ENTRY_POINT_GROUP,
            "builtin",
            "nv_config_manager_workflows.registration:builtin_plugin",
        ),
        (
            "console_scripts",
            "nv-config-manager-workflows-manifest",
            "nv_config_manager_workflows.registration.manifest:main",
        ),
    }
    assert expected_entry_points <= entry_point_triples(workflows), (
        f"workflows wheel entry points: {entry_point_triples(workflows)}"
    )
    requires = {re.split(r"[\s;<>=!~\[(]", r, maxsplit=1)[0] for r in workflows.requires or ()}
    assert SIBLING_DISTRIBUTIONS <= requires, f"workflows wheel requires {sorted(requires)}"

    sdist = single(dist, "nv_config_manager_workflows-*.tar.gz")
    with tarfile.open(sdist) as archive:
        leaked = [name for name in archive.getnames() if "tests/fixtures/plugin" in name]
    assert not leaked, f"workflows sdist ships the fixture plugin: {leaked[:5]}"

    fixture = wheel_distribution(single(dist, "nv_config_manager_fixture_plugin-*.whl"))
    fixture_entry_point = (
        WORKFLOW_PLUGIN_ENTRY_POINT_GROUP,
        FIXTURE_PLUGIN,
        "nvcm_fixture_plugin.registration:plugin",
    )
    assert fixture_entry_point in entry_point_triples(fixture), (
        f"fixture wheel entry points: {entry_point_triples(fixture)}"
    )
    return workflows.version, fixture.version


def check_service_package_absent() -> None:
    """The root service distribution must not be importable."""
    try:
        importlib.import_module("nv_config_manager")
    except ModuleNotFoundError as error:
        # A missing dependency of an installed nv_config_manager must not pass.
        assert error.name == "nv_config_manager", f"nv_config_manager is installed: {error}"
        return
    raise AssertionError("nv_config_manager is importable in the isolated environment")


def import_all(package_name: str) -> int:
    """Import a package and every module below it; return how many were imported."""
    package = importlib.import_module(package_name)
    count = 1
    # walk_packages yields each module before importing it, so a failing module
    # raises here instead of being skipped silently.
    for module in pkgutil.walk_packages(package.__path__, f"{package_name}."):
        importlib.import_module(module.name)
        count += 1
    return count


def check_module_locations() -> int:
    """Every loaded package module comes from the venv, not the source tree."""
    venv = Path(sys.prefix).resolve()
    checked = 0
    for name, module in sorted(sys.modules.items()):
        if not name.startswith(("nv_config_manager", "nvcm_fixture_plugin")):
            continue
        assert module.__file__, f"{name} has no __file__ (namespace package?)"
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(venv), (
            f"{name} was imported from {path}, not from the venv {venv}"
        )
        checked += 1
    return checked


def check_registry(workflows_version: str, fixture_version: str) -> WorkflowRegistry:
    """Discover both plugins, build the registry, and compare its manifest."""
    discovered = {ep.name for ep in entry_points(group=WORKFLOW_PLUGIN_ENTRY_POINT_GROUP)}
    assert discovered == {"builtin", FIXTURE_PLUGIN}, f"discovered plugins {discovered}"

    registry = WorkflowRegistry.build()
    manifest = registry_manifest(registry)
    expected_plugins = (
        PluginInfo(
            "builtin",
            workflows_version,
            len(BUILTIN_WORKFLOWS),
            len(BUILTIN_ACTIVITIES),
            len(BUILTIN_SCHEDULERS),
        ),
        PluginInfo(
            FIXTURE_PLUGIN,
            fixture_version,
            len(FIXTURE_WORKFLOWS),
            len(FIXTURE_ACTIVITIES),
            len(FIXTURE_SCHEDULERS),
        ),
    )
    assert manifest.plugins == expected_plugins, f"manifest plugins {manifest.plugins}"
    assert FIXTURE_SCHEDULER in manifest.schedulers, f"manifest schedulers {manifest.schedulers}"

    console_script = Path(sys.executable).parent / "nv-config-manager-workflows-manifest"
    # stderr is not captured, so a failure shows the console script's diagnostic.
    printed = json.loads(
        subprocess.run(
            [console_script], check=True, stdout=subprocess.PIPE, text=True, timeout=60
        ).stdout
    )
    assert printed["fingerprint"] == manifest.fingerprint, "console script manifest differs"
    return registry


async def check_workflow_execution(client: Client) -> None:
    """Run both fixture workflows and the fixture activity on the Temporal server."""
    task_queue = f"wheels-{uuid.uuid4()}"
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[FixtureEchoWorkflow, FixtureApiOnlyWorkflow],
        activities=[echo],
    ):
        echoed = await client.execute_workflow(
            FixtureEchoWorkflow.run,
            FixtureInput(message="wheels"),
            id=f"{task_queue}-echo",
            task_queue=task_queue,
            execution_timeout=timedelta(seconds=60),
        )
        returned = await client.execute_workflow(
            FixtureApiOnlyWorkflow.run,
            FixtureInput(message="wheels"),
            id=f"{task_queue}-api-only",
            task_queue=task_queue,
            execution_timeout=timedelta(seconds=60),
        )
    assert echoed == "nvcm-fixture: wheels", f"echo workflow returned {echoed!r}"
    assert returned == "wheels", f"API-only workflow returned {returned!r}"


async def check_scheduler(registry: WorkflowRegistry, client: Client) -> None:
    """Run the registered fixture scheduler on Temporal until it sleeps, then cancel it."""
    slept = asyncio.Event()

    async def temporal_client() -> Client:
        return client

    async def sleep_forever(_seconds: float) -> None:
        slept.set()
        await asyncio.Event().wait()

    configure_scheduler_runtime(
        SchedulerRuntime(
            temporal_client=temporal_client,
            workflow_roles=lambda _name: None,
            sleep=sleep_forever,
        )
    )
    (registration,) = [
        r for r in registry.scheduler_registrations if r.identity == FIXTURE_SCHEDULER
    ]
    runner = asyncio.create_task(registration.scheduler().run())
    sleeping = asyncio.create_task(slept.wait())
    try:
        await asyncio.wait((runner, sleeping), timeout=30, return_when=asyncio.FIRST_COMPLETED)
        assert not runner.done(), f"fixture scheduler stopped before sleeping: {runner!r}"
        assert sleeping.done(), "fixture scheduler did not reach its sleep within 30s"
    finally:
        sleeping.cancel()
        runner.cancel()
        await asyncio.wait((runner,), timeout=10)
    assert runner.cancelled(), f"fixture scheduler did not propagate cancellation: {runner!r}"

    heartbeat = schedule_id(FIXTURE_SCHEDULER, "heartbeat")
    # Raises if the schedule, with its plugin-typed workflow input, was not created.
    description = await client.get_schedule_handle(heartbeat).describe()
    action = description.schedule.action
    assert getattr(action, "workflow", None) == "FixtureApiOnlyWorkflow", f"{heartbeat}: {action}"


async def main() -> None:
    """Run every check and print a summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True, help="directory of built artifacts")
    args = parser.parse_args()

    workflows_version, fixture_version = check_artifacts(args.dist)
    print(f"ok artifacts: workflows {workflows_version}, fixture {fixture_version}")
    check_service_package_absent()
    print("ok nv_config_manager is not importable")
    imported = import_all("nv_config_manager_workflows") + import_all("nvcm_fixture_plugin")
    located = check_module_locations()
    print(f"ok imported {imported} modules; {located} package modules load from the venv")
    registry = check_registry(workflows_version, fixture_version)
    print("ok entry points, registry manifest, and console script list builtin + nvcm-fixture")
    async with await WorkflowEnvironment.start_local(data_converter=get_data_converter()) as env:
        await check_workflow_execution(env.client)
        print("ok fixture workflows and activity ran on a local Temporal server")
        await check_scheduler(registry, env.client)
        print("ok fixture scheduler created its Temporal schedule and propagated cancellation")
    print("PASS: workflow package and fixture plugin work without nv_config_manager")


if __name__ == "__main__":
    asyncio.run(main())
