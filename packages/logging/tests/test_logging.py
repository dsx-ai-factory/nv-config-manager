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

import inspect
import json
import logging
from collections.abc import Generator

import pytest

import nv_config_manager_logging as logging_config
from nv_config_manager_logging import LogCategory, _load_custom_labels, get_logger


@pytest.fixture
def _restore_logging_configuration() -> Generator[None]:
    """Restore process-wide logging state changed by configure_logging()."""
    original_factory = logging.getLogRecordFactory()
    original_configured = logging_config._logging_configured
    original_handlers = logging.root.handlers[:]
    original_level = logging.root.level
    original_labels = logging_config._custom_labels
    yield
    logging.setLogRecordFactory(original_factory)
    logging_config._logging_configured = original_configured
    logging.root.handlers[:] = original_handlers
    logging.root.setLevel(original_level)
    logging_config._custom_labels = original_labels


def test_logger_preserves_category_and_call_fields(caplog: pytest.LogCaptureFixture) -> None:
    """Package loggers attach category while merging per-call structured fields."""
    logger = get_logger("test.shared.logging", category=LogCategory.NATS)
    with caplog.at_level(logging.INFO, logger="test.shared.logging"):
        logger.info("connected", extra={"server": "nats.example.test"})

    record = caplog.records[-1]
    assert record.category == "nats"
    assert record.server == "nats.example.test"


def test_logger_preserves_application_caller(caplog: pytest.LogCaptureFixture) -> None:
    """Adapter frames do not replace the application source location."""
    logger = get_logger("test.shared.logging.caller")
    with caplog.at_level(logging.INFO, logger="test.shared.logging.caller"):
        frame = inspect.currentframe()
        assert frame is not None
        expected_lineno = frame.f_lineno + 1
        logger.info("connected")

    record = caplog.records[-1]
    assert record.pathname == __file__
    assert record.module == "test_logging"
    assert record.funcName == "test_logger_preserves_application_caller"
    assert record.lineno == expected_lineno


def test_logger_preserves_explicit_stacklevel(caplog: pytest.LogCaptureFixture) -> None:
    """Adapter frames are additive to an application-supplied stack level."""
    logger = get_logger("test.shared.logging.stacklevel")

    def log_from_helper() -> None:
        logger.info("connected", stacklevel=2)

    with caplog.at_level(logging.INFO, logger="test.shared.logging.stacklevel"):
        frame = inspect.currentframe()
        assert frame is not None
        expected_lineno = frame.f_lineno + 1
        log_from_helper()

    record = caplog.records[-1]
    assert record.pathname == __file__
    assert record.module == "test_logging"
    assert record.funcName == "test_logger_preserves_explicit_stacklevel"
    assert record.lineno == expected_lineno


def test_custom_label_keys_use_ascii_identifier_syntax(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Custom label keys reject leading digits and Unicode word characters."""
    monkeypatch.setenv(
        "NV_CONFIG_MANAGER_CUSTOM_LABELS",
        '{"tenant_1": "valid", "_region": "valid", "1tenant": "invalid", "t\u00e9nant": "invalid"}',
    )

    assert _load_custom_labels() == {"tenant_1": "valid", "_region": "valid"}


def test_call_fields_take_precedence_over_custom_labels(
    monkeypatch: pytest.MonkeyPatch,
    _restore_logging_configuration: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Custom labels fill missing fields without replacing call-level extra."""
    monkeypatch.setenv(
        "NV_CONFIG_MANAGER_CUSTOM_LABELS",
        '{"server": "configured.example.test", "region": "us_east"}',
    )
    monkeypatch.setenv("LOG_FORMAT", "json")
    logging_config._logging_configured = False
    logging_config.configure_logging()

    logger = get_logger("test.shared.logging.custom-labels", category=LogCategory.NATS)
    # Other repository suites may configure the shared ``test.*`` hierarchy.
    # Make this test depend only on the root handler configured above.
    logger.logger.disabled = False
    logger.logger.propagate = True
    logger.logger.setLevel(logging.INFO)
    logger.info("connected", extra={"server": "call.example.test"})

    emitted = json.loads(capsys.readouterr().err)
    assert emitted["server"] == "call.example.test"
    assert emitted["region"] == "us_east"
