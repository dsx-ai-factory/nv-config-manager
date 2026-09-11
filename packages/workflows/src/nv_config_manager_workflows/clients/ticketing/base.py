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
"""Configuration-independent ticketing provider contract."""

from __future__ import annotations

import types
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, Self

type TicketingSettings = Mapping[str, object]


class TicketingProvider(ABC):
    """Common interface implemented by ticketing backends."""

    max_attachment_size: int | None = None # bytes; None = no size limit enforced

    @classmethod
    @abstractmethod
    def from_settings(cls, settings: TicketingSettings) -> Self:
        """Construct a provider from explicit, provider-specific settings."""
        raise NotImplementedError()

    @abstractmethod
    async def __aenter__(self) -> Self:
        raise NotImplementedError()

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def validate_issue(self, issue_key: str) -> dict[str, Any]:
        """Confirm that an issue exists and return its provider metadata."""
        raise NotImplementedError()

    @abstractmethod
    async def upload_attachment(
        self,
        issue_key: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> str:
        """Upload an attachment and return its provider URL or identifier."""
        raise NotImplementedError()

    @abstractmethod
    async def add_comment(self, issue_key: str, body: str) -> str:
        """Add a plain-text comment and return its provider identifier."""
        raise NotImplementedError()
