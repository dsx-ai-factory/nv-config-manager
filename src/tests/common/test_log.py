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
"""Tests for nv_config_manager.common.log."""

import io
import json
import logging
from unittest import mock

import pytest
from pythonjsonlogger.json import JsonFormatter

from nv_config_manager.common import log
from nv_config_manager.common.log import EscapingFilter, escape_log_newlines, get_logger
from nv_config_manager_workflows.log import WORKFLOW_LOG_CATEGORY

# W3C trace-context example IDs.
_TRACE_ID = 0x4BF92F3577B34DA6A3CE929D0E0E4736
_SPAN_ID = 0x00F067AA0BA902B7
_TRACE_ID_HEX = "4bf92f3577b34da6a3ce929d0e0e4736"
_SPAN_ID_HEX = "00f067aa0ba902b7"


def _mock_span(*, trace_id: int, span_id: int, is_valid: bool):
    """Build a mock OTel span with the given span context."""
    span = mock.MagicMock()
    ctx = span.get_span_context.return_value
    ctx.is_valid = is_valid
    ctx.trace_id = trace_id
    ctx.span_id = span_id
    return span


class TestOtelTraceFields:
    """_otel_trace_fields() reads the active span context."""

    def test_valid_span_returns_zero_padded_hex(self):
        """A valid span yields 32-char trace_id and 16-char span_id."""
        span = _mock_span(trace_id=_TRACE_ID, span_id=_SPAN_ID, is_valid=True)
        with mock.patch("opentelemetry.trace.get_current_span", return_value=span):
            assert log._otel_trace_fields() == {
                "trace_id": _TRACE_ID_HEX,
                "span_id": _SPAN_ID_HEX,
            }

    def test_small_ids_are_left_padded(self):
        """Small integer IDs are padded to the full hex width."""
        span = _mock_span(trace_id=0x1, span_id=0x1, is_valid=True)
        with mock.patch("opentelemetry.trace.get_current_span", return_value=span):
            fields = log._otel_trace_fields()
        assert fields["trace_id"] == "0" * 31 + "1"
        assert fields["span_id"] == "0" * 15 + "1"

    def test_invalid_span_context_returns_empty(self):
        """No active/valid span yields no fields."""
        span = _mock_span(trace_id=0, span_id=0, is_valid=False)
        with mock.patch("opentelemetry.trace.get_current_span", return_value=span):
            assert log._otel_trace_fields() == {}


@pytest.fixture
def _restore_logging():
    """Restore global logging state mutated by configure_logging() after the test."""
    original_factory = logging.getLogRecordFactory()
    original_configured = log._logging_configured
    original_handlers = logging.root.handlers[:]
    original_level = logging.root.level
    original_labels = log._custom_labels
    yield
    logging.setLogRecordFactory(original_factory)
    log._logging_configured = original_configured
    logging.root.handlers[:] = original_handlers
    logging.root.setLevel(original_level)
    log._custom_labels = original_labels


class TestRecordFactoryIntegration:
    """configure_logging() wires trace fields onto every record."""

    def test_factory_stamps_trace_fields_when_span_active(self, _restore_logging):
        """Records carry trace_id/span_id when a valid span is in context."""
        log._logging_configured = False
        span = _mock_span(trace_id=_TRACE_ID, span_id=_SPAN_ID, is_valid=True)
        with mock.patch("opentelemetry.trace.get_current_span", return_value=span):
            log.configure_logging(service="test-svc")
            record = logging.getLogRecordFactory()("n", logging.INFO, "p", 1, "m", None, None)
        assert record.trace_id == _TRACE_ID_HEX
        assert record.span_id == _SPAN_ID_HEX
        assert record.service == "test-svc"

    def test_factory_omits_trace_fields_without_span(self, _restore_logging):
        """Records have no trace fields when no span is active."""
        log._logging_configured = False
        span = _mock_span(trace_id=0, span_id=0, is_valid=False)
        with mock.patch("opentelemetry.trace.get_current_span", return_value=span):
            log.configure_logging(service="test-svc")
            record = logging.getLogRecordFactory()("n", logging.INFO, "p", 1, "m", None, None)
        assert not hasattr(record, "trace_id")
        assert not hasattr(record, "span_id")


class UnsafeLogValue:
    """Value whose string representation contains unsafe log characters."""

    def __str__(self) -> str:
        return "before\nafter"


@pytest.mark.parametrize(
    ("separator", "escaped"),
    [
        ("\n", r"\n"),
        ("\r", r"\r"),
        ("\v", r"\v"),
        ("\f", r"\f"),
        ("\x1c", r"\x1c"),
        ("\x1d", r"\x1d"),
        ("\x1e", r"\x1e"),
        ("\x85", r"\x85"),
        ("\u2028", r"\u2028"),
        ("\u2029", r"\u2029"),
    ],
)
def test_escape_log_newlines_escapes_line_separators(separator: str, escaped: str) -> None:
    assert escape_log_newlines(f"before{separator}after") == f"before{escaped}after"


def test_escape_log_newlines_stringifies_objects() -> None:
    assert escape_log_newlines(ValueError("before\nafter")) == r"before\nafter"


def test_escape_log_newlines_escapes_terminal_escape_character() -> None:
    assert escape_log_newlines("before\x1b[31mafter") == r"before\x1b[31mafter"


def test_escape_log_newlines_preserves_text_without_separators() -> None:
    assert escape_log_newlines("unchanged") == "unchanged"


def test_logger_adapter_escapes_format_arguments(caplog: pytest.LogCaptureFixture) -> None:
    logger = get_logger("test.log-escaping")

    with caplog.at_level(logging.INFO, logger="test.log-escaping"):
        logger.info(
            "value=%s count=%d error=%s",
            "before\nafter",
            2,
            ValueError("bad\x1b[31mvalue"),
        )

    assert caplog.messages[-1] == r"value=before\nafter count=2 error=bad\x1b[31mvalue"


def test_logger_adapter_escapes_preformatted_message(caplog: pytest.LogCaptureFixture) -> None:
    logger = get_logger("test.log-escaping")
    unsafe_value = "before\nafter"

    with caplog.at_level(logging.INFO, logger="test.log-escaping"):
        logger.info(f"value={unsafe_value}")

    assert caplog.messages[-1] == r"value=before\nafter"


def test_logger_adapter_recursively_escapes_collection_arguments(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = get_logger("test.log-escaping")

    with caplog.at_level(logging.INFO, logger="test.log-escaping"):
        logger.info(
            "nested=%s",
            {"bad\n\x1bkey": [UnsafeLogValue(), ("bad\rvalue",)]},
        )

    assert caplog.messages[-1] == (
        r"nested={'bad\\n\\x1bkey': ['before\\nafter', ('bad\\rvalue',)]}"
    )


def test_workflow_package_category_matches_this_services_label() -> None:
    """The workflows package cannot import LogCategory, so it declares the label itself.

    Both definitions feed the same ``category`` field, so a rename on either side
    would silently split one dashboard filter into two.
    """
    assert WORKFLOW_LOG_CATEGORY == log.LogCategory.TEMPORAL_WORKFLOW


def _emit_through_escaping_filter(msg: object, *args: object) -> str:
    """Log one record through a handler carrying the filter, as configured services do."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("%(message)s"))
    handler.addFilter(EscapingFilter())

    logger = logging.getLogger("test.escaping-filter")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info(msg, *args)

    return stream.getvalue().strip()


def test_escaping_filter_escapes_plain_messages() -> None:
    """Packages using a stdlib logger get the same sanitizing as the adapter."""
    emitted = json.loads(_emit_through_escaping_filter("stage=%s", "render\nERROR forged"))

    assert emitted["message"] == r"stage=render\nERROR forged"


def test_escaping_filter_preserves_a_structured_message() -> None:
    """A mapping message is merged into JSON as fields, so it must not be stringified."""
    emitted = json.loads(_emit_through_escaping_filter({"event": "deploy", "device": "leaf01"}))

    assert emitted["event"] == "deploy"
    assert emitted["device"] == "leaf01"


def test_escaping_filter_escapes_inside_a_structured_message() -> None:
    """Keeping the structure must not let a value forge a log line."""
    emitted = json.loads(
        _emit_through_escaping_filter({"devices": [{"name": "leaf01\nERROR forged"}]})
    )

    assert emitted["devices"] == [{"name": r"leaf01\nERROR forged"}]


def test_escaping_filter_preserves_numeric_arguments() -> None:
    """Stringifying a number here would break %d formatting downstream."""
    emitted = json.loads(_emit_through_escaping_filter("count=%d", 42))

    assert emitted["message"] == "count=42"


def test_logger_adapter_merges_per_call_structured_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Structured audit fields are retained alongside the adapter category."""
    logger = get_logger("test.structured", category="temporal.audit")

    with caplog.at_level(logging.INFO, logger="test.structured"):
        logger.info("workflow action", extra={"action": "terminate", "actor": "operator"})

    record = caplog.records[-1]
    assert record.category == "temporal.audit"
    assert record.action == "terminate"
    assert record.actor == "operator"


def test_configure_logging_replaces_fallback_handler_without_duplicate_output(
    _restore_logging: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A logger created during imports emits once after root configuration."""
    logger_name = "test.preconfiguration-fallback"
    raw_logger = logging.getLogger(logger_name)
    original_handlers = raw_logger.handlers[:]
    original_level = raw_logger.level

    try:
        raw_logger.handlers.clear()
        log._logging_configured = False
        logger = get_logger(logger_name, category="temporal.audit")

        assert len(raw_logger.handlers) == 1

        log.configure_logging(service="test-svc")
        logger.info("unique workflow audit event")

        assert raw_logger.handlers == []
        assert capsys.readouterr().err.count("unique workflow audit event") == 1
    finally:
        raw_logger.handlers[:] = original_handlers
        raw_logger.setLevel(original_level)
