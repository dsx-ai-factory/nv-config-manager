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
"""Reusable helpers for configuration backup activities."""


def resolve_user_domain(user_domain: str | None, default_user_domain: str) -> str:
    """Use the configured user domain only when one was not supplied."""
    return default_user_domain if user_domain is None else user_domain


def backup_filename(path: str) -> str:
    """Return the final component of a Config Store backup path."""
    return path.split("/")[-1]


def format_backup_markdown(url: str) -> str:
    """Format a link to a configuration backup for workflow output."""
    return f"[Configuration Backup]({url})"


__all__ = ["backup_filename", "format_backup_markdown", "resolve_user_domain"]
