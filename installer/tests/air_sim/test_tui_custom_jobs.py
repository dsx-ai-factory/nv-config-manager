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
"""Custom topology jobs survive editing, file selection, and saved AIR profiles."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Button, Input, Static, TextArea

from nv_config_manager_installer.air_sim.installer_config import build_content_jobs
from nv_config_manager_installer.air_sim.sim_config import SimConfig
from nv_config_manager_installer.tui.air_sim.app import NVCMAirSimApp
from nv_config_manager_installer.tui.air_sim.screens.custom_jobs import (
    CustomJobsPanel,
    JobDirectoryRow,
    JobRunRow,
)
from nv_config_manager_installer.tui.air_sim.screens.launch import LaunchScreen


@pytest.mark.asyncio
async def test_custom_jobs_edit_save_reload_and_generate_install_config(tmp_path) -> None:
    directory = tmp_path / "example_topology"
    directory.mkdir()
    profile = tmp_path / "air-sim.yaml"
    config = SimConfig(
        run_mock_topology_job=False,
        extra_job_paths=[str(directory)],
        extra_run_after_deploy=[
            {
                "job": "custom.example.jobs.OldDesign",
                "input": {"dryrun": True},
                "label": "Example topology",
                "metadata": {"source": "external"},
            }
        ],
    )
    app = NVCMAirSimApp(config=config, config_path=profile)
    async with app.run_test(size=(180, 80)) as pilot:
        await pilot.pause()
        panel = app.query_one(CustomJobsPanel)
        assert panel.region.bottom <= panel.parent.region.bottom
        row = panel.query_one(JobRunRow)
        assert json.loads(row.query_one(TextArea).text) == {"dryrun": True}
        row.query_one(Input).value = "custom.example_topology.jobs.design.ExampleTopologyDesign"
        row.query_one(TextArea).load_text('{"deployment_name": "demo", "dryrun": false}')

        add_job = panel.query_one("#add-custom-job-run", Button)
        add_job.scroll_visible(animate=False)
        await pilot.pause()
        await pilot.click(add_job)
        await pilot.pause()
        second = list(panel.query(JobRunRow))[1]
        second.query_one(Input).value = "custom.example_topology.jobs.Verify"
        second.query_one(TextArea).load_text('{"devices": ["leaf01", "leaf02"]}')
        app.switch_section("options")
        app.switch_section("topology")
        app.collect_config()  # Saving before the new rows mount must preserve the jobs.
        await app.workers.wait_for_complete()
        app.action_save()

    saved = SimConfig.from_yaml(profile)
    paths, runs = build_content_jobs(saved)
    assert paths == [{"path": str(directory)}]
    assert [job["job"] for job in runs] == [
        "custom.example_topology.jobs.design.ExampleTopologyDesign",
        "custom.example_topology.jobs.Verify",
    ]
    assert json.loads(runs[0]["input"]) == {"deployment_name": "demo", "dryrun": False}
    assert json.loads(runs[1]["input"]) == {"devices": ["leaf01", "leaf02"]}
    assert saved.extra_run_after_deploy[0]["label"] == "Example topology"
    assert saved.extra_run_after_deploy[0]["metadata"] == {"source": "external"}
    assert saved.run_mock_topology_job is False

    async with NVCMAirSimApp(config=saved).run_test(size=(180, 80)) as pilot:
        await pilot.pause()
        assert len(pilot.app.query(JobDirectoryRow)) == 1
        assert [row.query_one(Input).value for row in pilot.app.query(JobRunRow)] == [
            job["job"] for job in runs
        ]


@pytest.mark.asyncio
async def test_directory_picker_cancel_add_and_remove_preserve_job_edits(
    tmp_path, monkeypatch
) -> None:
    config = SimConfig(
        run_mock_topology_job=False,
        extra_job_paths=[str(tmp_path / "old")],
        extra_run_after_deploy=[{"job": "custom.example.jobs.Design", "input": "{}"}],
    )
    app = NVCMAirSimApp(config=config)
    picker = AsyncMock(return_value=None)
    monkeypatch.setattr(app, "push_screen_wait", picker)
    async with app.run_test(size=(180, 80)) as pilot:
        await pilot.pause()
        panel = app.query_one(CustomJobsPanel)
        panel.query_one(JobRunRow).query_one(TextArea).load_text('{"name": "edited"}')
        panel.query_one("#add-custom-job-directory", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(panel.query(JobDirectoryRow)) == 1

        picker.return_value = tmp_path / "example_topology"
        panel.query_one("#add-custom-job-directory", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(panel.query(JobDirectoryRow)) == 2
        first = list(panel.query(JobDirectoryRow))[0]
        first.query_one(".remove-custom-job", Button).press()
        await pilot.pause()

        picker.return_value = tmp_path / "updated_topology"
        panel.query_one(JobDirectoryRow).query_one(".browse-job-directory", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        app.collect_config()

    assert config.extra_job_paths == [str(tmp_path / "updated_topology")]
    assert json.loads(config.extra_run_after_deploy[0]["input"]) == {"name": "edited"}


@pytest.mark.asyncio
async def test_removing_first_job_keeps_second_job_edits() -> None:
    config = SimConfig(
        run_mock_topology_job=False,
        extra_run_after_deploy=[
            {"job": "custom.example.jobs.First", "input": ""},
            {"job": "custom.example.jobs.Second", "input": ""},
        ],
    )
    app = NVCMAirSimApp(config=config)
    async with app.run_test(size=(180, 80)) as pilot:
        await pilot.pause()
        first, second = app.query(JobRunRow)
        second.query_one(TextArea).load_text('{"keep": true}')
        first.query_one(".remove-custom-job", Button).press()
        await pilot.pause()
        app.collect_config()

    assert config.extra_run_after_deploy == [
        {"job": "custom.example.jobs.Second", "input": '{"keep": true}'}
    ]


@pytest.mark.asyncio
async def test_invalid_json_blocks_tui_launch(monkeypatch) -> None:
    config = SimConfig(
        topology_path="fabric.yaml",
        generate_fabric_from_mock_context=False,
        run_mock_topology_job=False,
        extra_run_after_deploy=[{"job": "custom.example.jobs.Design", "input": "{"}],
    )
    app = NVCMAirSimApp(config=config)
    async with app.run_test(size=(180, 80)) as pilot:
        await pilot.pause()
        app.switch_section("launch")
        launch = app.query_one(LaunchScreen)
        runner = Mock()
        monkeypatch.setattr(launch, "_run_orchestrator", runner)
        assert "valid JSON" in launch.validate_provider_config()
        launch._start_bringup()
        runner.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spec",
    [
        None,
        "invalid saved entry",
        [],
        {"job": None, "input": ""},
        {"job": "custom.example.jobs.Design", "input": {"unsupported": {1, 2}}},
    ],
)
async def test_malformed_saved_jobs_can_be_corrected(tmp_path, monkeypatch, spec) -> None:
    profile = tmp_path / "air-sim.yaml"
    SimConfig(
        ngc_api_key="",
        topology_path="fabric.yaml",
        generate_fabric_from_mock_context=False,
        run_mock_topology_job=False,
        extra_run_after_deploy=[spec],
    ).to_yaml(profile)
    config = SimConfig.from_yaml(profile)
    app = NVCMAirSimApp(config=config, config_path=profile)

    async with app.run_test(size=(180, 80)) as pilot:
        await pilot.pause()
        panel = app.query_one(CustomJobsPanel)
        assert "Custom job 1" in str(panel.query_one("#custom-jobs-error", Static).render())
        app.collect_config()
        assert config.extra_run_after_deploy == [spec]
        app.switch_section("launch")
        launch = app.query_one(LaunchScreen)
        runner = Mock()
        monkeypatch.setattr(launch, "_run_orchestrator", runner)
        assert "Custom job 1" in launch.validate_provider_config()
        launch._start_bringup()
        runner.assert_not_called()

        app.switch_section("topology")
        await app.workers.wait_for_complete()
        row = panel.query_one(JobRunRow)
        row.query_one(Input).value = "custom.example.jobs.Design"
        row.query_one(TextArea).load_text("{}")
        app.collect_config()
        assert config.custom_jobs_error() == ""
        assert config.extra_run_after_deploy == [
            {"job": "custom.example.jobs.Design", "input": "{}"}
        ]


@pytest.mark.asyncio
async def test_malformed_saved_job_can_be_removed() -> None:
    config = SimConfig(extra_run_after_deploy=[None])
    app = NVCMAirSimApp(config=config)

    async with app.run_test(size=(180, 80)) as pilot:
        await pilot.pause()
        panel = app.query_one(CustomJobsPanel)
        panel.query_one(JobRunRow).query_one(".remove-custom-job", Button).press()
        await pilot.pause()
        app.collect_config()

        assert config.extra_run_after_deploy == []
        assert config.custom_jobs_error() == ""
        assert str(panel.query_one("#custom-jobs-error", Static).render()) == ""


@pytest.mark.asyncio
async def test_failed_sync_releases_write_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    config = SimConfig(
        run_mock_topology_job=False,
        extra_run_after_deploy=[{"job": "custom.example.jobs.Design", "input": "{}"}],
    )
    app = NVCMAirSimApp(config=config)
    async with app.run_test(size=(180, 80)) as pilot:
        await pilot.pause()
        panel = app.query_one(CustomJobsPanel)
        paths = panel.query_one("#custom-job-directories", Vertical)
        mount_all = paths.mount_all
        monkeypatch.setattr(paths, "mount_all", AsyncMock(side_effect=RuntimeError("mount failed")))
        panel._syncing = True

        # Exercise the worker body without the expected failure exiting the app.
        with pytest.raises(RuntimeError, match="mount failed"):
            await CustomJobsPanel._sync_rows.__wrapped__(panel, panel._sync_generation)
        assert panel._syncing is False

        monkeypatch.setattr(paths, "mount_all", mount_all)
        panel.sync_from_config(config)
        await app.workers.wait_for_complete()
        panel.query_one(JobRunRow).query_one(TextArea).load_text('{"recovered": true}')
        panel.write_to_config(config)
        assert config.extra_run_after_deploy[0]["input"] == '{"recovered": true}'


@pytest.mark.asyncio
async def test_cancelled_sync_keeps_latest_sync_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    first = SimConfig(extra_run_after_deploy=[{"job": "custom.example.jobs.First", "input": ""}])
    latest = SimConfig(extra_run_after_deploy=[{"job": "custom.example.jobs.Latest", "input": ""}])
    app = NVCMAirSimApp(config=first)
    async with app.run_test(size=(180, 80)) as pilot:
        await pilot.pause()
        panel = app.query_one(CustomJobsPanel)
        paths = panel.query_one("#custom-job-directories", Vertical)
        mount_all = paths.mount_all
        first_started = asyncio.Event()
        first_cancelled = asyncio.Event()
        latest_started = asyncio.Event()
        release_latest = asyncio.Event()
        calls = 0

        async def delayed_mount(widgets: Iterable[Widget]) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                try:
                    await asyncio.Future[None]()
                finally:
                    first_cancelled.set()
            else:
                latest_started.set()
                await release_latest.wait()
            await mount_all(widgets)

        monkeypatch.setattr(paths, "mount_all", delayed_mount)
        panel.sync_from_config(first)
        await asyncio.wait_for(first_started.wait(), timeout=5)
        panel.sync_from_config(latest)
        await asyncio.wait_for(first_cancelled.wait(), timeout=5)
        await asyncio.wait_for(latest_started.wait(), timeout=5)
        assert panel._syncing is True
        panel.write_to_config(latest)
        assert latest.extra_run_after_deploy == [{"job": "custom.example.jobs.Latest", "input": ""}]

        release_latest.set()
        await pilot.pause()  # Let the cancelled worker leave the manager before waiting.
        await app.workers.wait_for_complete()
        assert panel._syncing is False
        assert panel.query_one(JobRunRow).query_one(Input).value == "custom.example.jobs.Latest"


@pytest.mark.asyncio
@pytest.mark.parametrize("input_line", ["input:", "input: null"])
async def test_blank_yaml_inputs_render_as_empty(tmp_path: Path, input_line: str) -> None:
    profile = tmp_path / "air-sim.yaml"
    profile.write_text(
        "run_mock_topology_job: false\n"
        "extra_run_after_deploy:\n"
        "  - job: custom.example.jobs.Design\n"
        f"    {input_line}\n"
    )
    config = SimConfig.from_yaml(profile)
    assert config.custom_jobs_error() == ""

    app = NVCMAirSimApp(config=config)
    async with app.run_test(size=(180, 80)) as pilot:
        await pilot.pause()
        assert app.query_one(JobRunRow).query_one(TextArea).text == ""
        app.collect_config()

    _, runs = build_content_jobs(config)
    assert runs == [{"job": "custom.example.jobs.Design", "input": ""}]


@pytest.mark.parametrize("job", ["", " \t "])
@pytest.mark.parametrize("inputs", [None, "", "  "])
def test_blank_jobs_are_ignored_consistently(job: str, inputs: str | None) -> None:
    config = SimConfig(
        run_mock_topology_job=False,
        extra_run_after_deploy=[{"job": job, "input": inputs}],
    )
    assert config.custom_jobs_error() == ""
    assert build_content_jobs(config) == ([], [])


@pytest.mark.parametrize("inputs", [None, "", "  ", "{}", {"dryrun": True}])
def test_blank_or_object_job_inputs_are_valid(inputs: str | dict[str, bool] | None) -> None:
    config = SimConfig(
        extra_run_after_deploy=[{"job": "custom.example.jobs.Design", "input": inputs}]
    )
    assert config.custom_jobs_error() == ""


def test_job_inputs_without_class_are_invalid() -> None:
    config = SimConfig(extra_run_after_deploy=[{"job": "", "input": '{"dryrun": true}'}])
    assert "needs a job class" in config.custom_jobs_error()
