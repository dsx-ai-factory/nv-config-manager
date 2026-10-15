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
"""Tests for safeguards applied before OpenAPI client generation."""

from pathlib import Path
from runpy import run_path

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "generate_openapi.py"
validate_unique_operation_ids = run_path(str(_SCRIPT))["validate_unique_operation_ids"]


def test_unique_operation_ids_are_accepted() -> None:
    validate_unique_operation_ids(
        {
            "paths": {
                "/one": {"get": {"operationId": "get_one"}},
                "/two": {"post": {"operationId": "post_two"}},
            }
        }
    )


def test_duplicate_operation_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="already used by GET /one"):
        validate_unique_operation_ids(
            {
                "paths": {
                    "/one": {"get": {"operationId": "shared"}},
                    "/two": {"post": {"operationId": "shared"}},
                }
            }
        )


def test_missing_operation_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="GET /one: missing operationId"):
        validate_unique_operation_ids({"paths": {"/one": {"get": {}}}})
