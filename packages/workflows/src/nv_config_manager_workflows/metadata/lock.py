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
"""Declarative per-resource locking for workflows.

This module is safe to import inside the Temporal workflow sandbox: it declares
the lock specification and computes deterministic keys, but performs no I/O.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

_LOCK_KEY_PREFIX = "wf-lock"

LOCK_RENEW_BUFFER_SECONDS = 15


class WorkflowLockSpec(BaseModel):
    """Declare serialization on a resource derived from a workflow input."""

    key_fields: list[str] = Field(min_length=1)
    include_workflow_name: bool = False
    namespace: str | None = None
    ttl_seconds: int = 120
    renew_interval_seconds: int = 45
    wait_timeout_seconds: float = 30.0
    on_conflict: Literal["wait", "fail"] = "wait"

    @model_validator(mode="after")
    def _validate_intervals(self) -> WorkflowLockSpec:
        """Renewal must outpace expiry, or the lock lapses between renewals."""
        if self.ttl_seconds <= 0 or self.renew_interval_seconds <= 0:
            raise ValueError("ttl_seconds and renew_interval_seconds must be positive")
        if self.wait_timeout_seconds <= 0:
            raise ValueError("wait_timeout_seconds must be positive")
        if self.renew_interval_seconds >= self.ttl_seconds:
            raise ValueError("renew_interval_seconds must be less than ttl_seconds")
        if self.renew_interval_seconds + LOCK_RENEW_BUFFER_SECONDS >= self.ttl_seconds:
            raise ValueError(
                "renew_interval_seconds must leave the renewal buffer before ttl_seconds"
            )
        return self


def build_workflow_lock_key(
    spec: WorkflowLockSpec,
    *,
    workflow_name: str,
    namespace: str | None,
    workflow_input: BaseModel,
) -> str:
    """Build the distributed lock key for a run from its input."""
    parts = [_LOCK_KEY_PREFIX]

    scope_namespace = spec.namespace or namespace
    if scope_namespace:
        parts.append(scope_namespace)

    if spec.include_workflow_name:
        parts.append(workflow_name)

    for field in spec.key_fields:
        value = getattr(workflow_input, field, None)
        if value is None or value == "":
            raise ValueError(f"Workflow lock key field '{field}' is missing or empty on the input")
        parts.append(f"{field}={value}")

    return ":".join(parts)
