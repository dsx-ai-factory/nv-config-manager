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
"""Workflows configuration screen — Temporal RBAC."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Input, Label, Select

from nv_config_manager_installer.schema import (
    NVConfigManagerInstallConfig,
    TemporalDeploymentConfig,
    WorkflowRBACOverride,
    get_known_workflows,
)

_ROLES_PLACEHOLDER = "e.g. all"


class _OverrideCard(Vertical):
    """Editable card for a single workflow RBAC override."""

    def __init__(self, override: WorkflowRBACOverride, index: int, **kwargs: object) -> None:
        super().__init__(**kwargs, classes="account-card")
        self._override = override
        self._index = index

    def compose(self) -> ComposeResult:
        o = self._override
        idx = self._index
        prefix = f"rbac-ov-{idx}"

        with Horizontal(classes="account-title-row"):
            yield Label(o.name, classes="account-header")
            yield Button(
                "Remove", variant="error", id=f"{prefix}-remove", classes="remove-button-inline"
            )

        with Horizontal(classes="compact-field-row"):
            with Vertical(classes="compact-field"):
                yield Label("Read Roles", classes="field-label-compact")
                yield Input(
                    value=", ".join(o.read_roles),
                    placeholder=_ROLES_PLACEHOLDER,
                    id=f"{prefix}-read",
                )
            with Vertical(classes="compact-field"):
                yield Label("Execute Roles", classes="field-label-compact")
                yield Input(
                    value=", ".join(o.execute_roles),
                    placeholder=_ROLES_PLACEHOLDER,
                    id=f"{prefix}-exec",
                )

    def collect(self) -> WorkflowRBACOverride:
        prefix = f"rbac-ov-{self._index}"
        return WorkflowRBACOverride(
            name=self._override.name,
            read_roles=_parse_roles(self.query_one(f"#{prefix}-read", Input).value),
            execute_roles=_parse_roles(self.query_one(f"#{prefix}-exec", Input).value),
        )


def _parse_roles(text: str) -> list[str]:
    """Split a comma-separated roles string into a clean list."""
    return [r.strip() for r in text.split(",") if r.strip()]


class WorkflowsScreen(Container):
    """Temporal workflow settings — RBAC overrides."""

    def __init__(self, config: NVConfigManagerInstallConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config

    def compose(self) -> ComposeResult:
        rbac = self._config.rbac
        temporal = self._config.temporal

        yield Label("Workflows", classes="section-title")
        yield Label("─" * 40, classes="section-divider")
        yield Label("Managed Temporal Server", classes="section-title")
        with Container(id="temporal-server-fields"):
            yield Label(
                "Leave these fields blank to use the deployment size profile and chart defaults."
            )
            with Horizontal(classes="compact-field-row"):
                with Vertical(classes="compact-field"):
                    yield Label("History Replicas", classes="field-label-compact")
                    yield Input(
                        value=str(temporal.history_replicas) if temporal.history_replicas else "",
                        placeholder="deployment default",
                        type="integer",
                        restrict=r"[0-9]*",
                        id="temporal-history-replicas",
                    )
                with Vertical(classes="compact-field"):
                    yield Label("History Shards", classes="field-label-compact")
                    yield Input(
                        value=str(temporal.num_history_shards)
                        if temporal.num_history_shards
                        else "",
                        placeholder="inherits History replicas",
                        type="integer",
                        restrict=r"[0-9]*",
                        id="temporal-history-shards",
                    )
            yield Label(
                "Warning: Blank History Shards follows History Replicas. For an initialized "
                "cluster, pin the current shard count before changing replicas.",
                id="temporal-history-shards-warning",
            )

        yield Label("")
        yield Label("Workflow Authorization", classes="section-title")
        yield Label(
            "Configure Temporal workflow authorization. All known workflows "
            "receive the default roles unless explicitly overridden below.",
        )

        yield Label("Admin Roles", classes="field-label")
        yield Input(
            value=", ".join(rbac.admin_roles),
            placeholder=_ROLES_PLACEHOLDER,
            id="rbac-admin-roles",
        )
        yield Label("Default Read Roles", classes="field-label")
        yield Input(
            value=", ".join(rbac.default_read_roles),
            placeholder=_ROLES_PLACEHOLDER,
            id="rbac-default-read",
        )
        yield Label("Default Execute Roles", classes="field-label")
        yield Input(
            value=", ".join(rbac.default_execute_roles),
            placeholder=_ROLES_PLACEHOLDER,
            id="rbac-default-exec",
        )

        yield Label("")
        yield Label("Per-Workflow Overrides", classes="section-title")
        yield Label("─" * 40, classes="section-divider")
        yield Label("Workflows listed here use custom roles instead of the defaults above.")

        with Horizontal(classes="compact-field-row"):
            yield Select(
                self._available_workflow_options(),
                prompt="Select workflow to override",
                id="rbac-workflow-select",
            )
            yield Button("+ Add Override", id="rbac-add-override", variant="primary")

        yield Vertical(id="rbac-overrides-list")

    def on_mount(self) -> None:
        self._update_temporal_visibility()
        self._rebuild_overrides()

    def _managed_temporal_enabled(self) -> bool:
        return (
            self._config.services.temporal and not self._config.external_services.temporal.address
        )

    def _update_temporal_visibility(self) -> None:
        self.query_one("#temporal-server-fields").display = self._managed_temporal_enabled()

    def _optional_int(self, widget_id: str) -> int:
        input_widget = self.query_one(widget_id, Input)
        value = input_widget.value.strip()
        if value and not value.isdecimal():
            input_widget.value = ""
            return 0
        return int(value) if value else 0

    def _available_workflow_options(self) -> list[tuple[str, str]]:
        overridden = {o.name for o in self._config.rbac.workflow_overrides}
        return [(name, name) for name in get_known_workflows() if name not in overridden]

    def _refresh_select(self) -> None:
        select = self.query_one("#rbac-workflow-select", Select)
        select.set_options(self._available_workflow_options())

    def _rebuild_overrides(self) -> None:
        container = self.query_one("#rbac-overrides-list", Vertical)
        container.remove_children()
        for i, override in enumerate(self._config.rbac.workflow_overrides):
            container.mount(_OverrideCard(override, i))
        self._refresh_select()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "rbac-add-override":
            self._add_workflow_override()
        elif bid.startswith("rbac-ov-") and bid.endswith("-remove"):
            self._remove_workflow_override(bid)

    def _add_workflow_override(self) -> None:
        select = self.query_one("#rbac-workflow-select", Select)
        if select.value is Select.BLANK:
            return
        name = str(select.value)
        self._collect_all()
        defaults = self._config.rbac
        self._config.rbac.workflow_overrides.append(
            WorkflowRBACOverride(
                name=name,
                read_roles=list(defaults.default_read_roles),
                execute_roles=list(defaults.default_execute_roles),
            )
        )
        self._rebuild_overrides()

    def _remove_workflow_override(self, bid: str) -> None:
        self._collect_all()
        try:
            idx = int(bid.split("-")[2])
            if 0 <= idx < len(self._config.rbac.workflow_overrides):
                self._config.rbac.workflow_overrides.pop(idx)
        except (ValueError, IndexError):
            pass
        self._rebuild_overrides()

    def _collect_all(self) -> None:
        if self._managed_temporal_enabled():
            self._config.temporal = TemporalDeploymentConfig(
                history_replicas=self._optional_int("#temporal-history-replicas"),
                num_history_shards=self._optional_int("#temporal-history-shards"),
            )
        else:
            self._config.temporal = TemporalDeploymentConfig()
        self._config.rbac.admin_roles = _parse_roles(
            self.query_one("#rbac-admin-roles", Input).value
        )
        self._config.rbac.default_read_roles = _parse_roles(
            self.query_one("#rbac-default-read", Input).value
        )
        self._config.rbac.default_execute_roles = _parse_roles(
            self.query_one("#rbac-default-exec", Input).value
        )
        cards = self.query(_OverrideCard)
        self._config.rbac.workflow_overrides = [card.collect() for card in cards]

    def write_to_config(self, config: NVConfigManagerInstallConfig) -> None:
        self._collect_all()
        config.temporal = self._config.temporal.model_copy(deep=True)
        config.rbac = self._config.rbac.model_copy(deep=True)

    def sync_from_config(self, config: NVConfigManagerInstallConfig) -> None:
        self._config = config
        rbac = config.rbac
        try:
            self.query_one("#temporal-history-replicas", Input).value = (
                str(config.temporal.history_replicas) if config.temporal.history_replicas else ""
            )
            self.query_one("#temporal-history-shards", Input).value = (
                str(config.temporal.num_history_shards)
                if config.temporal.num_history_shards
                else ""
            )
            self._update_temporal_visibility()
            self.query_one("#rbac-admin-roles", Input).value = ", ".join(rbac.admin_roles)
            self.query_one("#rbac-default-read", Input).value = ", ".join(rbac.default_read_roles)
            self.query_one("#rbac-default-exec", Input).value = ", ".join(
                rbac.default_execute_roles
            )
        except LookupError:
            pass
        self._rebuild_overrides()

    def get_status(self, config: NVConfigManagerInstallConfig) -> str:
        if not config.rbac.admin_roles:
            return "[!]"
        if not config.rbac.default_read_roles and not config.rbac.default_execute_roles:
            return "[!]"
        return "[*]"
