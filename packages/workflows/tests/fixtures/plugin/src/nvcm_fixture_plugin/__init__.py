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
"""Fixture workflow plugin that exercises the public plugin contract.

It is packaged like any external plugin, through its own distribution and
``nv_config_manager.workflows`` entry point, and uses only the public
``nv_config_manager_workflows`` modules.

This module imports nothing: the Temporal workflow sandbox re-imports it as the
parent of ``workflows``, so the descriptor lives in ``registration``.
"""
