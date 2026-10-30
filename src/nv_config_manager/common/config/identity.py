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

"""Shared deployment defaults for attribution identities."""

from configparser import ConfigParser

from nv_config_manager.common.config.loader import application_setting


def default_user_domain(config: ConfigParser | None = None) -> str | None:
    """Read the existing Temporal attribution domain for all service consumers."""
    configured = application_setting(
        "temporal", "user_domain", legacy_env="NV_CONFIG_MANAGER_DOMAIN", config=config
    )
    return (configured or "").strip() or None
