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
"""Custom Nautobot job sources and ordered job runs for AIR simulations."""

from __future__ import annotations

import json
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Input, Label, Static, TextArea
from textual_fspicker import SelectDirectory

from nv_config_manager_installer.air_sim.sim_config import SimConfig


class JobDirectoryRow(Horizontal):
    """One editable local job-package directory."""

    DEFAULT_CSS = """
    JobDirectoryRow { height: auto; margin-bottom: 1; }
    JobDirectoryRow Input { width: 1fr; }
    JobDirectoryRow Button { width: auto; min-width: 9; margin-left: 1; }
    """

    def __init__(self, path: str) -> None:
        """Retain the saved path until its input is mounted."""
        super().__init__()
        self.path = path

    def compose(self) -> ComposeResult:
        """Build the directory input and its browse/remove controls."""
        yield Input(value=self.path, placeholder="/path/to/example_topology")
        yield Button("Browse", classes="browse-job-directory")
        yield Button("Remove", variant="error", classes="remove-custom-job")


class JobRunRow(Vertical):
    """One job class and its JSON arguments."""

    DEFAULT_CSS = """
    JobRunRow { height: auto; padding: 0 1 1 1; margin-bottom: 1; border: solid $panel; }
    JobRunRow TextArea { height: 5; margin-bottom: 1; }
    """

    def __init__(self, spec: Any) -> None:
        """Prepare editable fields while retaining the original saved entry."""
        super().__init__()
        self.spec = spec
        fields = spec if isinstance(spec, dict) else {}
        self._initial_job = str(fields.get("job", ""))
        raw_input = fields.get("input", "") if isinstance(spec, dict) else spec
        if isinstance(spec, dict) and raw_input is None:
            raw_input = ""
        try:
            self._initial_input = (
                raw_input if isinstance(raw_input, str) else json.dumps(raw_input, indent=2)
            )
        except (TypeError, ValueError):
            self._initial_input = repr(raw_input)

    def compose(self) -> ComposeResult:
        """Build the job class, JSON input and removal controls."""
        yield Label("Job class", classes="field-label")
        yield Input(
            value=self._initial_job,
            placeholder="custom.example_topology.jobs.design.ExampleTopologyDesign",
        )
        yield Label("Job inputs (JSON object; blank means no inputs)", classes="field-label")
        yield TextArea(self._initial_input, soft_wrap=True)
        yield Button("Remove job", variant="error", classes="remove-custom-job")

    def read_spec(self) -> Any:
        """Keep a malformed saved entry until the user edits or removes it."""
        job = self.query_one(Input).value
        inputs = self.query_one(TextArea).text
        if job == self._initial_job and inputs == self._initial_input:
            return self.spec
        fields = self.spec if isinstance(self.spec, dict) else {}
        return {**fields, "job": job.strip(), "input": inputs.strip()}


class CustomJobsPanel(Container):
    """Edit the existing extra_job_paths and extra_run_after_deploy settings."""

    DEFAULT_CSS = """
    CustomJobsPanel { height: auto; }
    #custom-job-directories, #custom-job-runs { height: auto; }
    #custom-jobs-error { height: auto; color: $error; margin-bottom: 1; }
    """

    def __init__(self, config: SimConfig, **kwargs: object) -> None:
        """Initialize the editor and track the latest configuration sync."""
        super().__init__(**kwargs)
        self._config = config
        self._syncing = False
        self._sync_generation = 0

    def compose(self) -> ComposeResult:
        """Build the saved directory/job rows and validation message."""
        yield Label("Custom topology jobs", classes="subsection-label")
        yield Static(
            "Upload local Nautobot job-package directories. Add the jobs to run after "
            "installation below, in execution order. Leave the job list empty to upload only.",
            classes="field-hint",
        )
        yield Button("+ Add Job Directory", id="add-custom-job-directory", classes="add-button")
        with Vertical(id="custom-job-directories"):
            for path in self._config.extra_job_paths:
                yield JobDirectoryRow(path)
        yield Button("+ Add Job to Run", id="add-custom-job-run", classes="add-button")
        with Vertical(id="custom-job-runs"):
            for spec in self._config.extra_run_after_deploy:
                yield JobRunRow(spec)
        yield Static(self._config.custom_jobs_error(), id="custom-jobs-error")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle adding, browsing and removing directory/job rows."""
        button = event.button
        if button.id == "add-custom-job-directory":
            event.stop()
            self._pick_directory()
        elif button.id == "add-custom-job-run":
            event.stop()
            await self.query_one("#custom-job-runs", Vertical).mount(JobRunRow({}))
        elif button.has_class("browse-job-directory"):
            event.stop()
            assert isinstance(button.parent, JobDirectoryRow)
            self._pick_directory(button.parent)
        elif button.has_class("remove-custom-job"):
            event.stop()
            assert isinstance(button.parent, (JobDirectoryRow, JobRunRow))
            await button.parent.remove()

    @work
    async def _pick_directory(self, row: JobDirectoryRow | None = None) -> None:
        """Select a directory and add or update its row when accepted."""
        picked = await self.app.push_screen_wait(
            SelectDirectory(title="Select job package directory")
        )
        if picked is None:
            return
        if row is None:
            await self.query_one("#custom-job-directories", Vertical).mount(
                JobDirectoryRow(str(picked))
            )
        else:
            row.query_one(Input).value = str(picked)

    def write_to_config(self, config: SimConfig) -> None:
        """Save mounted row edits unless a configuration sync is in progress."""
        if self._syncing:
            return  # The saved settings remain authoritative until rows finish mounting.
        config.extra_job_paths = [
            value
            for row in self.query(JobDirectoryRow)
            if (value := row.query_one(Input).value.strip())
        ]
        jobs = []
        for row in self.query(JobRunRow):
            job = row.query_one(Input).value.strip()
            inputs = row.query_one(TextArea).text.strip()
            if job or inputs or not isinstance(row.spec, dict):
                jobs.append(row.read_spec())
        config.extra_run_after_deploy = jobs
        self.query_one("#custom-jobs-error", Static).update(config.custom_jobs_error())

    def sync_from_config(self, config: SimConfig) -> None:
        """Schedule replacement of the rows from the latest saved configuration."""
        self._config = config
        self._syncing = True
        self._sync_generation += 1
        self._sync_rows(self._sync_generation)

    @work(exclusive=True)
    async def _sync_rows(self, generation: int) -> None:
        """Replace saved rows and release the guard for the latest sync on exit."""
        try:
            paths = self.query_one("#custom-job-directories", Vertical)
            runs = self.query_one("#custom-job-runs", Vertical)
            await paths.remove_children()
            await runs.remove_children()
            await paths.mount_all(JobDirectoryRow(path) for path in self._config.extra_job_paths)
            await runs.mount_all(JobRunRow(spec) for spec in self._config.extra_run_after_deploy)
            self.query_one("#custom-jobs-error", Static).update(self._config.custom_jobs_error())
        finally:
            if generation == self._sync_generation:
                self._syncing = False
