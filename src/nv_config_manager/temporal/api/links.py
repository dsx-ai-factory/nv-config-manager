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
"""Temporal API link helpers."""

from __future__ import annotations

from configparser import ConfigParser
from urllib.parse import quote

from nv_config_manager.common.config_loader import load_config


def temporal_ui_workflow_href(workflow_id: str, config: ConfigParser | None = None) -> str:
    """Build the Temporal Web URL for a workflow execution."""
    if config is None:
        config = load_config()

    safe_workflow_id = quote(workflow_id, safe="")
    ui_url = config.get("temporal", "temporal_ui_url", fallback="").strip()
    if not ui_url:
        return ""

    namespace = config.get("temporal", "namespace", fallback="default").strip() or "default"
    safe_namespace = quote(namespace, safe="")
    return f"{_normalize_base_url(ui_url)}/namespaces/{safe_namespace}/workflows/{safe_workflow_id}"


def _normalize_base_url(url: str) -> str:
    base_url = url.rstrip("/")
    if base_url.startswith(("http://", "https://")):
        return base_url
    return f"https://{base_url}"
