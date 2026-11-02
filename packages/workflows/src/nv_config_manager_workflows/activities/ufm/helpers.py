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
"""Reusable helpers for UFM activities."""

import csv
from io import StringIO
from typing import Any


def _generate_ports_csv(ports: list[dict[str, Any]]) -> str:
    """Generate CSV string from port data.

    Args:
        ports: List of port dictionaries

    Returns:
        CSV formatted string
    """
    if not ports:
        return ""

    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "system_name",
            "port",
            "label",
            "description",
            "physical_state",
            "logical_state",
            "peer_node_name",
            "peer_port",
            "peer_node_description",
            "guid",
        ],
    )
    writer.writeheader()
    writer.writerows(ports)
    return output.getvalue()


__all__ = ["_generate_ports_csv"]
