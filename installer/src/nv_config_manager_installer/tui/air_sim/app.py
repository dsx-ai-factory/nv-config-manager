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
"""NVCM DSX Air Simulation TUI - wizard for bringing up DSX Air simulations."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Label, Static

from nv_config_manager_installer.air_sim.constants import DEFAULT_AIR_SIM_CONFIG_PATH
from nv_config_manager_installer.air_sim.prebuilt_configs import (
    PREBUILT_CONFIGS,
    PrebuiltConfig,
    load_prebuilt_config,
)
from nv_config_manager_installer.air_sim.sim_config import SimConfig
from nv_config_manager_installer.tui.air_sim.screens.launch import LaunchScreen
from nv_config_manager_installer.tui.air_sim.screens.options import OptionsScreen
from nv_config_manager_installer.tui.air_sim.screens.topology import TopologyScreen

SECTION_LABELS: tuple[tuple[str, str], ...] = (
    ("topology", "Topology"),
    ("options", "Options"),
    ("launch", "Launch"),
)

CSS_PATH = Path(__file__).parent / "app.tcss"

_STATUS_CLASS_MAP = {
    "[*]": "--complete",
    "[!]": "--incomplete",
    "[>]": "--running",
    "[ ]": "--empty",
}

_STATUS_TOOLTIP = {
    "[*]": "Ready",
    "[!]": "Needs attention — check required fields",
    "[ ]": "Not configured",
    "[>]": "In progress",
}


class NavItem(Static):
    """Clickable sidebar navigation item."""

    def __init__(self, section_id: str, label: str) -> None:
        super().__init__()
        self.section_id = section_id
        self.label_text = label
        self.status = " "

    def render(self) -> str:  # type: ignore[override]
        prefix = "* " if self.status == "[!]" else "  "
        return f"{prefix}{self.label_text}"

    def on_click(self) -> None:
        app = self.app
        if isinstance(app, NVCMAirSimApp):
            app.switch_section(self.section_id)

    def set_status(self, status: str) -> None:
        self.status = status
        for cls in _STATUS_CLASS_MAP.values():
            self.remove_class(cls)
        self.add_class(_STATUS_CLASS_MAP.get(status, "--empty"))
        self.tooltip = _STATUS_TOOLTIP.get(status, "")
        self.refresh()


class QuitConfirmScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    QuitConfirmScreen { align: center middle; }
    #quit-dialog {
        width: 44; height: auto; padding: 1 2;
        border: thick $accent; background: $surface;
    }
    #quit-dialog Label { width: 100%; content-align: center middle; }
    #quit-buttons { height: auto; width: 100%; align: center middle; margin-top: 1; }
    #quit-buttons Button { min-width: 12; margin: 0 2; }
    """

    def compose(self) -> ComposeResult:
        with Container(id="quit-dialog"):
            yield Label("Quit NVCM DSX Air Sim Wizard?")
            with Horizontal(id="quit-buttons"):
                yield Button("Quit", variant="error", id="quit-yes")
                yield Button("Cancel", variant="primary", id="quit-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "quit-yes")


class NVCMAirSimApp(App[None]):
    """NVCM DSX Air Simulation Wizard TUI."""

    TITLE = "NVCM DSX Air Sim Wizard"
    CSS_PATH = CSS_PATH
    ENABLE_COMMAND_PALETTE = False
    CONFIG_MODEL: ClassVar[type[SimConfig]] = SimConfig
    SECTION_LABELS: ClassVar[tuple[tuple[str, str], ...]] = SECTION_LABELS
    SCREEN_CLASSES: ClassVar[dict[str, type[Container]]] = {
        "topology": TopologyScreen,
        "options": OptionsScreen,
        "launch": LaunchScreen,
    }

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f2", "save", "Save", key_display="F2"),
        Binding("f9", "launch", "Launch", key_display="F9"),
        Binding("f10", "save_and_exit", "Save & Exit", key_display="F10"),
        Binding("ctrl+c", "request_quit", "Quit"),
        Binding("ctrl+n", "next_section", "Next Section", key_display="^N"),
        Binding("ctrl+p", "prev_section", "Prev Section", key_display="^P"),
    ]

    def __init__(
        self,
        config: SimConfig | None = None,
        config_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.config = config if config is not None else self.CONFIG_MODEL()
        self.config_path = config_path or DEFAULT_AIR_SIM_CONFIG_PATH
        self.active_section = "topology"
        self._nav_items: dict[str, NavItem] = {}
        self._screens: dict[str, Container] = {}

    def compose(self) -> ComposeResult:
        with Horizontal():
            with VerticalScroll(id="sidebar"):
                yield Label("NVCM DSX Air Sim Wizard", id="sidebar-title")
                for section_id, label in self.section_labels():
                    item = NavItem(section_id, label)
                    item.add_class("nav-item")
                    self._nav_items[section_id] = item
                    yield item
            with VerticalScroll(id="content-area"):
                yield from self._build_screens()
        yield Footer()

    def _build_screens(self) -> list[Container]:
        screens = []
        for section_id, cls in self.screen_classes().items():
            screen = self.create_screen(section_id, cls)
            screen.display = section_id == self.active_section
            self._screens[section_id] = screen
            screens.append(screen)
        return screens

    def section_labels(self) -> tuple[tuple[str, str], ...]:
        """Return ordered navigation entries for this simulation installer."""
        return self.SECTION_LABELS

    def screen_classes(self) -> dict[str, type[Container]]:
        """Return screen implementations keyed by section identifier."""
        return dict(self.SCREEN_CLASSES)

    def create_screen(self, section_id: str, screen_class: type[Container]) -> Container:
        """Construct a section screen; derived installers may inject dependencies here."""
        return screen_class(self.config, id=f"screen-{section_id}")

    def prebuilt_configs(self) -> tuple[PrebuiltConfig, ...]:
        """Return presets offered by the topology screen."""
        return PREBUILT_CONFIGS

    def load_prebuilt_config(self, config_id: str) -> SimConfig:
        """Load a preset using this application's configuration model."""
        return load_prebuilt_config(config_id, self.CONFIG_MODEL)

    def on_mount(self) -> None:
        self._highlight_nav(self.active_section)
        self._update_all_statuses()

    def apply_prebuilt_config(self, config: SimConfig) -> None:
        """Replace current wizard values from a pre-built config without changing save path."""
        self.config = config
        for screen in self._screens.values():
            if hasattr(screen, "_config"):
                screen._config = config
            if hasattr(screen, "sync_from_config"):
                screen.sync_from_config(config)
        self._update_all_statuses()

    def switch_section(self, section_id: str) -> None:
        if section_id == self.active_section:
            return
        outgoing = self._screens.get(self.active_section)
        if outgoing and self.active_section != "launch" and hasattr(outgoing, "write_to_config"):
            outgoing.write_to_config(self.config)
        if outgoing:
            outgoing.display = False
        self.active_section = section_id
        incoming = self._screens.get(section_id)
        if incoming:
            incoming.display = True
            if hasattr(incoming, "sync_from_config"):
                incoming.sync_from_config(self.config)
        self._highlight_nav(section_id)
        self._update_all_statuses()

    def _highlight_nav(self, section_id: str) -> None:
        for sid, item in self._nav_items.items():
            if sid == section_id:
                item.add_class("--highlight")
            else:
                item.remove_class("--highlight")

    def _update_all_statuses(self) -> None:
        for section_id, item in self._nav_items.items():
            screen = self._screens.get(section_id)
            if screen and hasattr(screen, "get_status"):
                item.set_status(screen.get_status(self.config))
            else:
                item.set_status("[ ]")

    def collect_config(self) -> None:
        for section_id, screen in self._screens.items():
            if section_id != "launch" and hasattr(screen, "write_to_config"):
                screen.write_to_config(self.config)

    def action_next_section(self) -> None:
        sections = [section_id for section_id, _ in self.section_labels()]
        idx = sections.index(self.active_section) if self.active_section in sections else -1
        if idx < len(sections) - 1:
            self.switch_section(sections[idx + 1])

    def action_prev_section(self) -> None:
        sections = [section_id for section_id, _ in self.section_labels()]
        idx = sections.index(self.active_section) if self.active_section in sections else 0
        if idx > 0:
            self.switch_section(sections[idx - 1])

    def action_save(self) -> None:
        self.collect_config()
        self.config.to_yaml(self.config_path)
        self._update_all_statuses()
        self.notify(f"Saved to {self.config_path}")

    def action_launch(self) -> None:
        self.collect_config()
        self.switch_section("launch")

    def action_save_and_exit(self) -> None:
        self.collect_config()
        self.config.to_yaml(self.config_path)
        self.exit(message=f"Config saved to {self.config_path}")

    def action_request_quit(self) -> None:
        def _on_dismiss(result: bool) -> None:
            if result:
                self.exit()

        self.push_screen(QuitConfirmScreen(), callback=_on_dismiss)


def _resolve_config_path(argv: list[str] | None = None) -> Path:
    """Resolve the TUI config path from CLI args, env, or the default path."""
    parser = argparse.ArgumentParser(description="Launch the NVCM DSX Air simulation TUI")
    parser.add_argument(
        "config_path",
        nargs="?",
        help="Optional YAML config path to load and save",
    )
    parser.add_argument(
        "--config",
        dest="config_path_flag",
        help="YAML config path to load and save",
    )
    args = parser.parse_args(argv)
    path = args.config_path_flag or args.config_path or os.environ.get("NVCM_AIR_CONFIG")
    return Path(path).expanduser() if path else DEFAULT_AIR_SIM_CONFIG_PATH


def run(argv: list[str] | None = None) -> None:
    """Entry point for nvcm-air-tui command."""
    config_path = _resolve_config_path(argv)
    config = SimConfig.load_or_default(config_path)
    app = NVCMAirSimApp(config=config, config_path=config_path)
    app.run()
