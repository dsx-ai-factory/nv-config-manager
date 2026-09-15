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
"""Topology screen - pick topology YAML and simulation name."""

from __future__ import annotations

from typing import ClassVar

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Select
from textual_fspicker import FileOpen, Filters, SelectDirectory

from nv_config_manager_installer.air_sim.prebuilt_configs import (
    PREBUILT_CONFIGS,
    PrebuiltConfig,
    load_prebuilt_config,
)
from nv_config_manager_installer.air_sim.sim_config import SimConfig
from nv_config_manager_installer.tui.widgets import LabeledSwitch


class PopulationPanel(Container):
    """Replaceable provider-owned simulation population controls."""

    def __init__(self, config: SimConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config

    def write_to_config(self, config: SimConfig) -> None:
        """Write provider-owned fields into the simulation config."""

    def sync_from_config(self, config: SimConfig) -> None:
        """Refresh provider-owned controls from the simulation config."""
        self._config = config

    def get_status(self, config: SimConfig) -> str:
        """Return this panel's navigation status contribution."""
        return "[*]"


class NautobotPopulationPanel(PopulationPanel):
    """Configure the bundled Nautobot mock-topology population job."""

    def compose(self) -> ComposeResult:
        yield Label("Nautobot", classes="subsection-label")
        yield LabeledSwitch(
            "Populate bundled Nautobot with the mock topology job",
            value=self._config.run_mock_topology_job,
            id="run-mock-topology-job",
        )
        with Vertical(id="nautobot-population-fields"):
            yield Label("Mock Topology Job Path", classes="field-label")
            yield Input(
                value=self._config.mock_topology_path,
                placeholder="development/mock_topology",
                id="mock-topology-path",
            )

    def on_mount(self) -> None:
        self._toggle_fields()

    def on_labeled_switch_changed(self, event: LabeledSwitch.Changed) -> None:
        if event.labeled_switch.id == "run-mock-topology-job":
            self._toggle_fields()

    def _toggle_fields(self) -> None:
        self.query_one("#nautobot-population-fields").display = self.query_one(
            "#run-mock-topology-job", LabeledSwitch
        ).value

    def write_to_config(self, config: SimConfig) -> None:
        config.run_mock_topology_job = self.query_one("#run-mock-topology-job", LabeledSwitch).value
        config.mock_topology_path = self.query_one("#mock-topology-path", Input).value.strip()

    def sync_from_config(self, config: SimConfig) -> None:
        super().sync_from_config(config)
        self.query_one("#run-mock-topology-job", LabeledSwitch).value = config.run_mock_topology_job
        self.query_one("#mock-topology-path", Input).value = config.mock_topology_path
        self._toggle_fields()

    def get_status(self, config: SimConfig) -> str:
        if config.run_mock_topology_job and not config.mock_topology_path:
            return "[!]"
        return "[*]"


class TopologyScreen(Container):
    """Select the topology YAML file, name, and server attachment mode."""

    POPULATION_PANEL_CLASS: ClassVar[type[PopulationPanel]] = NautobotPopulationPanel

    def __init__(self, config: SimConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._syncing = False
        self._population_panel: PopulationPanel | None = None

    def create_population_panel(self) -> PopulationPanel:
        """Construct provider-owned population controls for this simulation."""
        return self.POPULATION_PANEL_CLASS(self._config, id="population-panel")

    def prebuilt_configs(self) -> tuple[PrebuiltConfig, ...]:
        """Return presets exposed by the containing AIR application."""
        app = self.app
        if hasattr(app, "prebuilt_configs"):
            return app.prebuilt_configs()
        return PREBUILT_CONFIGS

    def load_prebuilt_config(self, config_id: str) -> SimConfig:
        """Load a preset through the containing AIR application."""
        app = self.app
        if hasattr(app, "load_prebuilt_config"):
            return app.load_prebuilt_config(config_id)
        return load_prebuilt_config(config_id, type(self._config))

    def compose(self) -> ComposeResult:
        yield Label("Topology", classes="section-title")
        yield Label("─" * 40, classes="section-divider")

        yield Label("Pre-built Config", classes="field-label")
        yield Select(
            [(config.label, config.id) for config in self.prebuilt_configs()],
            prompt="Custom / manual",
            allow_blank=True,
            id="prebuilt-config",
        )
        yield Label(
            "Selecting a preset replaces the wizard fields but keeps your save file unchanged.",
            classes="field-hint",
        )

        yield Label("─" * 40, classes="section-divider")
        yield Label("Mock Topology", classes="subsection-label")
        yield LabeledSwitch(
            "Build DSX Air topology from development/mock_topology context",
            value=self._config.use_mock_context_for_fabric,
            id="generate-fabric-from-mock-context",
        )
        with Vertical(id="mock-topology-fields"):
            yield Label("Blueprint", classes="field-label")
            yield Input(
                value=self._config.mock_blueprint,
                placeholder="air_superpod",
                id="mock-blueprint",
            )
            yield Label("Deployment Name", classes="field-label")
            yield Input(
                value=self._config.deployment_name,
                placeholder="demo",
                id="deployment-name",
            )
        with Vertical(id="direct-topology-fields"):
            yield Label("DSX Air Topology YAML", classes="field-label")
            with Horizontal(classes="field-row"):
                yield Input(
                    value=self._config.topology_path,
                    placeholder="samples/custom_air_topology.yaml",
                    id="topology-path",
                )
                yield Button("Browse", id="browse-topology", variant="default")

        yield Label("─" * 40, classes="section-divider")
        self._population_panel = self.create_population_panel()
        yield self._population_panel

        yield Label("─" * 40, classes="section-divider")
        yield Label("Template Plugins", classes="subsection-label")
        yield Label(
            "Paths to template plugin directories or .tar.gz files paired with this topology",
            classes="field-hint",
        )
        yield Button("+ Add Template Plugin", id="add-template-plugin", classes="add-button")
        yield Vertical(id="template-plugin-list")

        yield Label("Simulation Name  (leave blank to auto-generate)", classes="field-label")
        yield Input(
            value=self._config.simulation_name,
            placeholder="NVCM-E2E-SUPERPOD-DEMO",
            id="sim-name",
        )

        yield Label("OOB Management Server Name", classes="field-label")
        yield Input(
            value=self._config.oob_server_name,
            id="oob-server-name",
        )

        yield Label("─" * 40, classes="section-divider")
        yield Label("Server Mode", classes="field-label")
        with RadioSet(id="server-mode"):
            yield RadioButton(
                "Use existing server (e.g. oob-mgmt-server)",
                id="mode-existing",
                value=self._config.server_mode == "use-existing",
            )
            yield RadioButton(
                "Create new server node attached to a switch",
                id="mode-create",
                value=self._config.server_mode == "create-new",
            )

        with Vertical(id="attach-fields"):
            yield Label("Switch Name", classes="field-label")
            yield Input(
                value=self._config.attach_switch,
                placeholder="leaf1-gp1-smn1-hfa01",
                id="attach-switch",
            )
            yield Label("Switch Interface", classes="field-label")
            yield Input(
                value=self._config.attach_interface,
                placeholder="swp48",
                id="attach-interface",
            )

    def on_mount(self) -> None:
        self._update_attach_fields()
        self._update_topology_fields()
        self._rebuild_template_plugins()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id == "server-mode":
            self._update_attach_fields()

    def on_labeled_switch_changed(self, event: LabeledSwitch.Changed) -> None:
        if event.labeled_switch.id == "generate-fabric-from-mock-context":
            self._update_topology_fields()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "prebuilt-config" or self._syncing:
            return
        if event.value == Select.BLANK:
            return
        config = self.load_prebuilt_config(str(event.value))
        app = self.app
        if hasattr(app, "apply_prebuilt_config"):
            app.apply_prebuilt_config(config)
            self.app.notify("Loaded pre-built config")

    def _update_attach_fields(self) -> None:
        mode = "create-new" if self.query_one("#mode-create", RadioButton).value else "use-existing"
        self.query_one("#attach-fields").display = mode == "create-new"

    def _update_topology_fields(self) -> None:
        use_mock = self.query_one("#generate-fabric-from-mock-context", LabeledSwitch).value
        self.query_one("#mock-topology-fields").display = use_mock
        self.query_one("#direct-topology-fields").display = not use_mock

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "browse-topology":
            self._pick_topology()
        elif button_id == "add-template-plugin":
            self._collect_template_plugins()
            self._pick_template_plugin()
        elif button_id.startswith("template-plugin-") and button_id.endswith("-remove"):
            self._collect_template_plugins()
            try:
                idx = int(button_id.split("-")[2])
            except (ValueError, IndexError):
                return
            if 0 <= idx < len(self._config.template_plugin_paths):
                self._config.template_plugin_paths.pop(idx)
            self._rebuild_template_plugins()

    @work
    async def _pick_topology(self) -> None:
        picked = await self.app.push_screen_wait(
            FileOpen(
                title="Select Topology YAML",
                filters=Filters(("YAML", lambda p: p.suffix in {".yaml", ".yml"})),
            )
        )
        if picked:
            self.query_one("#topology-path", Input).value = str(picked)

    def _rebuild_template_plugins(self) -> None:
        container = self.query_one("#template-plugin-list", Vertical)
        container.remove_children()
        for idx, path in enumerate(self._config.template_plugin_paths):
            row = Container(classes="template-plugin-row")
            row.compose_add_child(
                Input(
                    value=path,
                    placeholder="development/air_sim/template_plugins/my-plugin",
                    id=f"template-plugin-{idx}-path",
                )
            )
            row.compose_add_child(
                Button(
                    "Remove",
                    variant="error",
                    id=f"template-plugin-{idx}-remove",
                    classes="remove-button",
                )
            )
            container.mount(row)

    @work
    async def _pick_template_plugin(self) -> None:
        picked = await self.app.push_screen_wait(
            SelectDirectory(title="Select template plugin directory")
        )
        if picked is None:
            return
        self._collect_template_plugins()
        self._config.template_plugin_paths.append(str(picked))
        self._rebuild_template_plugins()

    def _collect_template_plugins(self) -> None:
        paths: list[str] = []
        for idx in range(len(self._config.template_plugin_paths)):
            try:
                value = self.query_one(f"#template-plugin-{idx}-path", Input).value.strip()
            except NoMatches:
                break
            if value:
                paths.append(value)
        self._config.template_plugin_paths = paths

    def write_to_config(self, config: SimConfig) -> None:
        self._collect_template_plugins()
        config.topology_path = self.query_one("#topology-path", Input).value.strip()
        config.generate_fabric_from_mock_context = self.query_one(
            "#generate-fabric-from-mock-context", LabeledSwitch
        ).value
        config.mock_blueprint = self.query_one("#mock-blueprint", Input).value.strip()
        config.deployment_name = self.query_one("#deployment-name", Input).value.strip()
        if self._population_panel is not None:
            self._population_panel.write_to_config(config)
        config.template_plugin_paths = list(self._config.template_plugin_paths)
        config.simulation_name = self.query_one("#sim-name", Input).value.strip()
        config.oob_server_name = self.query_one("#oob-server-name", Input).value.strip()
        config.server_mode = (
            "create-new" if self.query_one("#mode-create", RadioButton).value else "use-existing"
        )
        config.attach_switch = self.query_one("#attach-switch", Input).value.strip()
        config.attach_interface = self.query_one("#attach-interface", Input).value.strip()

    def sync_from_config(self, config: SimConfig) -> None:
        self._syncing = True
        try:
            self.query_one("#topology-path", Input).value = config.topology_path
            self.query_one(
                "#generate-fabric-from-mock-context", LabeledSwitch
            ).value = config.use_mock_context_for_fabric
            self.query_one("#mock-blueprint", Input).value = config.mock_blueprint
            self.query_one("#deployment-name", Input).value = config.deployment_name
            if self._population_panel is not None:
                self._population_panel.sync_from_config(config)
            self._config.template_plugin_paths = list(config.template_plugin_paths)
            self._rebuild_template_plugins()
            self.query_one("#sim-name", Input).value = config.simulation_name
            self.query_one("#oob-server-name", Input).value = config.oob_server_name
            self.query_one("#mode-existing", RadioButton).value = (
                config.server_mode == "use-existing"
            )
            self.query_one("#mode-create", RadioButton).value = config.server_mode == "create-new"
            self.query_one("#attach-switch", Input).value = config.attach_switch
            self.query_one("#attach-interface", Input).value = config.attach_interface
        finally:
            self._syncing = False
        self._update_attach_fields()
        self._update_topology_fields()

    def get_status(self, config: SimConfig) -> str:
        fabric_is_configured = (
            bool(config.mock_blueprint and config.deployment_name)
            if config.use_mock_context_for_fabric
            else bool(config.topology_path)
        )
        population_is_configured = (
            self._population_panel is None or self._population_panel.get_status(config) != "[!]"
        )
        return "[*]" if fabric_is_configured and population_is_configured else "[!]"
