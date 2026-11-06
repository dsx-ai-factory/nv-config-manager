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
"""Secrets / Vault configuration screen."""

from __future__ import annotations

import asyncio
import os

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Input, Label, RadioButton, RadioSet

from nv_config_manager_installer.k8s import K8sClient
from nv_config_manager_installer.openbao import OpenBaoClient, OpenBaoError
from nv_config_manager_installer.providers.api import DCIMInstallerProvider
from nv_config_manager_installer.providers.registry import installed_provider, provider_display_name
from nv_config_manager_installer.schema import (
    GitTokenEntry,
    K8sSecretGroup,
    NVConfigManagerInstallConfig,
    SecretsMethod,
    SiteConfig,
    VaultAuthMethod,
    VaultPathConfig,
    database_secret_keys,
)
from nv_config_manager_installer.secrets import vault_path_groups
from nv_config_manager_installer.tui.widgets import LabeledSwitch

# Ordered list of core vault path groups to display in the TUI.
# (schema_field, display_label)
_CORE_PATH_GROUPS: list[tuple[str, str]] = [
    ("dcim", "DCIM API Token"),
    ("nats", "NATS"),
    ("redis", "Redis"),
    ("postgres", "PostgreSQL"),
    ("network", "Network/Device Creds"),
    ("oidc", "OIDC / SSO"),
    ("redfish", "Redfish Credentials"),
    ("bmc", "BMC Credentials JSON"),
    ("slack", "Slack"),
    ("jira", "Jira"),
    ("cnpg_backup", "CNPG Backup S3"),
    ("ztp_s3", "ZTP S3"),
]

_LABEL_API_TOKEN = "API Token"
_VAULT_PRESENT_SENTINEL = "vault-value-present"

# Writable secret values shared by Kubernetes and Vault modes. Provider groups
# are placed by ``SecretGroupSpec.after``; the generic ``dcim``/``nats`` cards are
# shown only for providers that do not declare their own credential groups.
_CORE_K8S_GROUPS: list[tuple[str, str, bool, list[tuple[str, str]]]] = [
    (
        "dcim",
        "DCIM",
        False,
        [
            ("token", _LABEL_API_TOKEN),
        ],
    ),
    (
        "nats",
        "NATS",
        False,
        [
            ("password", "Password"),
        ],
    ),
    (
        "redis",
        "Redis",
        False,
        [
            ("password", "Password"),
        ],
    ),
    (
        "postgres",
        "PostgreSQL",
        False,
        [
            ("temporalUser", "Temporal User"),
            ("temporalPassword", "Temporal Password"),
            ("temporalVisibilityUser", "Temporal Visibility User"),
            ("temporalVisibilityPassword", "Temporal Visibility Password"),
            ("configStoreUser", "Config Store User"),
            ("configStorePassword", "Config Store Password"),
            ("dhcpUser", "DHCP User"),
            ("dhcpPassword", "DHCP Password"),
        ],
    ),
    (
        "network",
        "Network / Device Credentials",
        False,
        [
            ("user", "Username"),
            ("password", "Password"),
        ],
    ),
    (
        "slack",
        "Slack",
        True,
        [
            ("token", "Bot Token"),
        ],
    ),
    (
        "jira",
        "Jira",
        True,
        [
            ("baseUrl", "Base URL"),
            ("apiToken", _LABEL_API_TOKEN),
        ],
    ),
    (
        "cnpg_backup",
        "CNPG Backup S3",
        True,
        [
            ("accessKeyId", "Access Key ID"),
            ("accessSecretKey", "Access Secret Key"),
        ],
    ),
    (
        "ztp_s3",
        "ZTP S3",
        True,
        [
            ("endpoint", "Endpoint"),
            ("accessKeyId", "Access Key ID"),
            ("secretAccessKey", "Secret Access Key"),
        ],
    ),
]


_K8sGroup = tuple[str, str, bool, list[tuple[str, str]]]
_PathGroup = tuple[str, str, dict[str, str]]


def _database_fields(database: str) -> list[tuple[str, str]]:
    user_key, password_key = database_secret_keys(database)
    label = database.replace("_", " ").title()
    return [(user_key, f"{label} DB User"), (password_key, f"{label} DB Password")]


def _place[T](core: list[tuple[str, T]], provider: list[tuple[str, T]]) -> list[T]:
    """Interleave provider cards after the core group each names ("" = first)."""
    known = {name for name, _item in core}
    placed = [item for after, item in provider if after == ""]
    for name, item in core:
        placed.append(item)
        placed.extend(p_item for after, p_item in provider if after == name)
    placed.extend(item for after, item in provider if after and after not in known)
    return placed


def k8s_groups(provider: DCIMInstallerProvider | None) -> list[_K8sGroup]:
    """Return ``(field, label, optional, fields)`` cards for core and provider groups."""
    provider_groups = list(provider.secrets.groups) if provider is not None else []
    databases = tuple(provider.secrets.databases) if provider is not None else ()
    core: list[tuple[str, _K8sGroup]] = []
    for field_name, label, optional, fields in _CORE_K8S_GROUPS:
        if field_name in {"dcim", "nats"} and provider_groups:
            continue
        if field_name == "postgres":
            fields = [*fields, *(f for db in databases for f in _database_fields(db))]
        core.append((field_name, (field_name, label, optional, list(fields))))
    extra: list[tuple[str, _K8sGroup]] = [
        (
            spec.after if spec.k8s_after is None else spec.k8s_after,
            (spec.name, spec.k8s_label or spec.label, spec.optional, list(spec.fields)),
        )
        for spec in provider_groups
    ]
    return _place(core, extra)


def path_groups(provider: DCIMInstallerProvider | None) -> list[_PathGroup]:
    """Return ``(field, label, default_keys)`` vault path cards for core and provider groups."""
    defaults = (
        {field: keys for field, _helm_key, _suffix, keys in vault_path_groups(provider)}
        if provider is not None
        else {}
    )
    provider_groups = list(provider.secrets.groups) if provider is not None else []
    core: list[tuple[str, _PathGroup]] = [
        (field_name, (field_name, label, dict(defaults.get(field_name, {}))))
        for field_name, label in _CORE_PATH_GROUPS
        if not (field_name in {"dcim", "nats"} and provider_groups)
    ]
    extra: list[tuple[str, _PathGroup]] = [
        (spec.after, (spec.name, spec.label, dict(spec.keys))) for spec in provider_groups
    ]
    return _place(core, extra)


class _K8sSecretCard(Vertical):
    """Editable card for one kubernetes-mode secret group."""

    def __init__(
        self,
        field_name: str,
        label: str,
        optional: bool,
        keys: list[tuple[str, str]],
        group: K8sSecretGroup,
    ) -> None:
        super().__init__(classes="account-card", id=f"k8s-card-{field_name}")
        self._field_name = field_name
        self._label = label
        self._optional = optional
        self._keys = keys
        self._group = group

    def compose(self) -> ComposeResult:
        fn = self._field_name
        with Horizontal(classes="compact-field-row"):
            if self._optional:
                yield LabeledSwitch(self._label, value=self._group.enabled, id=f"k8s-enabled-{fn}")
            else:
                yield Label(self._label, classes="account-header")
        with Vertical(id=f"k8s-fields-{fn}", classes="k8s-field-group"):
            for vault_key, key_label in self._keys:
                with Horizontal(classes="compact-field-row"):
                    lbl = Label(key_label, classes="field-label-compact")
                    lbl.styles.width = "auto"
                    lbl.styles.min_width = 28
                    yield lbl
                    inp = Input(
                        value=self._group.values.get(vault_key, ""),
                        placeholder="auto-generate",
                        password=True,
                        id=f"k8s-{fn}-{vault_key}",
                    )
                    inp.styles.width = "1fr"
                    yield inp

    def on_mount(self) -> None:
        if self._optional:
            self._toggle_fields()

    def on_labeled_switch_changed(self, event: LabeledSwitch.Changed) -> None:
        if event.labeled_switch.id == f"k8s-enabled-{self._field_name}":
            self._toggle_fields()

    def _toggle_fields(self) -> None:
        try:
            enabled = self.query_one(f"#k8s-enabled-{self._field_name}", LabeledSwitch).value
            self.query_one(f"#k8s-fields-{self._field_name}").display = enabled
        except Exception:
            pass

    def collect(self) -> K8sSecretGroup:
        fn = self._field_name
        enabled = True
        if self._optional:
            try:
                enabled = self.query_one(f"#k8s-enabled-{fn}", LabeledSwitch).value
            except Exception:
                pass
        values: dict[str, str] = {}
        for vault_key, _ in self._keys:
            try:
                val = self.query_one(f"#k8s-{fn}-{vault_key}", Input).value.strip()
                if val:
                    values[vault_key] = val
            except Exception:
                pass
        return K8sSecretGroup(enabled=enabled, values=values)

    def sync_values(self, group: K8sSecretGroup) -> None:
        fn = self._field_name
        try:
            if self._optional:
                self.query_one(f"#k8s-enabled-{fn}", LabeledSwitch).value = group.enabled
            for vault_key, _ in self._keys:
                try:
                    self.query_one(f"#k8s-{fn}-{vault_key}", Input).value = group.values.get(
                        vault_key, ""
                    )
                except Exception:
                    pass
        except Exception:
            pass
        if self._optional:
            self._toggle_fields()


class _GitTokenCard(Vertical):
    """Editable card for a single git token entry."""

    def __init__(
        self,
        entry: GitTokenEntry,
        index: int,
        *,
        eso_mode: bool = False,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs, classes="account-card")
        self._entry = entry
        self._index = index
        self._eso_mode = eso_mode

    def compose(self) -> ComposeResult:
        e = self._entry
        idx = self._index
        prefix = f"gt-{idx}"

        with Horizontal(classes="account-title-row"):
            yield Label(f"Git Token {idx + 1}", classes="account-header")
            yield Button(
                "Remove", variant="error", id=f"{prefix}-remove", classes="remove-button-inline"
            )

        with Horizontal(classes="compact-field-row"):
            with Vertical(classes="compact-field"):
                yield Label("Name", classes="field-label-compact")
                yield Input(value=e.name, placeholder="e.g. prismo", id=f"{prefix}-name")
            with Vertical(classes="compact-field"):
                yield Label("Token", classes="field-label-compact")
                yield Input(
                    value=e.token,
                    placeholder="ghp_... or PAT",
                    id=f"{prefix}-token",
                    password=True,
                )

        with Horizontal(classes="compact-field-row"):
            with Vertical(classes="compact-field"):
                yield Label("Username (optional)", classes="field-label-compact")
                yield Input(value=e.username, placeholder="git username", id=f"{prefix}-username")
            if self._eso_mode:
                with Vertical(classes="compact-field"):
                    yield Label("Vault Path (ESO)", classes="field-label-compact")
                    yield Input(
                        value=e.vault_path,
                        placeholder="nv-config-manager/prod/git",
                        id=f"{prefix}-vault-path",
                    )

    def collect(self) -> GitTokenEntry:
        prefix = f"gt-{self._index}"
        vault_path = ""
        if self._eso_mode:
            try:
                vault_path = self.query_one(f"#{prefix}-vault-path", Input).value
            except Exception:
                pass
        return GitTokenEntry(
            name=self.query_one(f"#{prefix}-name", Input).value,
            token=self.query_one(f"#{prefix}-token", Input).value,
            username=self.query_one(f"#{prefix}-username", Input).value,
            vault_path=vault_path,
        )


class _SiteVaultCard(Vertical):
    """Card for per-site Vault path configuration (ESO mode only)."""

    def __init__(self, site: SiteConfig, index: int) -> None:
        super().__init__(classes="account-card")
        self._site = site
        self._index = index

    def compose(self) -> ComposeResult:
        yield Label(
            self._site.name or f"Site {self._index + 1}",
            classes="account-header",
        )
        yield Label("Vault Path Override (optional)", classes="field-label-compact")
        yield Input(
            value=self._site.vault_path,
            placeholder="e.g. nv-config-manager/site/dc01/config_secrets",
            id=f"site-vault-{self._index}",
        )

    def collect(self) -> str:
        """Return the current vault path value."""
        return self.query_one(f"#site-vault-{self._index}", Input).value


class _VaultPathCard(Vertical):
    """Vault path card with editable key mappings and initial Vault values."""

    def __init__(
        self,
        field_name: str,
        label: str,
        pc: VaultPathConfig,
        default_keys: dict[str, str],
        value_group: K8sSecretGroup | None = None,
        value_fields: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(classes="account-card", id=f"vp-card-{field_name}")
        self._field_name = field_name
        self._label = label
        self._pc = pc
        self._default_pc = VaultPathConfig(keys=default_keys)
        self._value_group = value_group
        self._value_fields = value_fields or []

    def compose(self) -> ComposeResult:
        fn = self._field_name
        pc = self._pc
        default_suffix = fn.replace("_", "-")
        placeholder = f"<env>/{default_suffix}"

        with Horizontal(classes="compact-field-row"):
            yield LabeledSwitch(self._label, value=pc.enabled, id=f"vp-enabled-{fn}")
            inp = Input(value=pc.path, placeholder=placeholder, id=f"vp-path-{fn}")
            inp.styles.width = "1fr"
            yield inp

        effective_keys = {**self._default_pc.keys, **pc.keys}
        value_labels = dict(self._value_fields)
        with Vertical(id=f"vp-keys-section-{fn}", classes="compact-field-row"):
            if self._value_fields:
                with Horizontal(classes="compact-field-row"):
                    field_header = Label("Field", classes="field-label-compact")
                    field_header.styles.width = "1fr"
                    yield field_header
                    key_header = Label("Vault Key", classes="field-label-compact")
                    key_header.styles.width = "1fr"
                    yield key_header
                    value_header = Label("Initial Value", classes="field-label-compact")
                    value_header.styles.width = "1fr"
                    yield value_header
                    status_header = Label("Vault Status", classes="field-label-compact")
                    status_header.styles.width = "1fr"
                    yield status_header
            for key_name, vault_property in effective_keys.items():
                with Horizontal(classes="compact-field-row"):
                    lbl = Label(value_labels.get(key_name, key_name), classes="field-label-compact")
                    lbl.styles.width = "1fr"
                    yield lbl
                    key_inp = Input(
                        value=vault_property,
                        placeholder=key_name,
                        id=f"vp-key-{fn}-{key_name}",
                    )
                    key_inp.styles.width = "1fr"
                    yield key_inp
                    if key_name in value_labels and self._value_group is not None:
                        value_inp = Input(
                            value=self._value_group.values.get(key_name, ""),
                            placeholder="generate if empty",
                            password=True,
                            id=f"vp-value-{fn}-{key_name}",
                        )
                        value_inp.styles.width = "1fr"
                        yield value_inp
                        status = Label(
                            "[dim]Not checked[/dim]",
                            id=f"vp-status-{fn}-{key_name}",
                        )
                        status.styles.width = "1fr"
                        yield status

    def on_mount(self) -> None: ...  # widgets are statically composed; no dynamic setup needed

    def sync_values(self, pc: VaultPathConfig, value_group: K8sSecretGroup | None) -> None:
        """Update widget values from config without rebuilding."""
        fn = self._field_name
        try:
            self.query_one(f"#vp-enabled-{fn}", LabeledSwitch).value = pc.enabled
            self.query_one(f"#vp-path-{fn}", Input).value = pc.path
        except Exception:
            pass

        effective_keys = {**self._default_pc.keys, **pc.keys}
        for key_name, vault_property in effective_keys.items():
            try:
                self.query_one(f"#vp-key-{fn}-{key_name}", Input).value = vault_property
            except Exception:
                pass
            if value_group is not None:
                try:
                    self.query_one(
                        f"#vp-value-{fn}-{key_name}", Input
                    ).value = value_group.values.get(key_name, "")
                except Exception:
                    pass

    def collect(self) -> VaultPathConfig:
        """Read current widget state into a VaultPathConfig."""
        fn = self._field_name
        try:
            enabled = self.query_one(f"#vp-enabled-{fn}", LabeledSwitch).value
            path = self.query_one(f"#vp-path-{fn}", Input).value
        except Exception:
            return self._default_pc.model_copy(deep=True)

        effective_keys = self._default_pc.keys.copy()
        for key_name in effective_keys:
            try:
                key_input = self.query_one(f"#vp-key-{fn}-{key_name}", Input)
                effective_keys[key_name] = key_input.value
            except Exception:
                pass

        return VaultPathConfig(enabled=enabled, path=path, keys=effective_keys)

    def collect_value_group(self) -> K8sSecretGroup | None:
        """Collect initial values shown alongside this path's key mappings."""
        if self._value_group is None:
            return None
        enabled = self.query_one(f"#vp-enabled-{self._field_name}", LabeledSwitch).value
        values: dict[str, str] = {}
        for key_name, _label in self._value_fields:
            try:
                value = self.query_one(
                    f"#vp-value-{self._field_name}-{key_name}", Input
                ).value.strip()
                if value and value != _VAULT_PRESENT_SENTINEL:
                    values[key_name] = value
            except Exception:
                pass
        return K8sSecretGroup(enabled=enabled, values=values)

    def set_vault_presence(self, present_keys: set[str]) -> None:
        """Show which mapped values already exist without reading them into config."""
        for key_name, _label in self._value_fields:
            value_input = self.query_one(f"#vp-value-{self._field_name}-{key_name}", Input)
            status = self.query_one(f"#vp-status-{self._field_name}-{key_name}", Label)
            if key_name in present_keys:
                if not value_input.value:
                    value_input.value = _VAULT_PRESENT_SENTINEL
                status.update("[green]Present in Vault[/green]")
            else:
                if value_input.value == _VAULT_PRESENT_SENTINEL:
                    value_input.value = ""
                status.update(
                    "[yellow]Configured locally[/yellow]"
                    if value_input.value
                    else "[dim]Missing — will generate[/dim]"
                )


class SecretsScreen(Container):
    """Secrets management: method (ESO vs kubernetes), Vault settings, git tokens."""

    PROVIDER_DEPENDENT = True

    def __init__(
        self,
        config: NVConfigManagerInstallConfig,
        provider: DCIMInstallerProvider | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._provider = provider if provider is not None else installed_provider(config)
        self._k8s_groups = k8s_groups(self._provider)
        self._path_groups = path_groups(self._provider)
        self._value_fields = {field: keys for field, _l, _o, keys in self._k8s_groups}

    @property
    def _eso_mode(self) -> bool:
        return self._config.secrets.method == SecretsMethod.ESO

    def compose(self) -> ComposeResult:
        s = self._config.secrets
        yield Label("Application Secrets", classes="section-title")
        yield Label("─" * 40, classes="section-divider")

        yield Label("Secrets Method", classes="field-label")
        with RadioSet(id="secrets-method"):
            yield RadioButton(
                "kubernetes (local K8s secrets)",
                value=s.method == SecretsMethod.KUBERNETES,
                id="method-kubernetes",
            )
            yield RadioButton(
                "eso (External Secrets Operator / Vault)",
                value=s.method == SecretsMethod.ESO,
                id="method-eso",
            )

        yield Label(
            "Enter initial secret values below. Empty required values are generated at deploy time.",
            id="secret-values-guidance",
        )

        with Container(id="k8s-secrets-section"):
            yield Label("Secret Values to Create / Populate", classes="section-title")
            yield Label("─" * 40, classes="section-divider")
            yield Label(
                "Values are written to Kubernetes Secrets or Vault based on the selected method. "
                "Enter a value to use it as the initial password or token. Leave generated "
                "credentials empty to create them at deploy time. Vault values that already "
                "exist are never overwritten. "
                "Enable optional integrations (Slack, Jira, CNPG Backup) to configure their credentials."
            )
            for field_name, label, optional, keys in self._k8s_groups:
                yield _K8sSecretCard(field_name, label, optional, keys, s.k8s.group(field_name))

        with Container(id="vault-fields"):
            yield Label("Vault Server", classes="field-label")
            yield Input(
                value=s.vault.server,
                placeholder="https://vault.example.com",
                id="vault-server",
            )

            yield Label("Vault Namespace (enterprise only)", classes="field-label")
            yield Input(value=s.vault.namespace, placeholder="admin", id="vault-namespace")

            yield Label("Secrets Engine Mount Path", classes="field-label")
            yield Input(
                value=s.vault.secrets_path, placeholder="nv-config-manager", id="vault-secrets-path"
            )

            yield Label("Config Secrets Engine Path (optional)", classes="field-label")
            yield Input(
                value=s.vault.config_secrets_path,
                placeholder="same as secrets path if empty",
                id="vault-config-secrets-path",
            )

            yield Label("Vault Auth Method", classes="field-label")
            with RadioSet(id="vault-auth-method"):
                yield RadioButton(
                    "JWT (Kubernetes auth)",
                    value=s.vault.auth.method == VaultAuthMethod.JWT,
                    id="auth-jwt",
                )
                yield RadioButton(
                    "Token (dev/testing only)",
                    value=s.vault.auth.method == VaultAuthMethod.TOKEN,
                    id="auth-token",
                )

            yield Label("JWT Auth Mount Path", classes="field-label")
            yield Input(
                value=s.vault.mount_path,
                placeholder="auth/kubernetes/prod",
                id="vault-mount-path",
            )

            yield Label("JWT Auth Role", classes="field-label")
            yield Input(
                value=s.vault.role,
                placeholder="nv-config-manager",
                id="vault-role",
            )

            yield Label("Token Secret Name (token auth only)", classes="field-label")
            yield Input(
                value=s.vault.auth.token_secret_name,
                placeholder="vault-token",
                id="vault-token-secret-name",
            )

            with Horizontal(classes="compact-field-row"):
                yield Button("Refresh Vault Values", id="refresh-vault-values")
                yield Label(
                    "[dim]Vault values not checked[/dim]",
                    id="vault-presence-status",
                )

            yield Label("")
            yield Label("Vault Secret Paths", classes="section-title")
            yield Label("─" * 40, classes="section-divider")
            yield Label(
                "Each group maps to a Vault path where ESO reads secrets. "
                "For application secrets, edit the Vault key and its initial value side by side. "
                "Empty values are generated; existing Vault keys are preserved."
            )

            paths_cfg = s.vault.paths
            for field_name, label, default_keys in self._path_groups:
                pc = paths_cfg.group(field_name)
                value_fields = self._value_fields.get(field_name)
                value_group = s.k8s.group(field_name) if value_fields else None
                yield _VaultPathCard(field_name, label, pc, default_keys, value_group, value_fields)

            yield Label("")
            yield Label("Site Vault Paths", classes="section-title")
            yield Label("─" * 40, classes="section-divider")
            yield Label(
                "Optional Vault path override for each site's config_secrets. "
                "When empty, the installer uses <environment>/site/<site>/config_secrets. "
                "Sites are defined in the Cluster section."
            )
            yield Vertical(id="site-vault-paths")

        yield Label("")
        yield Label("Git Repository Tokens", classes="section-title")
        yield Label("─" * 40, classes="section-divider")
        yield Label(
            f"Tokens for {provider_display_name(self._config)} git repository sync (e.g. Prismo). "
            "Each is written to a Kubernetes Secret or its configured Vault path.",
        )
        yield Button("+ Add Git Token", id="add-gt", classes="add-button")
        yield Vertical(id="gt-list")

    def on_mount(self) -> None:
        self._toggle_vault_fields()
        self._rebuild_site_vault_rows()
        self._rebuild_git_token_cards()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id == "secrets-method":
            self._toggle_vault_fields()

    def _toggle_vault_fields(self) -> None:
        eso_selected = self.query_one("#method-eso", RadioButton).value
        self.query_one("#vault-fields").display = eso_selected
        self.query_one("#k8s-secrets-section").display = not eso_selected
        guidance = self.query_one("#secret-values-guidance", Label)
        guidance.update(
            "Configure each Vault path below using the Key and Initial Value columns. "
            "Initial values fill missing keys only; existing values are preserved."
            if eso_selected
            else "Enter Kubernetes Secret values below. Empty required values are generated "
            "at deploy time."
        )

    # --- Vault Path Rows ---

    def _collect_vault_paths(self, *, collect_values: bool) -> None:
        """Read vault path UI state back into config."""
        paths_cfg = self._config.secrets.vault.paths
        for card in self.query(_VaultPathCard):
            setattr(paths_cfg, card._field_name, card.collect())
            if collect_values and (group := card.collect_value_group()) is not None:
                setattr(self._config.secrets.k8s, card._field_name, group)

    def _sync_vault_path_values(self) -> None:
        """Push current config values into existing vault path widgets."""
        paths_cfg = self._config.secrets.vault.paths
        for card in self.query(_VaultPathCard):
            pc = paths_cfg.group(card._field_name)
            value_group = (
                self._config.secrets.k8s.group(card._field_name)
                if card._value_group is not None
                else None
            )
            card.sync_values(pc, value_group)

    # --- K8s Secret Cards ---

    def _collect_k8s_secrets(self) -> None:
        for card in self.query(_K8sSecretCard):
            setattr(self._config.secrets.k8s, card._field_name, card.collect())

    def _sync_k8s_secret_values(self) -> None:
        for card in self.query(_K8sSecretCard):
            card.sync_values(self._config.secrets.k8s.group(card._field_name))

    # --- Site Vault Paths (ESO only) ---

    def _rebuild_site_vault_rows(self) -> None:
        """Rebuild per-site vault path cards from config.sites."""
        try:
            container = self.query_one("#site-vault-paths", Vertical)
        except Exception:
            return
        container.remove_children()
        for i, site in enumerate(self._config.sites):
            container.mount(_SiteVaultCard(site, i))

    def _collect_site_vault_paths(self) -> None:
        """Read vault path values from cards back into config.sites."""
        for card in self.query(_SiteVaultCard):
            try:
                self._config.sites[card._index].vault_path = card.collect()
            except Exception:
                pass

    # --- Git Token Cards ---

    def _rebuild_git_token_cards(self) -> None:
        container = self.query_one("#gt-list", Vertical)
        container.remove_children()
        for i, entry in enumerate(self._config.git_tokens):
            container.mount(_GitTokenCard(entry, i, eso_mode=self._eso_mode))

    def _collect_git_tokens(self) -> None:
        cards = self.query(_GitTokenCard)
        self._config.git_tokens = [card.collect() for card in cards]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""

        if bid == "refresh-vault-values":
            self._sync_vault_connection_from_widgets()
            self._load_vault_presence()
        elif bid == "add-gt":
            self._collect_git_tokens()
            self._config.git_tokens.append(GitTokenEntry(name=""))
            self._rebuild_git_token_cards()
        elif bid.startswith("gt-") and bid.endswith("-remove"):
            self._collect_git_tokens()
            parts = bid.split("-")
            try:
                idx = int(parts[1])
                if 0 <= idx < len(self._config.git_tokens):
                    self._config.git_tokens.pop(idx)
            except (ValueError, IndexError):
                pass
            self._rebuild_git_token_cards()

    def _sync_vault_connection_from_widgets(self) -> None:
        """Capture connection fields before a user-requested presence refresh."""
        vault = self._config.secrets.vault
        vault.server = self.query_one("#vault-server", Input).value.strip()
        vault.namespace = self.query_one("#vault-namespace", Input).value.strip()
        vault.secrets_path = self.query_one("#vault-secrets-path", Input).value.strip()
        vault.auth.method = (
            VaultAuthMethod.JWT
            if self.query_one("#auth-jwt", RadioButton).value
            else VaultAuthMethod.TOKEN
        )
        vault.auth.token_secret_name = self.query_one(
            "#vault-token-secret-name", Input
        ).value.strip()
        self._collect_vault_paths(collect_values=False)

    @work(group="vault-presence", exclusive=True, exit_on_error=False)
    async def _load_vault_presence(self) -> None:
        """Read Vault key presence without copying secret values into the TUI config."""
        vault = self._config.secrets.vault
        if not vault.server or not vault.secrets_path:
            self._set_vault_presence_status(
                "[yellow]Enter a Vault server and secrets mount, then refresh.[/yellow]"
            )
            return
        environment_token = (
            os.environ.get("VAULT_TOKEN", "").strip() or os.environ.get("OPENBAO_TOKEN", "").strip()
        )
        if not environment_token and vault.auth.method == VaultAuthMethod.JWT:
            self._set_vault_presence_status(
                "[yellow]Vault values not loaded: JWT presence checks require VAULT_TOKEN or "
                "OPENBAO_TOKEN.[/yellow]"
            )
            return
        if not environment_token and not vault.auth.token_secret_name:
            self._set_vault_presence_status(
                "[yellow]Vault values not loaded: set VAULT_TOKEN or configure a token Secret.[/yellow]"
            )
            return

        presence, error = await asyncio.to_thread(self._read_vault_presence)
        if error:
            self._set_vault_presence_status(error)
        elif presence is not None:
            self._apply_vault_presence(presence)

    def _read_vault_presence(self) -> tuple[dict[str, set[str]] | None, str | None]:
        """Perform blocking Vault and Kubernetes reads away from the UI thread."""
        vault = self._config.secrets.vault
        if not vault.server or not vault.secrets_path:
            return (
                None,
                "[yellow]Enter a Vault server and secrets mount, then refresh.[/yellow]",
            )

        token = (
            os.environ.get("VAULT_TOKEN", "").strip() or os.environ.get("OPENBAO_TOKEN", "").strip()
        )
        if not token and vault.auth.method == VaultAuthMethod.TOKEN:
            secret_name = vault.auth.token_secret_name
            if secret_name:
                try:
                    token = (
                        K8sClient()
                        .read_secret_data(
                            secret_name,
                            self._config.cluster.namespace,
                        )
                        .get("token", "")
                    )
                except Exception as exc:
                    return (
                        None,
                        f"[yellow]Vault values not loaded: {exc}[/yellow]",
                    )
        if not token:
            guidance = (
                "JWT presence checks require VAULT_TOKEN or OPENBAO_TOKEN."
                if vault.auth.method == VaultAuthMethod.JWT
                else "set VAULT_TOKEN or configure a token Secret."
            )
            return (
                None,
                f"[yellow]Vault values not loaded: {guidance}[/yellow]",
            )

        try:
            client = OpenBaoClient(
                vault.server,
                token,
                namespace=vault.namespace,
                timeout=10,
            )
            presence: dict[str, set[str]] = {}
            environment = self._config.cluster.environment
            for field_name, _label, default_keys in self._path_groups:
                value_fields = self._value_fields.get(field_name)
                if not value_fields:
                    continue
                path_config = vault.paths.group(field_name)
                if not path_config.enabled:
                    continue
                key_mapping = {**default_keys, **path_config.keys}
                path = path_config.path or f"{environment}/{field_name.replace('_', '-')}"
                data, _version = client.read_secret(vault.secrets_path, path)
                presence[field_name] = {
                    logical_name
                    for logical_name, _key_label in value_fields
                    if key_mapping.get(logical_name, logical_name) in data
                }
        except (OpenBaoError, OSError, ValueError) as exc:
            return (
                None,
                f"[yellow]Vault values not loaded: {exc}[/yellow]",
            )
        return presence, None

    def _set_vault_presence_status(self, message: str) -> None:
        self.query_one("#vault-presence-status", Label).update(message)

    def _apply_vault_presence(self, presence: dict[str, set[str]]) -> None:
        for card in self.query(_VaultPathCard):
            if card._value_group is not None:
                card.set_vault_presence(presence.get(card._field_name, set()))
        count = sum(len(keys) for keys in presence.values())
        self._set_vault_presence_status(f"[green]Loaded {count} existing Vault key(s)[/green]")

    def write_to_config(self, config: NVConfigManagerInstallConfig) -> None:
        """Collect widget values into the config model."""
        method_k8s = self.query_one("#method-kubernetes", RadioButton)
        config.secrets.method = SecretsMethod.KUBERNETES if method_k8s.value else SecretsMethod.ESO

        config.secrets.vault.server = self.query_one("#vault-server", Input).value
        config.secrets.vault.namespace = self.query_one("#vault-namespace", Input).value
        config.secrets.vault.secrets_path = self.query_one("#vault-secrets-path", Input).value
        config.secrets.vault.config_secrets_path = self.query_one(
            "#vault-config-secrets-path", Input
        ).value

        auth_jwt = self.query_one("#auth-jwt", RadioButton)
        config.secrets.vault.auth.method = (
            VaultAuthMethod.JWT if auth_jwt.value else VaultAuthMethod.TOKEN
        )
        config.secrets.vault.mount_path = self.query_one("#vault-mount-path", Input).value
        config.secrets.vault.role = self.query_one("#vault-role", Input).value
        config.secrets.vault.auth.token_secret_name = self.query_one(
            "#vault-token-secret-name", Input
        ).value

        self._collect_vault_paths(collect_values=not method_k8s.value)
        config.secrets.vault.paths = self._config.secrets.vault.paths

        if method_k8s.value:
            self._collect_k8s_secrets()
        config.secrets.k8s = self._config.secrets.k8s

        self._collect_site_vault_paths()
        config.sites = list(self._config.sites)

        self._collect_git_tokens()
        config.git_tokens = list(self._config.git_tokens)

    def sync_from_config(self, config: NVConfigManagerInstallConfig) -> None:
        """Refresh widget values from the config model."""
        self._config = config
        s = config.secrets
        self.query_one("#vault-server", Input).value = s.vault.server
        self.query_one("#vault-namespace", Input).value = s.vault.namespace
        self.query_one("#vault-secrets-path", Input).value = s.vault.secrets_path
        self.query_one("#vault-config-secrets-path", Input).value = s.vault.config_secrets_path
        self.query_one("#vault-mount-path", Input).value = s.vault.mount_path
        self.query_one("#vault-role", Input).value = s.vault.role
        self.query_one("#vault-token-secret-name", Input).value = s.vault.auth.token_secret_name
        self._toggle_vault_fields()
        self._sync_vault_path_values()
        self._sync_k8s_secret_values()
        self._rebuild_site_vault_rows()
        self._rebuild_git_token_cards()
        if self._eso_mode:
            self._load_vault_presence()

    def get_status(self, config: NVConfigManagerInstallConfig) -> str:
        """Return sidebar status indicator."""
        if config.secrets.method == SecretsMethod.ESO:
            if not config.secrets.vault.server:
                return "[!]"
        return "[*]"
