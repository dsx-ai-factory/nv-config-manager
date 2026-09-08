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
"""NVIDIA Config Manager Install Wizard - Textual TUI application.

Full-screen app with sidebar navigation and section-specific content panels.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Label, Static

from nv_config_manager_installer.schema import NVConfigManagerInstallConfig
from nv_config_manager_installer.tui.screens.cluster import ClusterScreen
from nv_config_manager_installer.tui.screens.content import IngestDataScreen
from nv_config_manager_installer.tui.screens.deploy import DeployScreen
from nv_config_manager_installer.tui.screens.external_services import ExternalServicesScreen
from nv_config_manager_installer.tui.screens.images import ImagesScreen
from nv_config_manager_installer.tui.screens.infrastructure import InfraScreen
from nv_config_manager_installer.tui.screens.network_secrets import NetworkSecretsScreen
from nv_config_manager_installer.tui.screens.render import RenderScreen
from nv_config_manager_installer.tui.screens.services import ServicesScreen
from nv_config_manager_installer.tui.screens.spiffe import SPIFFEScreen
from nv_config_manager_installer.tui.screens.sso import SSOScreen
from nv_config_manager_installer.tui.screens.values_preview import ValuesPreviewScreen
from nv_config_manager_installer.tui.screens.vault import SecretsScreen
from nv_config_manager_installer.tui.screens.workflow_rbac import WorkflowsScreen
from nv_config_manager_installer.tui.screens.ztp import ZTPScreen

SECTION_LABELS: tuple[tuple[str, str], ...] = (
    ("cluster", "Cluster"),
    ("services", "Services"),
    ("external_services", "External Services"),
    ("secrets", "App Secrets"),
    ("network_secrets", "Network Secrets"),
    ("ingest_data", "Ingest Data"),
    ("render", "Template Plugins"),
    ("ztp", "OS Images"),
    ("workflows", "Workflows"),
    ("images", "Container Images"),
    ("sso", "SSO"),
    ("spiffe", "SPIFFE"),
    ("infrastructure", "Infrastructure"),
    ("values_preview", "Values Preview"),
    ("deploy", "Deploy"),
)

CSS_PATH = Path(__file__).parent / "app.tcss"


_STATUS_CLASS_MAP: dict[str, str] = {
    "[*]": "--complete",
    "[!]": "--incomplete",
    "[>]": "--running",
    "[ ]": "--empty",
    "[~]": "--skipped",
}

_STATUS_TOOLTIP: dict[str, str] = {
    "[*]": "Complete",
    "[!]": "Needs attention \u2014 check required fields",
    "[ ]": "Not yet configured",
    "[>]": "Deployment in progress",
    "[~]": "Not applicable \u2014 managed externally (ESO)",
}


class NavItem(Static):
    """A clickable sidebar navigation item rendered as a compact panel."""

    def __init__(self, section_id: str, label: str) -> None:
        super().__init__()
        self.section_id = section_id
        self.label_text = label
        self.status = " "

    def render(self) -> str:  # type: ignore[override]
        prefix = "* " if self.status == "[!]" else "  "
        return f"{prefix}{self.label_text}"

    def on_click(self) -> None:
        """Handle click to switch sections."""
        app = self.app
        if isinstance(app, NVConfigManagerInstallerApp):
            app.switch_section(self.section_id)

    def set_status(self, status: str) -> None:
        """Update the status indicator, CSS class, and tooltip."""
        self.status = status
        for cls in _STATUS_CLASS_MAP.values():
            self.remove_class(cls)
        new_cls = _STATUS_CLASS_MAP.get(status, "--empty")
        self.add_class(new_cls)
        self.tooltip = _STATUS_TOOLTIP.get(status, "")
        self.refresh()


class QuitConfirmScreen(ModalScreen[bool]):
    """Modal dialog asking whether the user really wants to quit."""

    DEFAULT_CSS = """
    QuitConfirmScreen {
        align: center middle;
    }
    #quit-dialog {
        width: 46;
        height: auto;
        max-height: 12;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #quit-dialog Label {
        width: 100%;
        content-align: center middle;
    }
    #quit-buttons {
        height: auto;
        width: 100%;
        align: center middle;
        margin-top: 1;
    }
    #quit-buttons Button {
        min-width: 12;
        max-width: 14;
        margin: 0 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="quit-dialog"):
            yield Label("Quit NVIDIA Config Manager Install Wizard?")
            yield Label("Unsaved changes will be lost.")
            with Horizontal(id="quit-buttons"):
                yield Button("Quit", variant="error", id="quit-yes")
                yield Button("Cancel", variant="primary", id="quit-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "quit-yes")


class NVConfigManagerInstallerApp(App[None]):
    """NVIDIA Config Manager Install Wizard TUI."""

    TITLE = "NVCM Install Wizard"
    CSS_PATH = CSS_PATH
    ENABLE_COMMAND_PALETTE = False
    CONFIG_MODEL: ClassVar[type[NVConfigManagerInstallConfig]] = NVConfigManagerInstallConfig
    SECTION_LABELS: ClassVar[tuple[tuple[str, str], ...]] = SECTION_LABELS
    SCREEN_CLASSES: ClassVar[dict[str, type[Container]]] = {
        "cluster": ClusterScreen,
        "services": ServicesScreen,
        "external_services": ExternalServicesScreen,
        "secrets": SecretsScreen,
        "network_secrets": NetworkSecretsScreen,
        "ingest_data": IngestDataScreen,
        "render": RenderScreen,
        "ztp": ZTPScreen,
        "workflows": WorkflowsScreen,
        "images": ImagesScreen,
        "sso": SSOScreen,
        "spiffe": SPIFFEScreen,
        "infrastructure": InfraScreen,
        "values_preview": ValuesPreviewScreen,
        "deploy": DeployScreen,
    }

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f2", "save", "Save", key_display="F2"),
        Binding("f5", "generate", "Generate Values", key_display="F5"),
        Binding("f9", "deploy", "Deploy", key_display="F9"),
        Binding("f10", "save_and_exit", "Save & Exit", key_display="F10"),
        Binding("ctrl+c", "request_quit", "Quit"),
        Binding("ctrl+n", "next_section", "Next Section", key_display="^N"),
        Binding("ctrl+p", "prev_section", "Prev Section", key_display="^P"),
    ]

    def __init__(
        self,
        config: NVConfigManagerInstallConfig | None = None,
        config_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.config = config or self.create_default_config()
        self.config_path = config_path or Path("nv-config-manager-install.yaml")
        self.active_section = "cluster"
        self._nav_items: dict[str, NavItem] = {}
        self._screens: dict[str, Container] = {}
        self._deploy_running = False

    def compose(self) -> ComposeResult:
        """Build the two-pane layout with sidebar and content area."""
        with Horizontal():
            with VerticalScroll(id="sidebar"):
                yield Label("NVCM Install Wizard", id="sidebar-title")
                for section_id, label in self.section_labels():
                    item = NavItem(section_id, label)
                    item.add_class("nav-item")
                    self._nav_items[section_id] = item
                    yield item
            with VerticalScroll(id="content-area"):
                yield from self._build_section_screens()
        yield Footer()

    def _build_section_screens(self) -> list[Container]:
        """Create all section screens, only the active one visible."""
        screens = []
        for section_id, cls in self.screen_classes().items():
            screen = self.create_screen(section_id, cls)
            screen.display = section_id == self.active_section
            self._screens[section_id] = screen
            screens.append(screen)
        return screens

    def section_labels(self) -> tuple[tuple[str, str], ...]:
        """Return ordered navigation entries for this installer."""
        return self.SECTION_LABELS

    def create_default_config(self) -> NVConfigManagerInstallConfig:
        """Create the configuration model edited by this installer."""
        return self.CONFIG_MODEL()

    def validate_config(self) -> NVConfigManagerInstallConfig:
        """Validate and preserve the configuration model selected by a derived app."""
        return self.CONFIG_MODEL.model_validate(self.config.model_dump())

    def screen_classes(self) -> dict[str, type[Container]]:
        """Return screen implementations keyed by section identifier."""
        return dict(self.SCREEN_CLASSES)

    def create_screen(self, section_id: str, screen_class: type[Container]) -> Container:
        """Construct a section screen; derived installers may inject dependencies here."""
        return screen_class(self.config, id=f"screen-{section_id}")

    def on_mount(self) -> None:
        """Highlight the initial section after mounting."""
        self._highlight_nav(self.active_section)
        self._update_all_statuses()

    def switch_section(self, section_id: str) -> None:
        """Switch the visible content area to the given section."""
        if section_id == self.active_section:
            return
        if self._deploy_running and self.active_section == "deploy":
            self.notify("Deployment is running. Please wait.", severity="warning")
            return
        outgoing = self._screens.get(self.active_section)
        if outgoing and self.active_section != "deploy" and hasattr(outgoing, "write_to_config"):
            outgoing.write_to_config(self.config)
        if outgoing:
            outgoing.display = False
        self.active_section = section_id
        if section_id in self._screens:
            screen = self._screens[section_id]
            screen.display = True
            if hasattr(screen, "sync_from_config"):
                screen.sync_from_config(self.config)
        self._highlight_nav(section_id)
        self._update_all_statuses()

    def _highlight_nav(self, section_id: str) -> None:
        """Update sidebar highlighting."""
        for sid, item in self._nav_items.items():
            if sid == section_id:
                item.add_class("--highlight")
            else:
                item.remove_class("--highlight")

    def _update_all_statuses(self) -> None:
        """Refresh sidebar status indicators for all sections."""
        for section_id, item in self._nav_items.items():
            screen = self._screens.get(section_id)
            if screen and hasattr(screen, "get_status"):
                item.set_status(screen.get_status(self.config))
            else:
                item.set_status("[ ]")

    def collect_config(self) -> None:
        """Collect current values from all screens into self.config."""
        for section_id, screen in self._screens.items():
            if section_id != "deploy" and hasattr(screen, "write_to_config"):
                screen.write_to_config(self.config)

    def action_next_section(self) -> None:
        """Advance to the next sidebar section (Ctrl+N)."""
        sections = [s for s, _ in self.section_labels()]
        idx = sections.index(self.active_section) if self.active_section in sections else -1
        if idx < len(sections) - 1:
            self.switch_section(sections[idx + 1])

    def action_prev_section(self) -> None:
        """Go back to the previous sidebar section (Ctrl+P)."""
        sections = [s for s, _ in self.section_labels()]
        idx = sections.index(self.active_section) if self.active_section in sections else 0
        if idx > 0:
            self.switch_section(sections[idx - 1])

    def action_save(self) -> None:
        """Save current config to YAML (F2)."""
        self.collect_config()
        self.config.to_yaml(self.config_path)
        self._update_all_statuses()
        self.notify(f"Saved to {self.config_path}")

    def action_generate(self) -> None:
        """Switch to values preview screen (F5)."""
        self.switch_section("values_preview")

    def action_deploy(self) -> None:
        """Switch to deploy screen (F9)."""
        self.switch_section("deploy")

    def action_save_and_exit(self) -> None:
        """Validate, save, and exit (F10)."""
        self.collect_config()
        try:
            self.validate_config()
        except Exception as exc:
            self.notify(f"Validation error: {exc}", severity="error")
            return
        self.config.to_yaml(self.config_path)
        self._update_all_statuses()
        self.notify(f"Saved to {self.config_path}")
        self.exit()

    def action_request_quit(self) -> None:
        """Show a confirmation dialog before quitting."""

        def _on_dismiss(result: bool) -> None:
            if result:
                self.exit()

        self.push_screen(QuitConfirmScreen(), callback=_on_dismiss)
