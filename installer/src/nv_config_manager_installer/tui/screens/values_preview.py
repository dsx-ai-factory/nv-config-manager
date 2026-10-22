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
"""Values Preview screen -- generate and inspect Helm values before deploying."""

from __future__ import annotations

from pathlib import Path

import yaml
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Button, Input, Label, Log

from nv_config_manager_installer.helm_values import build_complete_values
from nv_config_manager_installer.schema import ImageSource, NVConfigManagerInstallConfig
from nv_config_manager_installer.secrets import generate_secrets
from nv_config_manager_installer.validation import validate_config_readiness


class ValuesPreviewScreen(Container):
    """Generate Helm values, preview them, and optionally write to disk."""

    def __init__(self, config: NVConfigManagerInstallConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config

    def compose(self) -> ComposeResult:
        yield Label("Values Preview", classes="section-title")
        yield Label("─" * 40, classes="section-divider")
        yield Label(
            "Generate and preview the Helm values that would be used for deployment. "
            "Optionally write them to a file for inspection or manual use.",
        )

        with Horizontal(classes="compact-field-row"):
            yield Button("Generate", id="values-generate", variant="primary")
            yield Button("Write to File", id="values-write", variant="default")

        yield Label("Output Path", classes="field-label")
        yield Input(
            value="values-generated.yaml",
            placeholder="path/to/values.yaml",
            id="values-output-path",
        )
        yield Label("", id="values-status")
        yield Log(id="values-log", auto_scroll=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "values-generate":
            self._generate_preview()
        elif bid == "values-write":
            self._write_to_file()

    def _generate_preview(self) -> None:
        app = self.app
        if hasattr(app, "collect_config"):
            app.collect_config()

        status = self.query_one("#values-status", Label)
        log = self.query_one("#values-log", Log)
        log.clear()

        try:
            config = validate_config_readiness(self._config)
            secrets_state = generate_secrets(config)
            local_images = config.images.source == ImageSource.LOCAL
            values = build_complete_values(
                config,
                secrets_state,
                local_images=local_images,
            )
            output = yaml.dump(values, default_flow_style=False, sort_keys=False)
            log.write(output)
            self._last_output = output
            status.update("Values generated successfully.")
        except Exception as exc:
            status.update(f"Error: {exc}")
            log.write(f"Generation failed:\n{exc}\n")
            self._last_output = None

    def _write_to_file(self) -> None:
        status = self.query_one("#values-status", Label)
        if not hasattr(self, "_last_output") or not self._last_output:
            status.update("Generate values first before writing.")
            return

        output_path = self.query_one("#values-output-path", Input).value.strip()
        if not output_path:
            status.update("Specify an output path.")
            return

        try:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self._last_output)
            status.update(f"Written to {path.resolve()}")
        except Exception as exc:
            status.update(f"Write failed: {exc}")

    def write_to_config(
        self, config: NVConfigManagerInstallConfig
    ) -> None: ...  # read-only preview screen; nothing to write back

    def sync_from_config(self, config: NVConfigManagerInstallConfig) -> None:
        self._config = config

    def get_status(self, config: NVConfigManagerInstallConfig) -> str:
        return "[*]"
