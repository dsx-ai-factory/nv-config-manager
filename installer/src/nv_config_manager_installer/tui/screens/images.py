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
"""Images configuration screen — registry, tags, and per-image overrides."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Label, RadioButton, RadioSet, Select

from nv_config_manager_installer.schema import (
    ImageOverride,
    ImageSource,
    NVConfigManagerInstallConfig,
    image_keys,
    image_override_keys,
)
from nv_config_manager_installer.tui.widgets import LabeledSwitch

_W_TAG = "#img-tag"
_W_REGISTRY = "#img-registry"
_W_USERNAME = "#img-username"
_W_PASSWORD = "#img-password"


class ImagesScreen(Container):
    """Image source, registry, tag discovery, and per-image overrides."""

    PROVIDER_DEPENDENT = True

    def __init__(self, config: NVConfigManagerInstallConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._fetched_tags: list[str] = []
        self._syncing_tag = False

    def compose(self) -> ComposeResult:
        img = self._config.images
        yield Label("Images", classes="section-title")
        yield Label("─" * 40, classes="section-divider")

        yield Label("Image Source", classes="field-label")
        with RadioSet(id="img-source"):
            yield RadioButton(
                "Pull from registry",
                value=img.source == ImageSource.REGISTRY,
                id="img-src-registry",
            )
            yield RadioButton(
                "Build locally",
                value=img.source == ImageSource.LOCAL,
                id="img-src-local",
            )

        with Container(id="registry-fields"):
            yield Label("Registry", classes="section-title")
            yield Label("─" * 40, classes="section-divider")

            with Horizontal(classes="compact-field-row"):
                with Vertical(classes="compact-field"):
                    yield Label("Registry Prefix", classes="field-label-compact")
                    yield Input(
                        value=img.registry,
                        placeholder="nvcr.io/nvidian/cfa",
                        id="img-registry",
                    )
                with Vertical(classes="compact-field"):
                    yield Label("Username", classes="field-label-compact")
                    yield Input(
                        value=img.pull_secret.username,
                        placeholder="$oauthtoken",
                        id="img-username",
                    )

            with Horizontal(classes="compact-field-row"):
                with Vertical(classes="compact-field"):
                    yield Label("Registry Key (optional)", classes="field-label-compact")
                    yield Input(
                        value=img.pull_secret.password,
                        placeholder="API key or password",
                        password=True,
                        id="img-password",
                    )
                with Vertical(classes="compact-field"):
                    yield Label("Pull Secret Name", classes="field-label-compact")
                    yield Input(
                        value=img.pull_secret.name,
                        placeholder="regcred-nvcr",
                        id="img-pull-secret-name",
                    )

            yield Label("Tag", classes="section-title")
            yield Label("─" * 40, classes="section-divider")

            with Horizontal(classes="compact-field-row"):
                with Vertical(classes="compact-field"):
                    yield Label("Tag", classes="field-label-compact")
                    yield Input(
                        value=img.tag,
                        placeholder="Type a tag or filter fetched tags",
                        id="img-tag",
                    )
                with Vertical(classes="compact-field"):
                    yield Button(
                        "Fetch Available Tags",
                        id="img-fetch-tags",
                        variant="primary",
                        classes="add-button",
                    )
                    yield Label("", id="img-fetch-status")

            yield Select[str]([], id="img-tag-select", prompt="Pick from fetched tags...")

            yield Label("Pull Policy", classes="field-label")
            with RadioSet(id="img-pull-policy"):
                yield RadioButton(
                    "IfNotPresent",
                    value=img.pull_policy == "IfNotPresent",
                    id="img-pp-ifnotpresent",
                )
                yield RadioButton(
                    "Always",
                    value=img.pull_policy == "Always",
                    id="img-pp-always",
                )

            yield Label("Per-Image Overrides", classes="section-title")
            yield Label("─" * 40, classes="section-divider")
            yield Label(
                "Override repository or tag for specific images. "
                "Leave empty to use the global registry and tag.",
            )
            yield LabeledSwitch("Show per-image overrides", id="img-show-overrides")
            with Container(id="img-overrides-section"):
                yield DataTable(id="img-overrides-table")
                yield Button("+ Add Override", id="img-add-override", classes="add-button")
                yield Vertical(id="img-override-cards")

    def on_mount(self) -> None:
        self._toggle_registry_fields()
        self._toggle_overrides()
        self._rebuild_overrides_table()
        self._rebuild_override_cards()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id == "img-source":
            self._toggle_registry_fields()

    def on_labeled_switch_changed(self, event: LabeledSwitch.Changed) -> None:
        if event.labeled_switch.id == "img-show-overrides":
            self._toggle_overrides()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "img-tag" and self._fetched_tags and not self._syncing_tag:
            self._apply_tag_filter(event.value)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "img-tag-select":
            return
        val = event.value
        if val is Select.BLANK or val is None:
            return
        tag_str = str(val)
        if tag_str.startswith("Select."):
            return
        self._syncing_tag = True
        self.query_one(_W_TAG, Input).value = tag_str
        self._syncing_tag = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "img-fetch-tags":
            self._fetch_tags()
        elif bid == "img-add-override":
            self._add_override()
        elif bid.startswith("img-ovr-") and bid.endswith("-remove"):
            key = bid.removeprefix("img-ovr-").removesuffix("-remove")
            self._remove_override(key)

    def _toggle_registry_fields(self) -> None:
        is_registry = self.query_one("#img-src-registry", RadioButton).value
        self.query_one("#registry-fields").display = is_registry

    def _toggle_overrides(self) -> None:
        show = self.query_one("#img-show-overrides", LabeledSwitch).value
        self.query_one("#img-overrides-section").display = show

    @work(thread=True)
    def _fetch_tags(self) -> None:
        """Query the registry for available tags in a background thread."""
        from nv_config_manager_installer.registry_client import list_tags

        registry = self.query_one(_W_REGISTRY, Input).value.strip()
        username = self.query_one(_W_USERNAME, Input).value.strip()
        password = self.query_one(_W_PASSWORD, Input).value.strip()

        if not registry:
            self.app.call_from_thread(self._set_fetch_status, "No registry specified.")
            return

        self.app.call_from_thread(self._set_fetch_status, "Fetching tags...")

        tags, error = list_tags(registry, "nv-config-manager", username, password)

        if error:
            self.app.call_from_thread(self._set_fetch_status, error)
            return

        if not tags:
            self.app.call_from_thread(self._set_fetch_status, "No tags found.")
            return

        self._fetched_tags = tags
        self.app.call_from_thread(self._populate_tag_select, tags)
        self.app.call_from_thread(self._set_fetch_status, f"Found {len(tags)} tags.")

    def _set_fetch_status(self, text: str) -> None:
        self.query_one("#img-fetch-status", Label).update(text)

    def _populate_tag_select(self, tags: list[str]) -> None:
        self._fetched_tags = tags
        current = self.query_one(_W_TAG, Input).value.strip()
        self._apply_tag_filter(current)

    def _apply_tag_filter(self, filter_text: str) -> None:
        """Filter the tag dropdown to tags matching the filter string."""
        needle = filter_text.strip().lower()
        filtered = (
            [t for t in self._fetched_tags if needle in t.lower()] if needle else self._fetched_tags
        )
        select = self.query_one("#img-tag-select", Select)
        options = [(t, t) for t in filtered[:200]]
        select.set_options(options)

    def _rebuild_overrides_table(self) -> None:
        """Show the effective image for each key based on global + overrides."""
        table = self.query_one("#img-overrides-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Image", "Repository", "Tag")

        registry = self._config.images.registry
        global_tag = self._config.images.tag or "(chart default)"

        provider = getattr(self.app, "provider", None)
        first_party = dict(image_keys(provider))
        for key, source_repo in image_override_keys(provider):
            ovr = self._config.images.overrides.get(key)
            if ovr and ovr.repository:
                repo = ovr.repository
            elif registry and key in first_party:
                repo = f"{registry}/{first_party[key]}"
            else:
                repo = source_repo

            tag = ovr.tag if ovr and ovr.tag else global_tag
            table.add_row(key, repo, tag)

    def _rebuild_override_cards(self) -> None:
        container = self.query_one("#img-override-cards", Vertical)
        container.remove_children()
        for key, ovr in self._config.images.overrides.items():
            card = Container(classes="account-card")
            card.compose_add_child(Label(f"Override: {key}", classes="field-label"))
            card.compose_add_child(
                Input(
                    value=ovr.repository,
                    placeholder=f"Custom repository for {key}",
                    id=f"img-ovr-{key}-repo",
                )
            )
            card.compose_add_child(
                Input(
                    value=ovr.tag,
                    placeholder="Custom tag (empty = use global)",
                    id=f"img-ovr-{key}-tag",
                )
            )
            card.compose_add_child(
                Button(
                    "Remove",
                    variant="error",
                    id=f"img-ovr-{key}-remove",
                    classes="remove-button",
                )
            )
            container.mount(card)

    def _add_override(self) -> None:
        self._collect_overrides()
        existing_keys = set(self._config.images.overrides.keys())
        provider = getattr(self.app, "provider", None)
        available = [k for k, _ in image_override_keys(provider) if k not in existing_keys]
        if not available:
            self.app.notify("All images already have overrides.", severity="warning")
            return
        key = available[0]
        self._config.images.overrides[key] = ImageOverride()
        self._rebuild_override_cards()
        self._rebuild_overrides_table()

    def _remove_override(self, key: str) -> None:
        self._collect_overrides()
        self._config.images.overrides.pop(key, None)
        self._rebuild_override_cards()
        self._rebuild_overrides_table()

    def _collect_overrides(self) -> None:
        new_overrides: dict[str, ImageOverride] = {}
        for key in self._config.images.overrides:
            try:
                repo = self.query_one(f"#img-ovr-{key}-repo", Input).value
                tag = self.query_one(f"#img-ovr-{key}-tag", Input).value
                new_overrides[key] = ImageOverride(repository=repo, tag=tag)
            except Exception:
                pass
        self._config.images.overrides = new_overrides

    def write_to_config(self, config: NVConfigManagerInstallConfig) -> None:
        if self.query_one("#img-src-local", RadioButton).value:
            config.images.source = ImageSource.LOCAL
        else:
            config.images.source = ImageSource.REGISTRY

        config.images.registry = self.query_one(_W_REGISTRY, Input).value
        config.images.tag = self.query_one(_W_TAG, Input).value
        config.images.pull_secret.username = self.query_one(_W_USERNAME, Input).value
        config.images.pull_secret.password = self.query_one(_W_PASSWORD, Input).value
        config.images.pull_secret.name = self.query_one("#img-pull-secret-name", Input).value

        if self.query_one("#img-pp-always", RadioButton).value:
            config.images.pull_policy = "Always"
        else:
            config.images.pull_policy = "IfNotPresent"

        self._collect_overrides()
        config.images.overrides = dict(self._config.images.overrides)

    def sync_from_config(self, config: NVConfigManagerInstallConfig) -> None:
        self._config = config
        img = config.images
        self.query_one(_W_REGISTRY, Input).value = img.registry
        self.query_one(_W_TAG, Input).value = img.tag
        self.query_one(_W_USERNAME, Input).value = img.pull_secret.username
        self.query_one(_W_PASSWORD, Input).value = img.pull_secret.password
        self.query_one("#img-pull-secret-name", Input).value = img.pull_secret.name
        self._toggle_registry_fields()
        self._rebuild_overrides_table()
        self._rebuild_override_cards()

    def get_status(self, config: NVConfigManagerInstallConfig) -> str:
        if config.images.source == ImageSource.LOCAL:
            return "[*]"
        if config.images.registry:
            return "[*]"
        return "[!]"
