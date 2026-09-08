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
"""Template plugin for the public SuperPOD demo topology."""

from pathlib import Path


def get_template_paths() -> list[Path]:
    """Return additional template search paths."""
    return [Path(__file__).parent / "templates"]


def get_custom_filters() -> dict:
    """Return custom Jinja2 filters."""
    return {}


def get_render_data_requirements() -> dict:
    """Return provider-neutral additional render-data requirements."""
    return {}
