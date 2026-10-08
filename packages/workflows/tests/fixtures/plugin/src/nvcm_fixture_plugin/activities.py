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
"""The fixture plugin's own activity."""

from temporalio import activity


@activity.defn(name="nvcm_fixture_echo")
async def echo(message: str) -> str:
    """Return the message tagged with the fixture plugin name, without I/O."""
    return f"nvcm-fixture: {message}"


FIXTURE_ACTIVITIES = (echo,)

__all__ = ["FIXTURE_ACTIVITIES", "echo"]
