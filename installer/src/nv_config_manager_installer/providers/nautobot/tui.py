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
"""Ingest Data screen — Nautobot jobs and post-deploy job execution.

Contributed to the install wizard by the bundled Nautobot provider.
"""

from __future__ import annotations

from collections.abc import Callable

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.css.query import NoMatches
from textual.widgets import Button, Input, Label, RadioButton, RadioSet
from textual_fspicker import SelectDirectory

from nv_config_manager_installer.providers.nautobot.settings import (
    NautobotSettings,
    nautobot_settings,
    store_settings,
)
from nv_config_manager_installer.schema import JobPath, NVConfigManagerInstallConfig, PostDeployJob
from nv_config_manager_installer.tui.screens.node_picker import NodeSelectorPanel


class IngestDataScreen(Container):
    """Custom Nautobot jobs and post-deploy job execution."""

    def __init__(self, config: NVConfigManagerInstallConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._settings: NautobotSettings = nautobot_settings(config)

    def compose(self) -> ComposeResult:
        ct = self._settings
        jc = ct.jobs_config
        yield Label("Ingest Data", classes="section-title")
        yield Label("─" * 40, classes="section-divider")

        warning = Label(
            "⚠ Nautobot is external — custom jobs cannot be loaded via PVC mount and "
            "post-deploy jobs cannot be run. Configure or run them on the external "
            "Nautobot instance directly.",
            id="content-nautobot-warning",
        )
        warning.styles.color = "yellow"
        warning.styles.background = "darkred"
        warning.styles.padding = (0, 1)
        warning.display = not self._config.dcim.local
        yield warning

        # -- Custom jobs --
        yield Label("Custom Nautobot Jobs", classes="field-label")
        yield Label("Paths to job directories or .tar.gz files", classes="section-divider")
        yield Button("+ Add Job Path", id="add-job-path", classes="add-button")
        yield Vertical(id="job-paths-list")

        # -- Post-deploy jobs --
        yield Label("Post-Deploy Jobs", classes="field-label")
        yield Label("Jobs to run automatically after deployment", classes="section-divider")
        yield Button("+ Add Post-Deploy Job", id="add-post-deploy", classes="add-button")
        yield Vertical(id="post-deploy-list")

        # -- Jobs PVC configuration --
        yield Label("Jobs PVC Storage Class (optional)", classes="field-label")
        yield Input(
            value=jc.storage_class,
            placeholder="e.g. nfs-client  (leave empty for cluster default)",
            id="jobs-storage-class",
        )

        yield Label("Jobs PVC Access Mode", classes="field-label")
        with RadioSet(id="jobs-access-mode"):
            yield RadioButton(
                "ReadWriteOnce  (single-node, no NFS required)",
                value=jc.access_mode != "ReadWriteMany",
                id="jobs-access-rwo",
            )
            yield RadioButton(
                "ReadWriteMany  (multi-node, requires NFS or RWX storage class)",
                value=jc.access_mode == "ReadWriteMany",
                id="jobs-access-rwx",
            )

        yield NodeSelectorPanel("jobs", jc.node_selector, id="jobs-node-selector")

    def on_mount(self) -> None:
        self._rebuild_all()

    def _rebuild_all(self) -> None:
        self._rebuild_job_paths()
        self._rebuild_post_deploy()

    def _rebuild_job_paths(self) -> None:
        container = self.query_one("#job-paths-list", Vertical)
        container.remove_children()
        for i, jp in enumerate(self._settings.jobs):
            row = Container(classes="account-card")
            row.compose_add_child(
                Input(value=jp.path, placeholder="/path/to/jobs", id=f"job-{i}-path")
            )
            row.compose_add_child(
                Button("Remove", variant="error", id=f"job-{i}-remove", classes="remove-button")
            )
            container.mount(row)

    def _rebuild_post_deploy(self) -> None:
        container = self.query_one("#post-deploy-list", Vertical)
        container.remove_children()
        for i, pdj in enumerate(self._settings.run_after_deploy):
            row = Container(classes="account-card")
            row.compose_add_child(Label("Job class", classes="field-label"))
            row.compose_add_child(
                Input(value=pdj.job, placeholder="module.Class", id=f"pdj-{i}-job")
            )
            row.compose_add_child(Label("Input JSON", classes="field-label"))
            row.compose_add_child(
                Input(value=pdj.input, placeholder='{"key": "value"}', id=f"pdj-{i}-input")
            )
            row.compose_add_child(
                Button("Remove", variant="error", id=f"pdj-{i}-remove", classes="remove-button")
            )
            container.mount(row)

    def _remove_by_id(
        self, bid: str, prefix: str, items: list[object], rebuild: Callable[[], None]
    ) -> None:
        idx = int(bid.split("-")[1])
        if 0 <= idx < len(items):
            items.pop(idx)
        rebuild()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "add-job-path":
            self._pick_path()
        elif bid == "add-post-deploy":
            self._settings.run_after_deploy.append(PostDeployJob(job=""))
            self._rebuild_post_deploy()
        elif bid.startswith("job-") and bid.endswith("-remove"):
            self._remove_by_id(bid, "job", self._settings.jobs, self._rebuild_job_paths)
        elif bid.startswith("pdj-") and bid.endswith("-remove"):
            self._remove_by_id(
                bid, "pdj", self._settings.run_after_deploy, self._rebuild_post_deploy
            )

    @work
    async def _pick_path(self) -> None:
        dialog = SelectDirectory(title="Select job directory")
        picked = await self.app.push_screen_wait(dialog)
        if picked is None:
            return
        self._collect_all()
        self._settings.jobs.append(JobPath(path=str(picked)))
        self._rebuild_job_paths()

    def _collect_all(self) -> None:
        ct = self._settings

        jobs: list[JobPath] = []
        for i in range(len(ct.jobs)):
            try:
                jobs.append(JobPath(path=self.query_one(f"#job-{i}-path", Input).value))
            except NoMatches:
                break
        ct.jobs = jobs

        try:
            jc = ct.jobs_config
            jc.storage_class = self.query_one("#jobs-storage-class", Input).value.strip()
            jc.access_mode = (
                "ReadWriteMany"
                if self.query_one("#jobs-access-rwx", RadioButton).value
                else "ReadWriteOnce"
            )
            jc.node_selector = self.query_one("#jobs-node-selector", NodeSelectorPanel).collect()
        except Exception:
            pass

        pdjs: list[PostDeployJob] = []
        for i in range(len(ct.run_after_deploy)):
            try:
                job_inp = self.query_one(f"#pdj-{i}-job", Input)
                input_inp = self.query_one(f"#pdj-{i}-input", Input)
                pdjs.append(PostDeployJob(job=job_inp.value, input=input_inp.value))
            except NoMatches:
                break
        ct.run_after_deploy = pdjs

    def write_to_config(self, config: NVConfigManagerInstallConfig) -> None:
        self._collect_all()
        store_settings(config, self._settings)

    def sync_from_config(self, config: NVConfigManagerInstallConfig) -> None:
        self._config = config
        self._settings = nautobot_settings(config)
        self._rebuild_all()
        try:
            self.query_one("#content-nautobot-warning", Label).display = not config.dcim.local
        except Exception:
            pass
        jc = self._settings.jobs_config
        try:
            self.query_one("#jobs-storage-class", Input).value = jc.storage_class
            self.query_one("#jobs-access-rwo", RadioButton).value = (
                jc.access_mode != "ReadWriteMany"
            )
            self.query_one("#jobs-access-rwx", RadioButton).value = (
                jc.access_mode == "ReadWriteMany"
            )
            self.query_one("#jobs-node-selector", NodeSelectorPanel).set_selector(jc.node_selector)
        except Exception:
            pass

    def get_status(self, config: NVConfigManagerInstallConfig) -> str:
        if not config.dcim.local:
            return "[~]"
        return "[*]"
