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

import json
from unittest.mock import AsyncMock, Mock

import pytest
from textual.widgets import Button, Input, TextArea

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
            {"job": "custom.example.jobs.OldDesign", "input": {"dryrun": True}}
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


@pytest.mark.parametrize("inputs", ["", "  ", "{}", {"dryrun": True}])
def test_blank_or_object_job_inputs_are_valid(inputs) -> None:
    config = SimConfig(
        extra_run_after_deploy=[{"job": "custom.example.jobs.Design", "input": inputs}]
    )
    assert config.custom_jobs_error() == ""


def test_job_inputs_without_class_are_invalid() -> None:
    config = SimConfig(extra_run_after_deploy=[{"job": "", "input": '{"dryrun": true}'}])
    assert "needs a job class" in config.custom_jobs_error()
