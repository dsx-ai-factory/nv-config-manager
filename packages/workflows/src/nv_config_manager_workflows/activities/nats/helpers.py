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
"""Reusable helpers for NATS activities."""

from nv_config_manager_workflows.runtime import NatsNotConfiguredError


def resolve_subject(requested_subject: str | None, configured_subject: str) -> str:
    """Return the requested subject or fall back to the configured default."""
    subject = requested_subject or configured_subject
    if not subject:
        raise NatsNotConfiguredError(
            "NATS subject is not configured and the activity input did not provide one"
        )
    return subject


__all__ = ["resolve_subject"]
