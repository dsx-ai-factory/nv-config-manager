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
"""Dependency-free form declarations safe to import in Temporal workflows."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FormOptionProvider:
    """An API-only option resolver and its validated query model.

    Both values use ``"module.path:attribute"`` references so Temporal workers
    can import the workflow class without importing API-only provider code.
    """

    resolver: str
    query_model: str


__all__ = ["FormOptionProvider"]
