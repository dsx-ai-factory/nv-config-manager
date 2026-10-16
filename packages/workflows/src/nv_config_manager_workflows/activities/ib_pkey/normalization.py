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
"""Pure normalization helpers for InfiniBand DCIM activity contracts."""

DEFAULT_MEMBERSHIP_TYPE = "full"
_VALID_MEMBERSHIP_TYPES = frozenset({"full", "limited"})


def normalize_membership_type(membership_type: object) -> str:
    """Normalize membership to 'full'/'limited'.

    None or a blank string defaults to 'full'; any other type or value raises ValueError.
    """
    if membership_type is None:
        return DEFAULT_MEMBERSHIP_TYPE
    if not isinstance(membership_type, str):
        raise ValueError("membership_type must be 'full' or 'limited'")
    normalized = membership_type.strip().lower()
    if not normalized:
        return DEFAULT_MEMBERSHIP_TYPE
    if normalized not in _VALID_MEMBERSHIP_TYPES:
        raise ValueError("membership_type must be 'full' or 'limited'")
    return normalized


def _normalize_membership_override(value: object) -> str | None:
    """Normalize an optional per-port membership override; blank/None stays None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("membership must be 'full' or 'limited'")
    if not value.strip():
        return None
    return normalize_membership_type(value)
