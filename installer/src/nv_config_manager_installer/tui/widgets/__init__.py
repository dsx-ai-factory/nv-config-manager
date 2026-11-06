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
"""Reusable TUI widgets for the NVIDIA Config Manager installer."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Static, Switch


class LabeledSwitch(Horizontal):
    """A ``Switch`` with a text label — drop-in replacement for ``Checkbox``.

    Posts ``LabeledSwitch.Changed`` when toggled, mirroring the
    ``Checkbox.Changed`` pattern so parent handlers stay consistent.
    """

    DEFAULT_CSS = """
    LabeledSwitch {
        height: auto;
        width: auto;
        padding: 0 1 0 0;
    }
    LabeledSwitch .labeled-switch--label {
        padding: 0 1 0 0;
        content-align: center middle;
        height: 3;
        width: auto;
    }
    """

    class Changed(Message):
        """Posted when the switch value changes."""

        def __init__(self, labeled_switch: LabeledSwitch, value: bool) -> None:
            super().__init__()
            self.labeled_switch = labeled_switch
            self.value = value

    def __init__(
        self,
        label: str,
        value: bool = False,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._label_text = label
        self._initial_value = value

    def compose(self) -> ComposeResult:
        yield Static(self._label_text, classes="labeled-switch--label")
        yield Switch(value=self._initial_value, animate=False)

    def set_label(self, label: str) -> None:
        """Replace the label text."""
        self._label_text = label
        if self.is_mounted:
            self.query_one(".labeled-switch--label", Static).update(label)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        event.stop()
        self.post_message(self.Changed(self, event.value))

    @property
    def value(self) -> bool:
        return self.query_one(Switch).value

    @value.setter
    def value(self, new_value: bool) -> None:
        self.query_one(Switch).value = new_value
