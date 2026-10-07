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
"""Network secrets configuration screen -- dynamic list of secret entries."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Input, Label

from nv_config_manager_installer.schema import (
    NetworkSecretEntry,
    NVConfigManagerInstallConfig,
    PasswordSource,
    SecretsMethod,
)
from nv_config_manager_installer.template_scanner import scan_plugins

# (name, secret_key, description, required, rotation, value)
DEFAULT_ENTRIES: list[tuple[str, str, str, bool, str, str]] = [
    ("Hash Salt", "hash_salt", "Password hashing salt for user accounts", True, "r1", ""),
    ("BGP Password", "bgp_password", "BGP peering authentication", True, "r1", ""),
    (
        "Device Admin Password",
        "root_password",
        "Admin/root password for managed devices",
        True,
        "r1",
        "",
    ),
    (
        "NVIDIA Config Manager Password",
        "api_user_key",
        "NVIDIA Config Manager service account device credential",
        True,
        "r1",
        "",
    ),
    (
        "UFM API User",
        "ufm_api_user",
        "InfiniBand UFM API username",
        False,
        "",
        "admin",
    ),
    (
        "UFM API Token",
        "ufm_api_token",
        "InfiniBand UFM API token",
        False,
        "r1",
        "",
    ),
]


class _SecretCard(Vertical):
    """Editable card for a single network secret entry."""

    def __init__(self, entry: NetworkSecretEntry, index: int, **kwargs: object) -> None:
        super().__init__(**kwargs, classes="account-card")
        self._entry = entry
        self._index = index

    def compose(self) -> ComposeResult:
        e = self._entry
        idx = self._index
        prefix = f"ns-{idx}"
        tag = "required" if e.required else "optional"

        with Horizontal(classes="account-title-row"):
            yield Label(f"Secret {idx + 1} [{tag}]", classes="account-header")
            yield Button(
                "Remove", variant="error", id=f"{prefix}-remove", classes="remove-button-inline"
            )

        with Horizontal(classes="compact-field-row"):
            with Vertical(classes="compact-field"):
                yield Label("Name", classes="field-label-compact")
                yield Input(value=e.name, placeholder="e.g. BGP Password", id=f"{prefix}-name")
            with Vertical(classes="compact-field"):
                yield Label("Secret Key (INI field)", classes="field-label-compact")
                yield Input(value=e.secret_key, placeholder="e.g. bgp_password", id=f"{prefix}-key")

        if e.description:
            yield Label(f"  {e.description}", classes="field-label-compact")

        with Horizontal(classes="compact-field-row"):
            with Vertical(classes="compact-field"):
                yield Label("Rotation", classes="field-label-compact")
                yield Input(value=e.rotation, placeholder="r1", id=f"{prefix}-rotation")
            with Vertical(classes="compact-field"):
                yield Label("Value (leave empty to auto-generate)", classes="field-label-compact")
                yield Input(
                    value=e.value,
                    placeholder="auto-generate",
                    password=True,
                    id=f"{prefix}-value",
                )

    def collect(self) -> NetworkSecretEntry:
        """Read widget values into a NetworkSecretEntry."""
        prefix = f"ns-{self._index}"
        value = self.query_one(f"#{prefix}-value", Input).value
        source = (
            PasswordSource.MANUAL
            if value.strip()
            else PasswordSource.VAULT
            if self._entry.source == PasswordSource.VAULT
            else PasswordSource.GENERATE
        )
        return NetworkSecretEntry(
            name=self.query_one(f"#{prefix}-name", Input).value,
            description=self._entry.description,
            source=source,
            secret_key=self.query_one(f"#{prefix}-key", Input).value,
            rotation=self.query_one(f"#{prefix}-rotation", Input).value,
            required=self._entry.required,
            value=value,
        )


class NetworkSecretsScreen(Container):
    """Network protocol secrets -- dynamic list with add/remove."""

    def __init__(self, config: NVConfigManagerInstallConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config

    @property
    def _eso_mode(self) -> bool:
        return self._config.secrets.method == SecretsMethod.ESO

    def compose(self) -> ComposeResult:
        yield Label("Network Configuration Secrets", classes="section-title")
        yield Label("─" * 40, classes="section-divider")

        with Container(id="ns-eso-notice"):
            yield Label(
                "ESO is enabled — these values populate each site's Vault path. "
                "Vault-sourced values are left unchanged; enter initial values or leave "
                "generated values blank.",
                classes="error-text",
            )

        with Container(id="ns-content"):
            yield Label(
                "Define secret keys for network protocols and templates. "
                "Each entry becomes a field in config-secrets.ini.",
            )
            with Horizontal(classes="compact-field-row"):
                yield Button("+ Add Secret", id="add-ns", classes="add-button")
                yield Button(
                    "Scan Plugin Templates",
                    id="scan-plugins",
                    variant="primary",
                    classes="add-button",
                )
            yield Label("", id="scan-status")
            yield Vertical(id="ns-list")

    def _toggle_eso_mode(self) -> None:
        self.query_one("#ns-eso-notice").display = self._eso_mode
        self.query_one("#ns-content").display = True

    def on_mount(self) -> None:
        self._toggle_eso_mode()
        self._ensure_default_entries()
        self._rebuild_cards()

    def _ensure_default_entries(self) -> None:
        """Initialize the standard site secrets for interactive configuration."""
        if not self._config.network_secrets:
            self._config.network_secrets = [
                NetworkSecretEntry(
                    name=name,
                    description=desc,
                    source=PasswordSource.MANUAL if value else PasswordSource.GENERATE,
                    secret_key=key,
                    rotation=rotation,
                    required=req,
                    value=value,
                )
                for name, key, desc, req, rotation, value in DEFAULT_ENTRIES
            ]
            self._merge_bootstrap_keys()

    def _merge_bootstrap_keys(self) -> None:
        """Merge secret keys the DCIM provider discovers from its bootstrap data."""
        provider = getattr(self.app, "provider", None)
        hints = provider.bootstrap_secret_hints(None) if provider is not None else []
        existing_keys = {e.secret_key for e in self._config.network_secrets}
        for display_name, secret_key, rotation in hints:
            if secret_key not in existing_keys:
                self._config.network_secrets.append(
                    NetworkSecretEntry(
                        name=display_name,
                        description="Discovered from bootstrap config context",
                        secret_key=secret_key,
                        rotation=rotation,
                    )
                )
                existing_keys.add(secret_key)

    def _rebuild_cards(self) -> None:
        container = self.query_one("#ns-list", Vertical)
        container.remove_children()
        for i, entry in enumerate(self._config.network_secrets):
            container.mount(_SecretCard(entry, i))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-ns":
            self._collect_all()
            self._config.network_secrets.append(NetworkSecretEntry(name="", secret_key=""))
            self._rebuild_cards()
        elif event.button.id == "scan-plugins":
            self._run_scan()
        elif event.button.id and event.button.id.endswith("-remove"):
            self._collect_all()
            parts = event.button.id.split("-")
            try:
                idx = int(parts[1])
                if 0 <= idx < len(self._config.network_secrets):
                    self._config.network_secrets.pop(idx)
            except (ValueError, IndexError):
                pass
            self._rebuild_cards()

    def _run_scan(self) -> None:
        """Scan user-supplied template plugins and merge discovered secrets."""
        self._collect_all()
        status = self.query_one("#scan-status", Label)

        plugin_paths = [tp.path for tp in self._config.content.template_plugins]
        if not plugin_paths:
            status.update("No template plugins configured (Content tab).")
            return

        valid_paths = [p for p in plugin_paths if Path(p).is_dir()]
        if not valid_paths:
            status.update(f"No valid plugin directories found ({len(plugin_paths)} configured).")
            return

        result = scan_plugins(valid_paths)

        existing_keys = {e.secret_key for e in self._config.network_secrets}
        added = 0
        for ds in result.secrets:
            if ds.secret_key not in existing_keys:
                self._config.network_secrets.append(
                    NetworkSecretEntry(
                        name=ds.display_name,
                        secret_key=ds.secret_key,
                        rotation=ds.rotation or "r1",
                    )
                )
                existing_keys.add(ds.secret_key)
                added += 1

        self._rebuild_cards()
        parts = [f"Scanned {result.scanned_files} files"]
        if added:
            parts.append(f"{added} new secret(s) added")
        else:
            parts.append("no new secrets found")
        if result.errors:
            parts.append(f"{len(result.errors)} error(s)")
        status.update(" — ".join(parts))

    def _collect_all(self) -> None:
        cards = self.query(_SecretCard)
        self._config.network_secrets = [card.collect() for card in cards]

    def write_to_config(self, config: NVConfigManagerInstallConfig) -> None:
        self._collect_all()
        config.network_secrets = list(self._config.network_secrets)

    def sync_from_config(self, config: NVConfigManagerInstallConfig) -> None:
        self._config = config
        self._toggle_eso_mode()
        self._ensure_default_entries()
        self._rebuild_cards()

    def get_status(self, config: NVConfigManagerInstallConfig) -> str:
        if not config.network_secrets:
            return "[ ]"
        if all(
            e.secret_key and (e.source != PasswordSource.MANUAL or e.value.strip())
            for e in config.network_secrets
        ):
            return "[*]"
        return "[!]"
