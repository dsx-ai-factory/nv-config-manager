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
"""Standalone structured logging contracts."""

import logging

import pytest

from nv_config_manager_logging import LogCategory, get_logger


def test_logger_preserves_category_and_call_fields(caplog: pytest.LogCaptureFixture) -> None:
    """Package loggers attach category while merging per-call structured fields."""
    logger = get_logger("test.shared.logging", category=LogCategory.NATS)
    with caplog.at_level(logging.INFO, logger="test.shared.logging"):
        logger.info("connected", extra={"server": "nats.example.test"})

    record = caplog.records[-1]
    assert record.category == "nats"
    assert record.server == "nats.example.test"
