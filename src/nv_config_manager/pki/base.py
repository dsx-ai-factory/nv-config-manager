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
"""Provider-neutral PKI client contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Self


class PKIError(Exception):
    """Base exception for PKI operations."""


class PKIConfigurationError(PKIError):
    """The selected PKI provider is not configured correctly."""


class PKIAuthenticationError(PKIError):
    """The PKI provider rejected workload authentication."""


class PKIAuthorizationError(PKIError):
    """The authenticated workload cannot perform the requested operation."""


class PKISourceNotFoundError(PKIError):
    """A logical certificate source is not configured for the operation."""


class PKIProviderError(PKIError):
    """The PKI provider failed or returned an invalid response."""


@dataclass(frozen=True, slots=True)
class CertificateIssueRequest:
    """Provider-neutral identity used to issue one device certificate."""

    source: str
    device_id: str
    device_name: str


@dataclass(frozen=True, slots=True)
class IssuedCertificate:
    """A newly issued identity and its trust chain."""

    certificate_pem: str
    private_key_pem: str
    ca_chain_pem: tuple[str, ...]
    serial_number: str
    expires_at: datetime


class PKIClient(ABC):
    """Base interface implemented by certificate authority providers."""

    async def __aenter__(self) -> Self:
        """Enter an async client lifetime."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release provider resources."""
        await self.close()

    @abstractmethod
    async def close(self) -> None:
        """Release provider resources, if any."""

    @abstractmethod
    async def issue_certificate(self, request: CertificateIssueRequest) -> IssuedCertificate:
        """Issue a new identity certificate for a managed device."""

    @abstractmethod
    async def get_ca_chain(self, source: str) -> tuple[str, ...]:
        """Return the CA chain associated with a logical certificate source."""
