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
"""Records leaving this package carry the category the host filters on.

These tests capture through ``caplog``, which restores handlers and levels when
each test ends. Installing a handler directly would outlive the test, and on
``StageMixin.logger`` -- a real production logger, not a throwaway -- that would
silence stage logging for everything that ran afterwards.
"""

import logging

import pytest

from nv_config_manager_workflows.log import WORKFLOW_LOG_CATEGORY, get_workflow_logger
from nv_config_manager_workflows.stage import StageMixin

STAGE_LOGGER_NAME = "nv_config_manager_workflows.stage.mixin"


def test_records_carry_the_workflow_category(caplog: pytest.LogCaptureFixture) -> None:
    """Dashboards select on this field, so a plain logger would drop them."""
    logger = get_workflow_logger("test.category")

    with caplog.at_level(logging.INFO, logger="test.category"):
        logger.error("stage failed")

    assert caplog.records[-1].__dict__["category"] == WORKFLOW_LOG_CATEGORY


def test_per_call_fields_are_merged_with_the_category(caplog: pytest.LogCaptureFixture) -> None:
    """A call site adding structured fields must not displace the category."""
    logger = get_workflow_logger("test.merge")

    with caplog.at_level(logging.INFO, logger="test.merge"):
        logger.error("stage failed", extra={"stage": "render"})

    record = caplog.records[-1]
    assert record.__dict__["category"] == WORKFLOW_LOG_CATEGORY
    assert record.__dict__["stage"] == "render"


def test_the_logger_name_stays_the_calling_module(caplog: pytest.LogCaptureFixture) -> None:
    logger = get_workflow_logger("test.naming")

    with caplog.at_level(logging.INFO, logger="test.naming"):
        logger.error("stage failed")

    assert caplog.records[-1].name == "test.naming"


def test_the_stage_mixin_logs_under_the_workflow_category(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The signal handlers on StageMixin are the records this exists for."""
    with caplog.at_level(logging.INFO, logger=STAGE_LOGGER_NAME):
        StageMixin.logger.error("Received retry signal for non-existent stage: %s", "render")

    record = caplog.records[-1]
    assert record.__dict__["category"] == WORKFLOW_LOG_CATEGORY
    assert record.getMessage() == "Received retry signal for non-existent stage: render"


def test_the_package_installs_no_handlers_of_its_own() -> None:
    """A library leaves handler configuration to the host that embeds it.

    This also fails if a test in this suite leaks a handler onto the stage
    logger, which is the failure the ``caplog`` capture above avoids.
    """
    stage_logger = logging.getLogger(STAGE_LOGGER_NAME)

    assert stage_logger.handlers == []
    assert stage_logger.propagate is True
