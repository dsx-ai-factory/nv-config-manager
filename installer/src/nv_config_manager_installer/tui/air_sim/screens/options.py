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
"""Options screen - DSX Air auth, source settings, and advanced timing."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Input, Label, RadioButton, RadioSet, Static

from nv_config_manager_installer.air_sim.sim_config import SimConfig, generate_oob_ssh_password
from nv_config_manager_installer.tui.widgets import LabeledSwitch

_SIZES = ["small", "medium", "large"]


class OptionsScreen(Container):
    """Configure DSX Air auth, source settings, size, branch, and timeouts."""

    def __init__(self, config: SimConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config

    def compose(self) -> ComposeResult:
        yield Label("Options", classes="section-title")
        yield Label("─" * 40, classes="section-divider")

        yield Label("DSX Air / Auth", classes="subsection-label")
        yield Label("NGC API Key  (or set NGC_API_KEY env var)", classes="field-label")
        yield Input(
            value=self._config.ngc_api_key,
            password=True,
            placeholder="nvapi-...",
            id="ngc-api-key",
        )
        yield LabeledSwitch(
            "Use Public DSX Air",
            value=not self._config.use_internal,
            id="use-public-air",
        )
        yield Label("OOB SSH Password", classes="field-label")
        yield Input(
            value=self._config.oob_ssh_password,
            placeholder="generated password for nvcm@oob-mgmt-server",
            id="oob-ssh-password",
        )

        yield Label("─" * 40, classes="section-divider")
        yield Label("Source", classes="subsection-label")
        yield Label(
            "Git Token  (optional; only needed for private forks)",
            classes="field-label",
        )
        yield Input(
            value=self._config.git_token,
            password=True,
            placeholder="token for a private fork",
            id="git-token",
        )
        yield Label("Config Manager Repository", classes="field-label")
        yield Input(
            value=self._config.config_manager_repo,
            placeholder="Git URL or local checkout path",
            id="config-manager-repo",
        )
        yield Static(
            "Enter a Git URL to clone or a local checkout path to upload.",
            classes="field-hint",
        )

        yield Label("─" * 40, classes="section-divider")
        yield Label("Deployment", classes="subsection-label")
        yield Label("nv-config-manager Git Ref", classes="field-label")
        yield Input(value=self._config.config_manager_ref, id="config-manager-ref")
        yield Static("", id="build-mode-hint", classes="field-hint")
        yield Label(
            "Cumulus Version Override  (leave blank to use topology values)",
            classes="field-label",
        )
        yield Input(
            value=self._config.cumulus_version,
            placeholder="5.16.1",
            id="cumulus-version",
        )

        yield Label("─" * 40, classes="section-divider")
        yield Label("Advanced", classes="subsection-label")
        yield Label("Deployment Size", classes="field-label")
        with RadioSet(id="size-radio"):
            for s in _SIZES:
                yield RadioButton(s, id=f"size-{s}", value=self._config.size == s)
        yield Label("Cloud-init Wait Timeout (seconds)", classes="field-label")
        yield Input(value=str(self._config.wait_timeout), id="wait-timeout")
        yield Label("Deploy Timeout (seconds)", classes="field-label")
        yield Input(value=str(self._config.deploy_timeout), id="deploy-timeout")

    def on_mount(self) -> None:
        self._update_build_mode_hint()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "config-manager-ref":
            self._update_build_mode_hint()

    def _update_build_mode_hint(self) -> None:
        branch = self.query_one("#config-manager-ref", Input).value.strip()
        hint = self.query_one("#build-mode-hint", Static)
        ref = branch or "main"
        hint.update(
            f"[dim]Images will be built locally from nv-config-manager ref {ref!r}; "
            "registry pulls are disabled for DSX Air demos.[/dim]"
        )

    def write_to_config(self, config: SimConfig) -> None:
        config.ngc_api_key = self.query_one("#ngc-api-key", Input).value.strip()
        config.use_internal = not self.query_one("#use-public-air", LabeledSwitch).value
        oob_ssh_password = (
            self.query_one("#oob-ssh-password", Input).value.strip() or generate_oob_ssh_password()
        )
        config.oob_ssh_password = oob_ssh_password
        self.query_one("#oob-ssh-password", Input).value = oob_ssh_password
        config.git_token = self.query_one("#git-token", Input).value.strip()
        config.config_manager_repo = self.query_one("#config-manager-repo", Input).value.strip()
        config.config_manager_ref = self.query_one("#config-manager-ref", Input).value.strip()
        config.cumulus_version = self.query_one("#cumulus-version", Input).value.strip()
        for s in _SIZES:
            if self.query_one(f"#size-{s}", RadioButton).value:
                config.size = s
                break
        try:
            config.wait_timeout = int(self.query_one("#wait-timeout", Input).value)
        except ValueError:
            self.app.notify("Invalid wait timeout - must be an integer", severity="warning")
        try:
            config.deploy_timeout = int(self.query_one("#deploy-timeout", Input).value)
        except ValueError:
            self.app.notify("Invalid deploy timeout - must be an integer", severity="warning")

    def sync_from_config(self, config: SimConfig) -> None:
        self.query_one("#ngc-api-key", Input).value = config.ngc_api_key
        self.query_one("#use-public-air", LabeledSwitch).value = not config.use_internal
        self.query_one("#oob-ssh-password", Input).value = config.oob_ssh_password
        self.query_one("#git-token", Input).value = config.git_token
        self.query_one("#config-manager-repo", Input).value = config.config_manager_repo
        self.query_one("#config-manager-ref", Input).value = config.config_manager_ref
        self.query_one("#cumulus-version", Input).value = config.cumulus_version
        for s in _SIZES:
            self.query_one(f"#size-{s}", RadioButton).value = config.size == s
        self.query_one("#wait-timeout", Input).value = str(config.wait_timeout)
        self.query_one("#deploy-timeout", Input).value = str(config.deploy_timeout)
        self._update_build_mode_hint()

    def get_status(self, config: SimConfig) -> str:
        if not config.ngc_api_key:
            return "[!]"
        return "[*]"
