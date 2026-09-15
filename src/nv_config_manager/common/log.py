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
"""NVIDIA Config Manager structured logging."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from numbers import Number
from typing import Any

from opentelemetry import trace
from pythonjsonlogger.json import JsonFormatter

# =============================================================================
# LOG CATEGORIES
# =============================================================================


class LogCategory:
    """Standard log category labels for structured logging."""

    RENDER = "render"
    RENDER_EVENT = "render.event"
    RENDER_API = "render.api"
    DHCP = "dhcp"
    DHCP_DATA = "dhcp.data"
    CONFIG_STORE = "config_store"
    CONFIG_STORE_API = "config_store.api"
    ZTP = "ztp"
    ZTP_API = "ztp.api"
    TEMPORAL = "temporal"
    TEMPORAL_WORKFLOW = "temporal.workflow"
    TEMPORAL_ACTIVITY = "temporal.activity"
    TEMPORAL_API = "temporal.api"
    TEMPORAL_AUDIT = "temporal.audit"
    DCIM = "dcim"
    NAUTOBOT = "nautobot"
    AUTH = "auth"
    API = "api"  # Deprecated: use per-service variants (RENDER_API, etc.)
    NATS = "nats"
    CACHE = "cache"


# =============================================================================
# INTERNALS
# =============================================================================

_logging_configured = False
_custom_labels: dict[str, str] = {}


class _FallbackStreamHandler(logging.StreamHandler):
    """Temporary handler used before process-wide logging is configured."""


_LOG_LINE_BREAK_ESCAPES = str.maketrans(
    {
        "\n": r"\n",
        "\r": r"\r",
        "\v": r"\v",
        "\f": r"\f",
        "\x1c": r"\x1c",
        "\x1d": r"\x1d",
        "\x1e": r"\x1e",
        "\x1b": r"\x1b",
        "\x85": r"\x85",
        "\u2028": r"\u2028",
        "\u2029": r"\u2029",
    }
)
_VALID_LABEL_KEY = re.compile(r"^\w+(\_\w+)?$")
_MAX_LABEL_VALUE_LEN = 63
_RESERVED_FIELDS = frozenset(
    {
        "message",
        "msg",
        "level",
        "levelname",
        "levelno",
        "name",
        "pathname",
        "filename",
        "module",
        "lineno",
        "funcName",
        "created",
        "asctime",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "process",
        "processName",
        "exc_info",
        "exc_text",
        "stack_info",
        "service",
        "category",
        "trace_id",
        "span_id",
    }
)


def _otel_trace_fields() -> dict[str, str]:
    """Return trace_id/span_id for the active span.

    Empty when no valid span is in context (e.g. observability disabled, or
    logging outside a workflow/request span).
    """
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return {}
    return {
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
    }


def _load_custom_labels() -> dict[str, str]:
    """Parse and validate NV_CONFIG_MANAGER_CUSTOM_LABELS env var.

    Keys must be valid Python/Prometheus identifiers (``[a-zA-Z_][a-zA-Z0-9_]*``),
    at most 63 characters, and must not collide with reserved LogRecord fields.
    Values are truncated to 63 characters to stay within the Kubernetes pod-label
    limit.  Invalid entries are skipped with a warning on stderr.
    """
    raw = os.getenv("NV_CONFIG_MANAGER_CUSTOM_LABELS", "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        print(  # noqa: T201
            f"WARNING: NV_CONFIG_MANAGER_CUSTOM_LABELS is not valid JSON, ignoring: {raw!r}",
        )
        return {}
    if not isinstance(parsed, dict):
        return {}

    labels: dict[str, str] = {}
    for k, v in parsed.items():
        key = str(k)
        if key in _RESERVED_FIELDS:
            print(f"WARNING: custom label key {key!r} is reserved, skipping")  # noqa: T201
            continue
        if not _VALID_LABEL_KEY.match(key) or len(key) > _MAX_LABEL_VALUE_LEN:
            print(  # noqa: T201
                f"WARNING: custom label key {key!r} is invalid "
                f"(must match {_VALID_LABEL_KEY.pattern} and be <= 63 chars), skipping",
            )
            continue
        value = str(v)[:_MAX_LABEL_VALUE_LEN]
        labels[key] = value
    return labels


def _get_log_level() -> int:
    """Resolve log level from LOG_LEVEL env var, falling back to DEBUG env var."""
    level_name = os.getenv("LOG_LEVEL", "").upper()
    if level_name:
        return getattr(logging, level_name, logging.INFO)
    return logging.DEBUG if os.getenv("DEBUG") else logging.INFO


def _use_json_format() -> bool:
    """Check LOG_FORMAT env var: 'text' for plain text, JSON otherwise."""
    return os.getenv("LOG_FORMAT", "json").lower() != "text"


def _build_formatter() -> logging.Formatter:
    """Build the appropriate formatter based on environment configuration."""
    if _use_json_format():
        format_str = "%(message)s%(levelname)s%(name)s%(asctime)s%(module)s%(lineno)d"
        return JsonFormatter(format_str)
    return logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")


# =============================================================================
# PUBLIC API
# =============================================================================


def escape_log_newlines(value: object) -> str:
    """Escape characters that could forge additional log entries."""
    return str(value).translate(_LOG_LINE_BREAK_ESCAPES)


def _escape_log_argument(value: object) -> object:
    """Escape unsafe characters while preserving numeric formatting types."""
    if isinstance(value, (str, BaseException)):
        return escape_log_newlines(value)
    if isinstance(value, list):
        return [_escape_log_argument(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_escape_log_argument(item) for item in value)
    if isinstance(value, dict):
        return {
            _escape_log_argument(key): _escape_log_argument(item) for key, item in value.items()
        }
    if isinstance(value, Number):
        return value
    return escape_log_newlines(value)


def _escape_log_arguments(
    args: tuple[object, ...] | Mapping[str, object],
) -> tuple[object, ...] | dict[str, object]:
    """Escape record arguments while preserving whether they are positional or named."""
    if isinstance(args, Mapping):
        return {escape_log_newlines(key): _escape_log_argument(item) for key, item in args.items()}
    return tuple(_escape_log_argument(arg) for arg in args)


class EscapingLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that escapes unsafe characters in messages and arguments."""

    def log(self, level: int, msg: object, *args: object, **kwargs: Any) -> None:
        """Delegate an enabled log call after sanitizing its message and arguments."""
        if self.isEnabledFor(level):
            msg, processed_kwargs = self.process(msg, kwargs)
            escaped_msg = escape_log_newlines(msg)
            escaped_args = tuple(_escape_log_argument(arg) for arg in args)
            self.logger.log(level, escaped_msg, *escaped_args, **processed_kwargs)


class EscapingFilter(logging.Filter):
    """Escape unsafe characters in records that bypass :class:`EscapingLoggerAdapter`.

    Libraries and packages outside this distribution -- notably
    ``nv_config_manager_workflows``, which logs signal-supplied stage names --
    use plain ``logging.getLogger()``, so their records reach the handler
    unsanitized. Escaping is idempotent: the translation table maps only control
    characters, so a record the adapter already escaped passes through unchanged.

    Sanitizing must preserve the message's structure. A mapping passed as the
    message -- ``logger.info({"event": "deploy"})`` -- is merged into the JSON
    output as top-level fields by the formatter, so :func:`_escape_log_argument`
    escapes it in place instead of stringifying it into one Python repr.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Sanitize the record in place and always keep it."""
        record.msg = _escape_log_argument(record.msg)
        if record.args:
            record.args = _escape_log_arguments(record.args)
        return True


def configure_logging(service: str | None = None) -> None:
    """Configure the root logger for the entire process.

    Call this once at service startup before any other logging calls.
    Sets up the root logger with JSON or text formatting so that all
    loggers (including third-party libraries) produce consistent output.

    Args:
        service: Service name to embed in every log record
                 (e.g., "render", "dhcp", "ztp").
    """
    global _logging_configured, _custom_labels  # noqa: PLW0603
    if _logging_configured:
        return
    _logging_configured = True

    _custom_labels = _load_custom_labels()

    root = logging.getLogger()
    root.setLevel(_get_log_level())

    for h in root.handlers[:]:
        root.removeHandler(h)

    # Modules can call get_logger() while the service entry point is still
    # importing. Remove only the temporary handlers installed by get_logger()
    # so those records do not also propagate to the newly configured root
    # handler and appear twice.
    for managed_logger in logging.Logger.manager.loggerDict.values():
        if not isinstance(managed_logger, logging.Logger):
            continue
        for existing_handler in managed_logger.handlers[:]:
            if isinstance(existing_handler, _FallbackStreamHandler):
                managed_logger.removeHandler(existing_handler)
                existing_handler.close()

    handler = logging.StreamHandler()
    handler.setFormatter(_build_formatter())
    handler.addFilter(EscapingFilter())
    root.addHandler(handler)

    old_factory = logging.getLogRecordFactory()

    def _record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = old_factory(*args, **kwargs)
        record.level = record.levelname.lower()  # type: ignore[attr-defined]
        if service:
            record.service = service  # type: ignore[attr-defined]
        for key, value in _custom_labels.items():
            setattr(record, key, value)
        for key, value in _otel_trace_fields().items():
            setattr(record, key, value)
        return record

    logging.setLogRecordFactory(_record_factory)


def get_logger(
    name: str,
    json_format: bool = True,
    category: str = "",
) -> EscapingLoggerAdapter:
    """Get a configured logger with optional category label.

    If ``configure_logging()`` has already been called (recommended), this
    simply returns a ``LoggerAdapter`` wrapping the named logger with a
    ``category`` extra field.  Otherwise it attaches a handler to the named
    logger directly for backward compatibility.

    Args:
        name: Logger name (typically ``__name__``)
        json_format: Ignored when ``configure_logging()`` has been called;
                     kept for backward compatibility.
        category: Default log category for this logger (see ``LogCategory``).

    Returns:
        A ``LoggerAdapter`` with a ``category`` extra field.
    """
    logger = logging.getLogger(name)

    if not _logging_configured and not logger.handlers:
        handler = _FallbackStreamHandler()
        handler.setFormatter(
            _build_formatter()
            if json_format
            else logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        handler.addFilter(EscapingFilter())
        logger.addHandler(handler)
        logger.setLevel(_get_log_level())

    return EscapingLoggerAdapter(
        logger,
        extra={"category": category},
        merge_extra=True,
    )
