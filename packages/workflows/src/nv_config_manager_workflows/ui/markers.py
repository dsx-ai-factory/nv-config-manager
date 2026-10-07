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
"""Field markers that shape a workflow's form without changing its input model.

Each marker is a plain frozen dataclass placed in a top-level input field's
``Annotated`` metadata. Pydantic ignores it, so the model's JSON Schema, its
validation, the API request body, and the MCP tool schema stay unchanged; only
the ``/form`` projection reads it.
"""

from dataclasses import dataclass
from typing import Any

UNSET: Any = object()
"""Sentinel for a :class:`FormSchema` keyword that is not set."""


@dataclass(frozen=True)
class ServerOwned:
    """The server fills this field (for example ``user``); the form never shows or sends it.

    The field must have a Pydantic default because the request body is validated
    before the server fills it.
    """


@dataclass(frozen=True)
class FormExcluded:
    """The form never shows or sends this field; the model's default applies.

    Use it for values other callers (schedulers, parent workflows) send and for
    enriched fields resolved server-side. The field must have a Pydantic default.
    """


@dataclass(frozen=True, kw_only=True)
class FormSchema:
    """Form-only JSON Schema keywords merged into this field's ``/form`` property.

    They tighten or seed the form without changing what the API accepts:
    ``default`` becomes the projected ``default`` (JSON-encoded through the
    field's type), and the remaining keywords become ``minItems``, ``maxItems``,
    ``minLength``, ``maxLength``, ``minimum``, ``maximum``, and ``pattern``.
    """

    default: Any = UNSET
    min_items: int | None = None
    max_items: int | None = None
    min_length: int | None = None
    max_length: int | None = None
    minimum: float | None = None
    maximum: float | None = None
    pattern: str | None = None

    def keywords(self) -> dict[str, Any]:
        """Return the set JSON Schema keywords other than ``default``, keyed by wire name."""
        values = {
            "minItems": self.min_items,
            "maxItems": self.max_items,
            "minLength": self.min_length,
            "maxLength": self.max_length,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "pattern": self.pattern,
        }
        return {name: value for name, value in values.items() if value is not None}


FORM_MARKERS = (ServerOwned, FormExcluded, FormSchema)
"""Every marker type; registration rejects one anywhere but a top-level field."""

__all__ = ["FORM_MARKERS", "UNSET", "FormExcluded", "FormSchema", "ServerOwned"]
