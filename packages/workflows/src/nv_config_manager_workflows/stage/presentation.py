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
"""Rendering and wire formats for stages and stage output."""

from __future__ import annotations

import base64
import gzip
import json
from collections.abc import Sequence
from typing import Any

from py_markdown_table.markdown_table import markdown_table
from pydantic import BaseModel

from nv_config_manager_workflows.stage.models import Stage


def format_row_for_markdown_table(row_data: dict[str, Any]) -> dict[str, Any]:
    """Format list-of-strings values as comma-separated for table display."""
    for key, value in list(row_data.items()):
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            row_data[key] = ", ".join(value) if value else ""
    return row_data


def render_markdown_table(
    rows: Sequence[BaseModel] | BaseModel, exclude: set[str] | None = None
) -> str:
    """Provide a markdown table output."""
    if isinstance(rows, BaseModel):
        row_list: list[BaseModel] = [rows]
    else:
        row_list = list(rows)
    if not row_list:
        return ""

    first_row = row_list[0]
    markdown_fields = getattr(first_row, "markdown_fields", None)

    table_data = []
    for row in row_list:
        row_data = row.model_dump(exclude=exclude, mode="json")
        if markdown_fields:
            row_data = {k: v for k, v in row_data.items() if k in markdown_fields}
        table_data.append(format_row_for_markdown_table(row_data))

    return str(
        markdown_table(table_data).set_params(quote=False, row_sep="markdown").get_markdown()
    )


def render_markdown_table_dict(rows: Sequence[Any] | Any) -> str:
    """Provide a tabular output for a dict."""
    row_list = list(rows) if isinstance(rows, Sequence) else [rows]
    return (
        str(markdown_table(row_list).set_params(quote=False, row_sep="markdown").get_markdown())
        if row_list
        else ""
    )


def compress_stages(stages: list[Stage]) -> str:
    """Compress stages into a base64 encoded gzipped JSON string.

    Args:
        stages: List of Stage objects to compress

    Returns:
        str: Base64 encoded gzipped JSON string
    """
    # Convert stages to JSON string.
    # mode="json" coerces bytes fields to lists of ints (Pydantic v2 default)
    # so that json.dumps never encounters a raw bytes object.
    stages_json = json.dumps([stage.model_dump(mode="json") for stage in stages])

    # Compress the JSON string using gzip
    compressed = gzip.compress(stages_json.encode("utf-8"))

    # Encode the compressed data in base64
    return base64.b64encode(compressed).decode("utf-8")


def decompress_stages(compressed_stages: str) -> list[Stage]:
    """Decompress stages from a base64 encoded gzipped JSON string.

    Args:
        compressed_stages: Base64 encoded gzipped JSON string

    Returns:
        list[Stage]: List of decompressed Stage objects
    """
    # Decode base64
    compressed_data = base64.b64decode(compressed_stages)

    # Decompress gzip
    json_str = gzip.decompress(compressed_data).decode("utf-8")

    # Parse JSON and convert to Stage objects
    return [Stage.model_validate(stage_dict) for stage_dict in json.loads(json_str)]
