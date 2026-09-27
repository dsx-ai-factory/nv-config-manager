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
"""Hello World Activities."""

from nv_config_manager.temporal.hello_world.activities.hello_world import (
    HELLO_WORLD_ACTIVITIES,
    hello_world_activity,
    hello_world_prompt_activity,
    hello_world_reject_activity,
)

REGISTERED_ACTIVITIES = list(HELLO_WORLD_ACTIVITIES)

__all__ = [
    "REGISTERED_ACTIVITIES",
    "hello_world_activity",
    "hello_world_prompt_activity",
    "hello_world_reject_activity",
]
