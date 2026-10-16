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
"""Simple reusable Hello World activity implementations."""

from temporalio import activity


@activity.defn
async def hello_world_activity(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"


@activity.defn
async def hello_world_prompt_activity() -> str:
    """Ask whether the caller would like to be greeted."""
    return "Would you like to be greeted?"


@activity.defn
async def hello_world_reject_activity() -> str:
    """Reject the greeting."""
    return "Goodbye!"
