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
"""The root of the workflow mixin hierarchy."""

from typing import Any

from pydantic import BaseModel


class BaseMixin:
    """Base Workflow mixin class.

    Subclasses override the `run` method with specific input/output types.
    Use `# type: ignore[override, ty:invalid-method-override]` on subclass `run` methods since we
    intentionally use more specific input types (covariant override).
    """

    async def run(self, workflow_input: BaseModel) -> Any:
        """Run the workflow."""
        ...
